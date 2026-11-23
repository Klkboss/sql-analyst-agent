"""Turn the benchmark CSVs into eval/results/summary.md and eval/results/failures.md.

Usage: python -m eval.report   (safe to run while the benchmark is still going)
"""
import json
from datetime import date
from pathlib import Path

import pandas as pd

from agent import config
from eval.bird import (BIRD, DEFAULT_RUNS, MODELS, PER_DIFFICULTY, PRED_TIMEOUT_S,
                       RESULTS, SEED)

DIFFS = ["simple", "moderate", "challenging"]
WHAT = {"a_names": "names only", "b_schema": "+ full schema", "c_retry": "+ self-correction",
        "c_zero": "+ fix 1 (zero aggregate)", "c_strict": "+ fix 2 (strict retry)",
        "c_both": "+ fix 1 + fix 2"}


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
             ("c_both+ev", "c_retry+ev")]
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
        f"seed {SEED}, ids in `bird_sample.json`. Ollama `num_ctx={config.OLLAMA_NUM_CTX}`, "
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

def _pick(results):
    """Yield (title, model, run, question_id, base_run): stories first, then failure types."""
    for model in MODELS:
        r = lambda run: results.get((model, run))  # noqa: E731
        stories = [
            ("Self-correction rescued it", "c_retry", "b_schema",
             lambda new, base: (new.ex == 1) & (base.ex == 0) & (pd.to_numeric(new.retries) > 0)),
            ("Fix 1 (zero aggregate) rescued it", "c_zero", "c_retry",
             lambda new, base: (new.ex == 1) & (base.ex == 0)),
            ("Fix 2 (strict retry) changed the outcome", "c_strict", "c_retry",
             lambda new, base: (new.ex != base.ex) & (pd.to_numeric(new.retries) > 0)),
            ("Evidence hint was needed", "b_schema+ev", "b_schema",
             lambda new, base: (new.ex == 1) & (base.ex == 0)),
        ]
        for title, new_run, base_run, cond in stories:
            new, base = r(new_run), r(base_run)
            if new is not None and base is not None:
                common = new.index.intersection(base.index)
                hits = common[cond(new.loc[common], base.loc[common]).to_numpy()]
                if len(hits):
                    yield title, model, new_run, hits[0], base_run
    for model in MODELS:
        df = results.get((model, "c_retry"))
        if df is not None:
            for reason in ["wrong_result", "bad_column", "empty_result", "format_only", "timeout", "syntax"]:
                hits = df.index[df.reason == reason]
                if len(hits):
                    yield f"Failure: {reason}", model, "c_retry", hits[0], None


def _example(i, title, model, run, qid, base_run, results) -> str:
    row = results[(model, run)].loc[qid]
    out = [f"## {i}. {title}", "",
           f"`q{qid}` · {row.db_id} · {row.difficulty} · {model} · run `{run}` · "
           f"{'PASS' if row.ex else 'FAIL'} ({row.reason or 'pass'})", "",
           f"**Question:** {row.question}", ""]
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


def write_failures(results, limit: int = 10) -> None:
    seen, examples = set(), []
    for title, model, run, qid, base_run in _pick(results):
        if qid in seen or len(examples) >= limit:
            continue  # each question at most once, so the examples stay varied
        seen.add(qid)
        examples.append(_example(len(examples) + 1, title, model, run, qid, base_run, results))
    header = ["# Interesting examples from the BIRD runs", "",
              "Picked automatically by `eval/report.py`: cases where a change helped or hurt, "
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
