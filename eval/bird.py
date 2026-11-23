"""BIRD dev benchmark: run the agent under several configs and record execution accuracy.

Usage:
  python -m eval.bird                                   # every default run, both models
  python -m eval.bird --models qwen3:4b-instruct-2507-q4_K_M --runs c_retry b_schema+ev
  python -m eval.bird --limit 10                        # quick pilot on 10 questions
  python -m eval.report                                 # build summary.md / failures.md

Resumable: each (model, run) appends one CSV row per question as soon as it finishes,
and on restart questions already in the CSV are skipped. Ctrl+C is safe.
"""
import argparse
import csv
import json
import logging
import random
import sys
import time
from pathlib import Path

from agent import config, llm
from agent.loop import run_agent
from agent.sandbox import _execute
from eval.metrics import ex_match, failure_reason, lenient_match

BIRD = Path("data/bird")
RESULTS = Path("eval/results")
SAMPLE_FILE = RESULTS / "bird_sample.json"
SEED = 42
PER_DIFFICULTY = {"simple": 80, "moderate": 80, "challenging": 40}
MODELS = ["qwen2.5-coder:7b", "qwen3:4b-instruct-2507-q4_K_M"]

# Every config sets every switch explicitly, so values in .env can't leak into the benchmark.
_BASE = dict(schema_detail="full", max_retries=3, zero_agg_suspicious=False, strict_retry=False,
             check_shape=False, check_fanout=False)
CONFIGS = {
    "a_names":  dict(_BASE, schema_detail="names", max_retries=0),  # baseline: names only
    "b_schema": dict(_BASE, max_retries=0),                          # + types, keys, sample rows
    "c_retry":  dict(_BASE),                                         # + self-correction (Phase 1)
    "c_zero":   dict(_BASE, zero_agg_suspicious=True),               # + fix 1
    "c_strict": dict(_BASE, strict_retry=True),                      # + fix 2
    "c_both":   dict(_BASE, zero_agg_suspicious=True, strict_retry=True),
    # The shipped defaults, run on the final code (keyword quoting, join paths in the
    # schema, SQL-shape check), so the README headline matches what ships.
    "final":    dict(_BASE, zero_agg_suspicious=True, check_shape=True),
    # One switch flipped from "final", to count what each switch fixes and breaks.
    "final_fanout":  dict(_BASE, zero_agg_suspicious=True, check_shape=True, check_fanout=True),
    "final_noshape": dict(_BASE, zero_agg_suspicious=True),
}
# A run is a config name, plus "+ev" to append BIRD's human-written evidence hint.
DEFAULT_RUNS = ["a_names", "b_schema", "c_retry", "c_zero", "c_strict", "c_both",
                "a_names+ev", "b_schema+ev", "c_retry+ev", "c_both+ev", "final", "final+ev",
                "final_fanout", "final_fanout+ev", "final_noshape", "final_noshape+ev"]

FIELDS = ["question_id", "db_id", "difficulty", "ex", "ex_lenient", "reason", "retries",
          "latency_ms", "select_ms", "tokens_per_sec", "prompt_tokens_est", "selected_tables",
          "ctx_overflow", "tokens", "error", "pred_rows", "pred_preview", "gold_preview",
          "pred_sql", "gold_sql", "question", "evidence", "attempts"]

# Benchmark limits: more generous than the CLI so slow-but-correct SQL on the big
# databases (european_football_2 is 600 MB) isn't scored as wrong.
PRED_TIMEOUT_S, GOLD_TIMEOUT_S, MAX_ROWS = 30, 120, 100_000
# Pinned so published numbers stay comparable, whatever OLLAMA_NUM_CTX the app uses.
BENCH_NUM_CTX = 4096


def db_path(q: dict) -> Path:
    return BIRD / "dev_databases" / q["db_id"] / f"{q['db_id']}.sqlite"


def load_sample() -> list[dict]:
    """The fixed 200-question sample. Saved to a file so it never drifts between runs."""
    dev = json.loads((BIRD / "dev.json").read_text(encoding="utf-8"))
    if not SAMPLE_FILE.exists():
        rng = random.Random(SEED)
        ids = []
        for difficulty, n in PER_DIFFICULTY.items():
            ids += rng.sample([q["question_id"] for q in dev if q["difficulty"] == difficulty], n)
        SAMPLE_FILE.parent.mkdir(parents=True, exist_ok=True)
        SAMPLE_FILE.write_text(json.dumps(sorted(ids)))
    by_id = {q["question_id"]: q for q in dev}
    sample = [by_id[i] for i in json.loads(SAMPLE_FILE.read_text())]
    # Same database back-to-back: the schema prefix of the prompt stays identical, so Ollama
    # reuses its cached KV state instead of re-reading thousands of schema tokens each time.
    return sorted(sample, key=lambda q: (q["db_id"], q["question_id"]))


def done_ids(csv_path: Path) -> set[int]:
    """Question ids already finished in this run. agent_error rows are retried."""
    if not csv_path.exists():
        return set()
    with open(csv_path, newline="", encoding="utf-8") as f:
        return {int(r["question_id"]) for r in csv.DictReader(f) if r["reason"] != "agent_error"}


def _preview(rows) -> str:
    return repr(rows[:3])[:200] if rows is not None else ""


def _billed() -> int:
    # Groq tokens counted against the free tier so far (cached prompt tokens don't count).
    return llm.usage["prompt"] + llm.usage["completion"] - llm.usage["cached"]


def run_one(q: dict, run: str, gold: list) -> dict:
    cfg_name, _, ev = run.partition("+")
    row = {"question_id": q["question_id"], "db_id": q["db_id"], "difficulty": q["difficulty"],
           "gold_sql": q["SQL"], "question": q["question"], "evidence": q["evidence"] if ev else "",
           "gold_preview": _preview(gold)}
    start, used = time.perf_counter(), _billed()
    try:
        r = run_agent(q["question"], db_path(q), evidence=q["evidence"] if ev else None, **CONFIGS[cfg_name])
    except llm.RateLimited:
        raise  # main() waits it out and redoes this question
    except Exception as e:  # LLM/HTTP failure (e.g. Ollama stopped): record it; resume retries it
        return {**row, "ex": 0, "ex_lenient": 0, "reason": "agent_error", "error": str(e)[:300],
                "latency_ms": round((time.perf_counter() - start) * 1000)}
    pred = r.result.rows if r.result else None
    ok = pred is not None and ex_match(pred, gold)
    ok_l = pred is not None and lenient_match(pred, gold)
    speeds = [a.tokens_per_sec for a in r.attempts if a.tokens_per_sec]
    return {**row,
            "ex": int(ok), "ex_lenient": int(ok_l), "reason": failure_reason(r, ok, ok_l),
            "retries": len(r.attempts) - 1,
            "latency_ms": round(r.select_ms + sum(a.latency_ms for a in r.attempts)),
            "select_ms": round(r.select_ms),
            "tokens_per_sec": round(sum(speeds) / len(speeds), 1) if speeds else "",
            "prompt_tokens_est": r.prompt_tokens_est,
            "selected_tables": ",".join(r.selected_tables or []),
            "ctx_overflow": int(r.ctx_overflow),
            "tokens": (_billed() - used) or "",
            "error": r.attempts[-1].error or "",
            "pred_rows": len(pred) if pred is not None else "",
            "pred_preview": _preview(pred),
            "pred_sql": r.sql or r.attempts[-1].sql,
            "attempts": json.dumps([{"sql": a.sql, "error": a.error} for a in r.attempts])}


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--models", nargs="+", default=MODELS)
    p.add_argument("--provider", choices=["ollama", "groq"], default="ollama",
                   help="groq: --models are Groq model ids, e.g. openai/gpt-oss-120b")
    p.add_argument("--first", type=int, help="only the first N questions, 40/40/20 by difficulty")
    p.add_argument("--runs", nargs="+", default=DEFAULT_RUNS)
    p.add_argument("--limit", type=int, help="only N questions, spread across databases (pilot)")
    args = p.parse_args()
    for run in args.runs:
        if run.partition("+")[0] not in CONFIGS or run.partition("+")[2] not in ("", "ev"):
            p.error(f"unknown run {run!r}; configs: {', '.join(CONFIGS)} (optionally +ev)")
    logging.basicConfig(level=logging.WARNING, format="  [%(name)s] %(message)s")

    # The same 4096-token budget on Groq too, so it sees exactly the prompts the 4B saw.
    config.LLM_PROVIDER = args.provider
    config.OLLAMA_NUM_CTX = config.CONTEXT_LIMIT = BENCH_NUM_CTX
    config.QUERY_TIMEOUT_S, config.MAX_ROWS = PRED_TIMEOUT_S, MAX_ROWS

    sample = load_sample()
    if args.limit:
        sample = sample[:: max(1, len(sample) // args.limit)][: args.limit]
    if args.first:  # the Groq check: the lowest question ids of each difficulty in the sample
        n = sum(PER_DIFFICULTY.values())
        keep = {d: sorted(q["question_id"] for q in sample if q["difficulty"] == d)[: args.first * k // n]
                for d, k in PER_DIFFICULTY.items()}
        sample = [q for q in sample if q["question_id"] in keep[q["difficulty"]]]

    print(f"Gold results for {len(sample)} questions ...")
    gold = {}
    for q in sample:
        try:
            gold[q["question_id"]] = _execute(db_path(q), q["SQL"], GOLD_TIMEOUT_S, 10**7).rows
        except Exception as e:  # a broken gold query can't score anything: leave it out
            print(f"  skipping q{q['question_id']}: gold SQL failed ({e})")
    sample = [q for q in sample if q["question_id"] in gold]

    for model in args.models:
        config.OLLAMA_MODEL = config.GROQ_MODEL = model
        for run in args.runs:
            out = RESULTS / model.replace(":", "_").replace("/", "_") / f"{run}.csv"
            out.parent.mkdir(parents=True, exist_ok=True)
            todo = [q for q in sample if q["question_id"] not in done_ids(out)]
            print(f"\n=== {model} | {run} | {len(sample) - len(todo)} done, {len(todo)} to go ===")
            new_file = not out.exists()
            if new_file:
                fields = FIELDS
            else:  # older CSVs predate some columns: keep appending in their own layout
                with open(out, newline="", encoding="utf-8") as f:
                    fields = next(csv.reader(f))
            with open(out, "a", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
                if new_file:
                    w.writeheader()
                for i, q in enumerate(todo, 1):
                    while True:
                        try:
                            row = run_one(q, run, gold[q["question_id"]])
                            break
                        except llm.RateLimited as e:  # daily cap: sleep until it resets, then go on
                            print(f"  {e}; sleeping until then", flush=True)
                            time.sleep(e.wait_s + 30)
                    w.writerow(row)
                    f.flush()  # every finished question is on disk, so Ctrl+C loses at most one
                    print(f"  {i:3}/{len(todo)} q{q['question_id']:<5} {q['difficulty']:11} "
                          f"{'PASS' if row['ex'] else 'FAIL'} retries={row.get('retries', '-')} "
                          f"{row['latency_ms'] / 1000:5.1f}s {row['reason']}")


if __name__ == "__main__":
    main()
