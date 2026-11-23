"""Turn the benchmark CSVs into eval/results/summary.md and eval/results/failures.md.

Usage: python -m eval.report   (safe to run while the benchmark is still going)
"""
import json
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from eval.bird import (BENCH_NUM_CTX, BIRD, DEFAULT_RUNS, MODELS, PER_DIFFICULTY, PRED_TIMEOUT_S,
                       RESULTS, SEED)

DIFFS = ["simple", "moderate", "challenging"]
WHAT = {"a_names": "names only", "b_schema": "+ full schema", "c_retry": "+ self-correction",
        "c_zero": "+ fix 1 (zero aggregate)", "c_strict": "+ fix 2 (strict retry)",
        "c_both": "+ fix 1 + fix 2", "final": "shipped defaults", "final_fanout": "shipped + fan-out check",
        "final_noshape": "shipped without SQL-shape check"}


def _slug(model: str) -> str:
    return model.replace(":", "_").replace("/", "_")


def load() -> dict[tuple[str, str], pd.DataFrame]:
    """(model, run) -> results, one row per question (last write wins, agent errors dropped)."""
    out = {}
    for model in MODELS:
        for run in DEFAULT_RUNS + sorted({p.stem for p in (RESULTS / _slug(model)).glob("*.csv")} - set(DEFAULT_RUNS)):
            path = RESULTS / _slug(model) / f"{run}.csv"
            if path.exists():
                df = pd.read_csv(path, keep_default_na=False)
                df = df[df.reason != "agent_error"].drop_duplicates("question_id", keep="last")
                if len(df):
                    out[(model, run)] = df.set_index("question_id")
    return out


def _weights() -> dict[str, float]:
    # BIRD dev's real difficulty mix, so "adjusted EX" is comparable to published dev numbers.
    dev = pd.read_json(BIRD / "dev.json")
    return (dev.difficulty.value_counts(normalize=True)).to_dict()


def _pct(x) -> str:
    return f"{100 * x:.1f}" if pd.notna(x) else "-"


def summary_table(results, model) -> str:
    w = _weights()
    rows = []
    for (m, run), df in results.items():
        if m != model:
            continue
        cfg, _, ev = run.partition("+")
        by_diff = df.groupby("difficulty").ex.mean()
        adjusted = sum(w[d] * by_diff.get(d, float("nan")) for d in DIFFS)
        num = lambda c: pd.to_numeric(df[c], errors="coerce")  # noqa: E731
        rows.append({
            "Run": f"`{run}`", "Adds": WHAT.get(cfg, cfg) + (" + evidence" if ev else ""),
            "N": len(df), "EX %": _pct(df.ex.mean()), "Adjusted EX %": _pct(adjusted),
            **{f"{d.title()} %": _pct(by_diff.get(d)) for d in DIFFS},
            "Lenient EX %": _pct(df.ex_lenient.mean()),
            "Avg retries": f"{num('retries').mean():.2f}",
            "Avg s/question": f"{num('latency_ms').mean() / 1000:.1f}",
            "tok/s": f"{num('tokens_per_sec').mean():.0f}",
            "Table selection %": _pct((df.selected_tables != "").mean()),
        })
    return pd.DataFrame(rows).to_markdown(index=False) if rows else "_no results yet_"


def paired_table(results, model) -> str:
    """Each fix vs the run it extends, question by question: how many it fixed and broke."""
    pairs = [("c_retry", "b_schema"), ("c_zero", "c_retry"), ("c_strict", "c_retry"),
             ("c_both", "c_retry"), ("b_schema+ev", "b_schema"), ("c_retry+ev", "c_retry"),
             ("c_both+ev", "c_retry+ev"), ("final", "c_zero"), ("final+ev", "c_retry+ev"),
             ("final_fanout", "final"), ("final_fanout+ev", "final+ev"),
             ("final", "final_noshape"), ("final+ev", "final_noshape+ev")]
    rows = []
    for new, base in pairs:
        if (model, new) in results and (model, base) in results:
            a, b = results[(model, base)].ex, results[(model, new)].ex
            common = a.index.intersection(b.index)
            a, b = a[common], b[common]
            rows.append({"Change": f"`{base}` → `{new}`", "N (both done)": len(common),
                         "Fixed": int(((a == 0) & (b == 1)).sum()), "Broke": int(((a == 1) & (b == 0)).sum()),
                         "Net EX points": f"{100 * (b.mean() - a.mean()):+.1f}"})
    return pd.DataFrame(rows).to_markdown(index=False) if rows else "_not enough runs yet_"


def reasons_table(results, model) -> str:
    cols = {run: df.reason.replace("", "pass").value_counts() for (m, run), df in results.items() if m == model}
    if not cols:
        return "_no results yet_"
    return pd.DataFrame(cols).fillna(0).astype(int).to_markdown()


def write_summary(results) -> None:
    n = sum(PER_DIFFICULTY.values())
    lines = [
        "# BIRD dev benchmark", "",
        f"Generated {date.today()} by `python -m eval.report`. "
        f"Sample: {n} questions ({' / '.join(f'{v} {k}' for k, v in PER_DIFFICULTY.items())}), "
        f"seed {SEED}, ids in `bird_sample.json`. Ollama `num_ctx={BENCH_NUM_CTX}` (pinned), "
        f"query timeout {PRED_TIMEOUT_S}s, temperature 0.", "",
        "- **EX** = execution accuracy, BIRD's official rule (same set of result rows).",
        "- **Adjusted EX** re-weights the per-difficulty scores to BIRD dev's real mix "
        "(925 / 464 / 145), so it is comparable to published dev numbers.",
        "- **Lenient EX** also ignores column order and rounding; the gap to EX is formatting-only misses.",
        "- `+ev` runs add BIRD's human-written evidence hint to the question.",
        "- **Table selection %**: questions whose full schema didn't fit the context, so the agent "
        "picked tables first.", ""]
    for model in MODELS:
        lines += [f"## {model}", "", summary_table(results, model), "",
                  "### Effect of each change (paired, same questions)", "", paired_table(results, model), "",
                  "### Failure reasons", "", reasons_table(results, model), ""]
    (RESULTS / "summary.md").write_text("\n".join(lines), encoding="utf-8")


# --- failures.md --------------------------------------------------------------------

# Hand-written notes for examples the picker selects (by question id), written after
# reading the attempts. Kept here so regenerating the report doesn't lose them.
NOTES = {
    158: "The keyword fix at work: `order` is now quoted. The error was a wrong alias (`d` is "
         "`disp`, which has no district_id). The model resent the identical query once; the "
         "'you sent this exact query before' note made it rewrite the join.",
    293: "`molecule.label` holds '+' / '-', not words. COUNT = 0 looks like a valid answer, so "
         "without SUSPECT_ZERO_AGG it would have been accepted; the warning sent the model to "
         "the sample rows, where it found '+'.",
    52: "Without the hint the model read 'total SAT score >= 1500' as the `NumGE1500` column (a "
        "count of test takers). 'Total = Read + Math + Write' is a definition no schema shows; "
        "the hint supplies it.",
    54: "With the fan-out rule in the prompt, the model joined `satscores` to a question that "
        "needs one table. This school has no SAT row, so the join returned 0 rows, and the "
        "model confirmed the empty result. Without the rule it wrote the one-table query.",
    668: "The shape check did what it was built for: show the value the ranking is based on. "
         "BIRD's rule compares exact columns, so the extra `BadgeCount` column scores as wrong. "
         "For a user, seeing 456 badges is better; on BIRD it cost 2 questions (q668, q1173).",
    962: "The shape check didn't fire here, so both runs sent the same prompt at temperature 0, "
         "yet one run used `driverStandings` and the other `results`. Differences like this are "
         "why ±1 point between runs is noise.",
    389: "BIRD's gold SQL returns card ids although the question asks for names. The agent's "
         "answer is right and scores as wrong in every run of every config: a labeling error "
         "in BIRD dev, not an agent failure.",
    28: "Two wrong choices in a wide schema: 'locally funded' became `\"Charter Funding Type\" = "
        "'Directly funded'` in frpm instead of `schools.FundingType = 'Locally funded'`, and "
        "`DOCType` (a description) instead of `DOC` (the code).",
    42: "The education type is `schools.EdOpsName`; the model looked in frpm instead. The NULL "
        "and 0-row warnings made it rewrite the filter, but never the table choice, and it "
        "finally confirmed the empty result.",
    228: "Right answer, but not rounded to the 4 decimals the question asked for. A miss under "
         "BIRD's rule; lenient EX accepts it.",
    1232: "`CAST(x)` without `AS type` is invalid SQLite. The error only says 'near \")\"', so "
          "the model kept rewriting the WHERE clause and never touched the CAST.",
}

FINAL_MODEL = "qwen3:4b-instruct-2507-q4_K_M"
SHAPE_MSG = "ranks rows by|subquery .SELECT"  # the two messages agent/sqlcheck.py sends


def _first_error(attempts: str) -> str:
    return json.loads(attempts)[0]["error"] or ""


def _pick(results):
    """Yield (title, model, run, question_id, base_run) from the final runs of the shipped
    model: stories first, then one example per failure type."""
    m = FINAL_MODEL
    retried = lambda new: pd.to_numeric(new.retries, errors="coerce").fillna(0) > 0  # noqa: E731
    stories = [
        ("Self-correction rescued an SQL error", "final", None,
         lambda new, base: (new.ex == 1) & retried(new)
         & new.attempts.map(_first_error).str.startswith("The query failed")),
        ("The zero-aggregate warning rescued it (SUSPECT_ZERO_AGG)", "final", None,
         lambda new, base: (new.ex == 1) & new.attempts.str.contains("single row of 0/NULL")),
        ("The evidence hint was needed", "final+ev", "final",
         lambda new, base: (new.ex == 1) & (base.ex == 0)),
        ("The fan-out prompt rule broke it (why CHECK_FANOUT is off)", "final_fanout", "final",
         lambda new, base: (new.ex == 0) & (base.ex == 1)),
        ("The SQL-shape check broke it (extra column)", "final", "final_noshape",
         lambda new, base: (new.ex == 0) & (base.ex == 1) & new.attempts.str.contains(SHAPE_MSG)),
        # Where the shape check didn't fire, these two runs sent identical prompts.
        ("Run noise: same prompt, different answer", "final+ev", "final_noshape+ev",
         lambda new, base: (new.ex != base.ex) & ~new.attempts.str.contains(SHAPE_MSG)),
        ("Label noise: the gold answer is wrong", "final", None,
         lambda new, base: new.index == 389),
    ]
    for title, new_run, base_run, cond in stories:
        new, base = results.get((m, new_run)), results.get((m, base_run)) if base_run else None
        if new is None or (base_run and base is None):
            continue
        common = new.index if base is None else new.index.intersection(base.index)
        b = None if base is None else base.loc[common]
        hits = common[np.asarray(cond(new.loc[common], b))]
        if len(hits):
            yield title, m, new_run, hits[0], base_run
    df = results.get((m, "final"))
    if df is not None:
        for reason in ["wrong_result", "bad_column", "empty_result", "format_only", "timeout",
                       "syntax", "context_overflow"]:
            hits = df.index[df.reason == reason]
            if len(hits):
                yield f"Failure: {reason}", m, "final", hits[0], None


def _example(i, title, model, run, qid, base_run, results) -> str:
    row = results[(model, run)].loc[qid]
    out = [f"## {i}. {title}", "",
           f"`q{qid}` · {row.db_id} · {row.difficulty} · {model} · run `{run}` · "
           f"{'PASS' if row.ex else 'FAIL'} ({row.reason or 'pass'})", "",
           f"**Question:** {row.question}", ""]
    if qid in NOTES:
        out += [f"> **Why it matters:** {NOTES[qid]}", ""]
    if row.evidence:
        out += [f"**Evidence (shown to the model):** {row.evidence}", ""]
    if base_run:
        base = results[(model, base_run)].loc[qid]
        out += [f"**Before (`{base_run}`, {'PASS' if base.ex else 'FAIL'}):**",
                "```sql", base.pred_sql, "```", ""]
    out += ["**Attempts:**", ""]
    for n, a in enumerate(json.loads(row.attempts), 1):
        out += [f"{n}. `{' '.join(a['sql'].split())}`", f"   → {a['error'] or 'accepted'}"]
    out += ["", "**Gold SQL:**", "```sql", row.gold_sql, "```",
            f"**Gold rows:** `{row.gold_preview}`  ", f"**Agent rows:** `{row.pred_preview}`", ""]
    return "\n".join(out)


def write_failures(results, limit: int = 12) -> None:
    seen, examples = set(), []
    for title, model, run, qid, base_run in _pick(results):
        if qid in seen or len(examples) >= limit:
            continue  # each question at most once, so the examples stay varied
        seen.add(qid)
        examples.append(_example(len(examples) + 1, title, model, run, qid, base_run, results))
    header = ["# Interesting examples from the BIRD runs", "",
              "Picked automatically by `eval/report.py` from the final runs of the shipped model "
              "(qwen3:4b): cases where a switch helped or hurt, noise in the benchmark, "
              "then one example per failure type.", ""]
    (RESULTS / "failures.md").write_text("\n".join(header + examples), encoding="utf-8")


def main() -> None:
    results = load()
    write_summary(results)
    write_failures(results)
    print(f"Wrote {RESULTS / 'summary.md'} and {RESULTS / 'failures.md'} "
          f"from {len(results)} runs: {', '.join(f'{m}/{r} ({len(d)})' for (m, r), d in results.items())}")


if __name__ == "__main__":
    main()
