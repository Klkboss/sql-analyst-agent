"""Phase 1 check: 10 Chinook questions, easy -> multi-join, scored by execution accuracy.

A question passes when the agent's result has the same rows as the hand-written gold
query's result. Row order and column order are ignored, and numbers are compared to
2 decimals - the same idea as the execution-accuracy metric used by Spider/BIRD.

Usage: python eval_chinook.py
"""
import sys
import time
from collections import Counter

from tabulate import tabulate

from agent import config
from agent.loop import run_agent
from agent.sandbox import run_query

DB = "data/chinook.sqlite"

# (difficulty, question, gold SQL)
CASES = [
    ("easy", "How many customers are there?",
     "SELECT COUNT(*) FROM Customer"),
    ("easy", "How many tracks are longer than 5 minutes?",
     "SELECT COUNT(*) FROM Track WHERE Milliseconds > 300000"),
    ("easy", "List the distinct countries our customers come from.",
     "SELECT DISTINCT Country FROM Customer"),
    ("easy", "What is the total amount of all invoices combined?",
     "SELECT SUM(Total) FROM Invoice"),
    ("1 join", "How many albums does the artist AC/DC have?",
     "SELECT COUNT(*) FROM Album JOIN Artist USING (ArtistId) WHERE Artist.Name = 'AC/DC'"),
    ("1 join", "How many tracks are in the Rock genre?",
     "SELECT COUNT(*) FROM Track JOIN Genre USING (GenreId) WHERE Genre.Name = 'Rock'"),
    ("join+agg", "Which 5 customers spent the most in total? Show first name, last name and total spent.",
     "SELECT c.FirstName, c.LastName, SUM(i.Total) FROM Customer c JOIN Invoice i USING (CustomerId) "
     "GROUP BY c.CustomerId ORDER BY 3 DESC LIMIT 5"),
    ("2 joins", "Which 3 genres earned the most revenue (unit price times quantity)? Show genre name and revenue.",
     "SELECT g.Name, SUM(il.UnitPrice * il.Quantity) FROM InvoiceLine il JOIN Track t USING (TrackId) "
     "JOIN Genre g USING (GenreId) GROUP BY g.GenreId ORDER BY 2 DESC LIMIT 3"),
    ("2 joins", "Which employee has the highest total invoice sales from the customers they support? "
                "Show first name and last name.",
     "SELECT e.FirstName, e.LastName FROM Employee e JOIN Customer c ON c.SupportRepId = e.EmployeeId "
     "JOIN Invoice i USING (CustomerId) GROUP BY e.EmployeeId ORDER BY SUM(i.Total) DESC LIMIT 1"),
    ("3 joins", "Which artist has sold the most tracks by quantity? Show artist name and quantity sold.",
     "SELECT ar.Name, SUM(il.Quantity) FROM InvoiceLine il JOIN Track t USING (TrackId) "
     "JOIN Album al USING (AlbumId) JOIN Artist ar ON ar.ArtistId = al.ArtistId "
     "GROUP BY ar.ArtistId ORDER BY 2 DESC LIMIT 1"),
]


def _norm(rows) -> Counter:
    """Order-insensitive fingerprint of a result: rows as sorted value tuples, numbers to 2 dp."""
    def val(v):
        return f"{v:.2f}" if isinstance(v, (int, float)) else str(v)
    return Counter(tuple(sorted(val(v) for v in row)) for row in rows)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    model = config.OLLAMA_MODEL if config.LLM_PROVIDER == "ollama" else config.GROQ_MODEL
    print(f"Model: {config.LLM_PROVIDER} / {model}\n")
    table, passed, started = [], 0, time.perf_counter()

    for i, (level, question, gold_sql) in enumerate(CASES, 1):
        gold = run_query(DB, gold_sql).rows
        r = run_agent(question, DB)
        ok = r.result is not None and _norm(r.result.rows) == _norm(gold)
        passed += ok
        speeds = [a.tokens_per_sec for a in r.attempts if a.tokens_per_sec]
        table.append((i, level, "PASS" if ok else "FAIL", len(r.attempts) - 1,
                      f"{sum(a.latency_ms for a in r.attempts) / 1000:.1f}",
                      f"{sum(speeds) / len(speeds):.1f}" if speeds else "-", question[:60]))
        print(f"[{i}/10] {'PASS' if ok else 'FAIL'}  {question}\n        SQL: {' '.join((r.sql or '').split())}")
        if not ok:
            print(f"        got:  {r.result.rows[:5] if r.result else r.attempts[-1].error}\n"
                  f"        want: {gold[:5]}")

    print("\n" + tabulate(table, headers=["#", "level", "result", "retries", "sec", "tok/s", "question"]))
    print(f"\n{passed}/{len(CASES)} passed in {time.perf_counter() - started:.0f}s")


if __name__ == "__main__":
    main()
