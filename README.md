# LLM Data Analyst Agent

Ask a question in plain English about a SQLite database; the agent writes a
SELECT query with an LLM, runs it in a read-only sandbox, fixes its own
mistakes (up to 3 retries), and returns the table, the SQL and a short
explanation.

Status: Phase 1 (core agent + CLI) in progress.

## Setup

```bash
py -3.11 -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt
copy .env.example .env          # then edit if needed
python data/download_chinook.py
```
