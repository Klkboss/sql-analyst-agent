# LLM Data Analyst Agent

Ask a question in plain English about a SQLite database; the agent writes a
SELECT query with an LLM, runs it in a read-only sandbox, fixes its own
mistakes (up to 3 retries), and returns the table, the SQL and a short
explanation. Runs locally on Ollama; Groq is an optional cloud provider.

Status: Phase 1 (core agent + CLI) done.

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
pytest                          # unit tests, no LLM needed
```

## How it works

```
question ─► schema.py ─► generator.py ─► llm.py ─► sandbox.py ─► result OK? ─► explain.py
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

Chinook is easy for these models; the real test is BIRD/Spider in Phase 2.
