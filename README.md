# LLM Data Analyst Agent

Ask a question in plain English about a SQLite database; the agent writes a
SELECT query with an LLM, runs it in a read-only sandbox, fixes its own
mistakes (up to 3 retries), and returns the table, the SQL and a short
explanation. Runs locally on Ollama; Groq is an optional cloud provider.

Status: Phase 1 (core agent + CLI) and Phase 2 (BIRD benchmark) done.

## Setup

```bash
py -3.11 -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt
copy .env.example .env          # then edit if needed
python data/download_chinook.py
ollama pull qwen2.5-coder:7b
```

## Usage

```bash
python cli.py "Which 5 customers spent the most in total?"
python eval_chinook.py          # 10-question accuracy check
python data/download_bird.py    # BIRD dev set: 346 MB download, ~1.5 GB on disk
python -m eval.bird             # BIRD benchmark (resumable; --limit 10 for a pilot)
python -m eval.report           # -> eval/results/summary.md, failures.md
pytest                          # unit tests, no LLM needed
```

## How it works

```
question ─► schema.py ─► (too big for context? pick tables first) ─► generator.py ─► llm.py ─► sandbox.py ─► result OK? ─► explain.py
            (tables,      (prompt +       (Ollama    (guard +        │ no: error / 0 rows / all NULL
             types, FKs,   extract SQL)    or Groq)   read-only DB)   └─► feed SQL + problem back (≤3 retries)
             3 samples)
```

| File | Role |
|---|---|
| `agent/config.py` | All settings, read from `.env` |
| `agent/llm.py` | `chat(messages)` for Ollama and Groq over plain HTTP; logs latency and tokens/sec |
| `agent/schema.py` | Schema as prompt text: columns, types, keys, 3 sample rows (long values cut to 50 chars) |
| `agent/generator.py` | System prompt, retry messages, SQL extraction from ```sql blocks |
| `agent/sandbox.py` | SQL guard + locked-down execution |
| `agent/loop.py` | Generate → run → check → retry; records every attempt |
| `agent/explain.py` | 1–2 sentence answer from the result |

### Sandbox: four layers

The LLM's SQL is untrusted input, so no single check is relied on:
1. **Text guard** – one statement, must start with SELECT/WITH, no write keywords
   (string literals and comments are blanked first so they can't fool it).
2. **`mode=ro` URI** – file opened read-only; a wrong path errors instead of creating an empty DB.
3. **`PRAGMA query_only`** – SQLite refuses writes on the connection.
4. **Authorizer callback** – SQLite asks before every operation; only reads are allowed.
   This is the real guarantee (tests run writes with the guard skipped to prove it).

Limits: 10 s via a progress handler (`connect(timeout=)` only covers lock waits),
1000 rows via `fetchmany(1001)` so truncation is detected without rewriting the SQL.

## Phase 1 results (Chinook, 10 questions)

Execution accuracy: a question passes when the result rows match a gold query
(row/column order ignored, numbers to 2 decimals). RTX 4050 Laptop, 6 GB VRAM.

| Model | Passed | Retries used | Total time | Speed |
|---|---|---|---|---|
| qwen2.5-coder:7b | **10/10** | 0 | 35 s | ~29 tok/s |
| qwen3:4b-instruct-2507-q4_K_M | **10/10** | 0 | 36 s | ~53 tok/s (100% GPU) |

| # | Level | Question |
|---|---|---|
| 1–4 | easy | counts, filters, DISTINCT, SUM on one table |
| 5–6 | 1 join | albums by AC/DC, tracks in Rock |
| 7 | join + agg | top 5 customers by spend |
| 8–9 | 2 joins | top 3 genres by revenue, top support rep by sales |
| 10 | 3 joins | best-selling artist by quantity |

Chinook is easy for these models; the real test is BIRD below.

## Phase 2 results (BIRD dev, 200 questions)

200 questions from BIRD dev (80 simple / 80 moderate / 40 challenging, seed 42),
scored with BIRD's official execution-accuracy rule. **Adjusted EX** re-weights the
difficulty scores to BIRD dev's real mix (925 / 464 / 145) so it is comparable to
published dev numbers. Ollama `num_ctx=4096`, temperature 0. Full tables, failure
reasons and paired fixed/broke counts: [eval/results/summary.md](eval/results/summary.md);
annotated examples: [eval/results/failures.md](eval/results/failures.md).

| Run | Adds | qwen2.5-coder:7b EX / adjusted | qwen3:4b EX / adjusted |
|---|---|---|---|
| `a_names` | table + column names only | 25.5 / 28.8 | 21.0 / 25.1 |
| `b_schema` | + types, keys, sample rows | 34.5 / 38.3 | 36.0 / 38.3 |
| `c_retry` | + self-correction | 35.5 / 39.3 | 40.0 / 42.5 |
| `c_both` | + fix 1 + fix 2 | 35.5 / 39.3 | 40.5 / 43.3 |
| `a_names+ev` | names + evidence hint | 47.5 / 50.7 | 44.0 / 46.5 |
| `b_schema+ev` | full schema + evidence | 49.5 / 53.0 | 52.5 / 54.8 |
| `c_retry+ev` | + self-correction + evidence | 51.5 / 55.5 | **56.0 / 58.5** |
| `c_both+ev` | + both fixes + evidence | 51.5 / **56.0** | 55.5 / 58.3 |

Best run by difficulty (`c_retry+ev`), simple / moderate / challenging:
qwen2.5-coder:7b 62.5 / 46.2 / 40.0; qwen3:4b 63.7 / 50.0 / 52.5.
Speed: ~30 tok/s for the 7B (84% GPU, 16% CPU) vs ~52 tok/s for the 4B (100% GPU);
5–7 s per question with self-correction.

What the numbers say:
- **Evidence hints are the biggest lever (+15–17 points).** Many BIRD questions hinge on
  definitions ("eligible free rate = free meals / enrollment") no schema can reveal.
- **Schema context is next (+9 / +15 points without hints).** Types and sample rows
  show the model real values and formats.
- **Self-correction helps a little, and never hurt:** +1 (7B) / +4 (4B) points, 0 previously
  correct answers broken. The 4B uses retries better: it cleared every `no such column`
  error, while the 7B resubmitted the same failing SQL in 7 of its 42 retried questions.
- **The smaller model wins.** qwen3:4b beats qwen2.5-coder:7b in every run that gets the full schema,
  and runs ~1.7× faster because it fits entirely in 6 GB of VRAM.
- **The two Phase 1 fixes are within noise on BIRD.** Fix 1 (single 0/NULL row is
  suspicious) fixed 2 and broke 0 on the 4B, 0/0 on the 7B. Fix 2 (strict retry prompt)
  broke 1 and fixed 0 on the 4B (0/0 on the 7B): the model read "return the same SQL" as permission to keep a wrong
  value. Even at temperature 0, identical prompts gave different SQL on up to 4 of 200
  questions between runs, so ±1 point differences are not meaningful.
- **Table selection** ran on 22–24% of questions (card_games, european_football_2 and a
  few california_schools). It works (74% EX on those questions when the result fits, 7B `c_retry+ev`), but
  5 questions per run picked very wide tables and still overflowed 4096 tokens.
- **Lenient EX is within 0.5 points of EX**, so the misses are real, not formatting.
