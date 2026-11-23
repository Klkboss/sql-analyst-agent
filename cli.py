"""Ask the agent a question from the terminal.

Usage: python cli.py "Which 5 customers spent the most?" [--db data/chinook.sqlite]
"""
import argparse
import logging
import sys

from tabulate import tabulate

from agent import config
from agent.explain import explain
from agent.loop import run_agent

SHOW_ROWS = 20  # print at most this many rows; the full result is still in memory


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")  # Chinook has names like 'Luís'; Windows console defaults to cp1252
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("question")
    p.add_argument("--db", default="data/chinook.sqlite")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="  [%(name)s] %(message)s")

    model = config.OLLAMA_MODEL if config.LLM_PROVIDER == "ollama" else config.GROQ_MODEL
    print(f"Model: {config.LLM_PROVIDER} / {model}\n")
    r = run_agent(args.question, args.db)

    print("\nAttempts:")
    print(tabulate(
        [(i, f"{a.latency_ms:.0f}", f"{a.tokens_per_sec:.1f}" if a.tokens_per_sec else "-",
          (a.error or "OK")[:70]) for i, a in enumerate(r.attempts, 1)],
        headers=["#", "ms", "tok/s", "status"]))
    print(f"Retries used: {len(r.attempts) - 1}")

    if r.selected_tables:
        print(f"Schema too big for the context window; used tables: {', '.join(r.selected_tables)}")
    print(f"\nSQL:\n{r.sql or r.attempts[-1].sql}\n")  # sql is None if nothing executed
    if r.result is None:
        print("No query succeeded.")
        sys.exit(1)

    res = r.result
    print(tabulate(res.rows[:SHOW_ROWS], headers=res.columns))
    if len(res.rows) > SHOW_ROWS or res.truncated:
        print(f"... showing {SHOW_ROWS} of {len(res.rows)}{'+' if res.truncated else ''} rows")
    print(f"\nAnswer: {explain(args.question, r.sql, res)}")


if __name__ == "__main__":
    main()
