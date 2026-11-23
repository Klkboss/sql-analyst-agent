# Interesting examples from the BIRD runs

Picked automatically by `eval/report.py`: cases where a change helped or hurt, then one example per failure type.

## 1. Self-correction rescued it

`q164` · financial · simple · qwen2.5-coder:7b · run `c_retry` · PASS (pass)

**Question:** Who placed the order with the id 32423?

> **Why it matters:** `order` is an SQL keyword, so the table name must be quoted. The syntax error was enough for the model to fix it on the first retry.

**Before (`b_schema`, FAIL):**
```sql
SELECT client_id FROM disp WHERE account_id = (SELECT account_id FROM order WHERE order_id = 32423)
```

**Attempts:**

1. `SELECT client_id FROM disp WHERE account_id = (SELECT account_id FROM order WHERE order_id = 32423)`
   → The query failed with error: near "order": syntax error
2. `SELECT client_id FROM disp WHERE account_id = (SELECT account_id FROM `order` WHERE order_id = 32423)`
   → accepted

**Gold SQL:**
```sql
SELECT T3.client_id FROM `order` AS T1 INNER JOIN account AS T2 ON T1.account_id = T2.account_id INNER JOIN disp AS T4 ON T4.account_id = T2.account_id  INNER JOIN client AS T3 ON T4.client_id = T3.client_id WHERE T1.order_id = 32423
```
**Gold rows:** `[(2497,)]`  
**Agent rows:** `[(2497,)]`

## 2. Evidence hint was needed

`q52` · california_schools · simple · qwen2.5-coder:7b · run `b_schema+ev` · PASS (pass)

**Question:** What is the total number of schools whose total SAT scores are greater or equal to 1500 whose mailing city is Lakeport?

> **Why it matters:** A lucky pass: the model ignored the hint (it used `NumGE1500 >= 1` instead of summing the three scores) and still got the right count. EX only checks the result, so small answers like a count can reward wrong SQL.

**Evidence (shown to the model):** Total SAT scores can be computed by AvgScrRead + AvgScrMath + AvgScrWrite

**Before (`b_schema`, FAIL):**
```sql
SELECT COUNT(*)
FROM schools
JOIN satscores ON schools.CDSCode = satscores.cds
WHERE satscores.NumGE1500 >= 1500 AND schools.MailCity = 'Lakeport';
```

**Attempts:**

1. `SELECT COUNT(*) FROM satscores AS T1 JOIN schools AS T2 ON T1.cds = T2.CDSCode WHERE T1.NumGE1500 >= 1 AND T2.MailCity = 'Lakeport';`
   → accepted

**Gold SQL:**
```sql
SELECT COUNT(T1.cds) FROM satscores AS T1 INNER JOIN schools AS T2 ON T1.cds = T2.CDSCode WHERE T2.MailCity = 'Lakeport' AND (T1.AvgScrRead + T1.AvgScrMath + T1.AvgScrWrite) >= 1500
```
**Gold rows:** `[(2,)]`  
**Agent rows:** `[(2,)]`

## 3. Self-correction rescued it

`q379` · card_games · simple · qwen3:4b-instruct-2507-q4_K_M · run `c_retry` · PASS (pass)

**Question:** What are the cards belong to duel deck a? List the ID.

> **Why it matters:** The 0-row check working as designed: 'Duel Deck A' is not a set name, the feedback pointed at the sample rows, and the model found `cards.duelDeck = 'a'`.

**Before (`b_schema`, FAIL):**
```sql
SELECT c.id
FROM cards c
JOIN sets s ON c.setCode = s.code
WHERE s.name = 'Duel Deck A';
```

**Attempts:**

1. `SELECT c.id FROM cards c JOIN sets s ON c.setCode = s.code WHERE s.name = 'Duel Deck A';`
   → The query ran but returned 0 rows. Check WHERE values against the sample rows (spelling, case, format) and the join conditions. If 0 rows is truly the correct answer, return the same query unchanged.
2. `SELECT c.id FROM cards c WHERE c.duelDeck = 'a';`
   → accepted

**Gold SQL:**
```sql
SELECT id FROM cards WHERE duelDeck = 'a'
```
**Gold rows:** `[(14440,), (14441,), (14442,)]`  
**Agent rows:** `[(14440,), (14441,), (14442,)]`

## 4. Fix 1 (zero aggregate) rescued it

`q190` · financial · simple · qwen3:4b-instruct-2507-q4_K_M · run `c_zero` · PASS (pass)

**Question:** How many clients who were born in 1920 stay in east Bohemia?

> **Why it matters:** Fix 1 working as designed: COUNT = 0 came from filtering the wrong column (`A2` is the district name, `A3` is the region). Phase 1's 0-rows check would have accepted the 0.

**Before (`c_retry`, FAIL):**
```sql
SELECT COUNT(*) 
FROM client 
JOIN district ON client.district_id = district.district_id 
WHERE client.birth_date LIKE '1920%' 
  AND district.A2 LIKE 'east Bohemia%';
```

**Attempts:**

1. `SELECT COUNT(*) FROM client JOIN district ON client.district_id = district.district_id WHERE client.birth_date LIKE '1920%' AND district.A2 LIKE 'east Bohemia%';`
   → The query returned a single row of 0/NULL, which often means a filter matched nothing (e.g. a misspelled value or wrong join). Check WHERE values against the sample rows. If 0 is truly the correct answer, return the same query unchanged.
2. `SELECT COUNT(*) FROM client JOIN district ON client.district_id = district.district_id WHERE client.birth_date LIKE '1920%' AND district.A3 LIKE 'east Bohemia%';`
   → accepted

**Gold SQL:**
```sql
SELECT COUNT(T1.client_id) FROM client AS T1 INNER JOIN district AS T2 ON T1.district_id = T2.district_id WHERE STRFTIME('%Y', T1.birth_date) = '1920' AND T2.A3 = 'east Bohemia'
```
**Gold rows:** `[(6,)]`  
**Agent rows:** `[(6,)]`

## 5. Fix 2 (strict retry) changed the outcome

`q920` · formula_1 · simple · qwen3:4b-instruct-2507-q4_K_M · run `c_strict` · FAIL (empty_result)

**Question:** Please list all the years that Silverstone Circuit was used in a Formula_1 race.

> **Why it matters:** Fix 2 backfired. Both runs started with the wrong value 'Silverstone' (the user wrote 'Silverstone Circuit'). The plain retry fixed it; with the strict note the model treated 'return the same SQL' as permission and resubmitted it. The note should point at the user's words, not the model's SQL.

**Before (`c_retry`, PASS):**
```sql
SELECT DISTINCT r.year
FROM races r
JOIN circuits c ON r.circuitId = c.circuitId
WHERE c.name = 'Silverstone Circuit'
```

**Attempts:**

1. `SELECT DISTINCT r.year FROM races r JOIN circuits c ON r.circuitId = c.circuitId WHERE c.name = 'Silverstone'`
   → The query ran but returned 0 rows. Check WHERE values against the sample rows (spelling, case, format) and the join conditions. If 0 rows is truly the correct answer, return the same query unchanged.
2. `SELECT DISTINCT r.year FROM races r JOIN circuits c ON r.circuitId = c.circuitId WHERE c.name = 'Silverstone'`
   → accepted

**Gold SQL:**
```sql
SELECT DISTINCT T2.year FROM circuits AS T1 INNER JOIN races AS T2 ON T2.circuitID = T1.circuitId WHERE T1.name = 'Silverstone Circuit'
```
**Gold rows:** `[(2009,), (2008,), (2007,)]`  
**Agent rows:** `[]`

## 6. Failure: bad_column

`q42` · california_schools · simple · qwen2.5-coder:7b · run `c_retry` · FAIL (bad_column)

**Question:** What is the type of education offered in the school who scored the highest average in Math?

> **Why it matters:** A made-up column name, repeated unchanged on all 3 retries: the error alone didn't help. qwen2.5-coder did this on 7 of its 42 retried questions in `c_retry`; qwen3 never did.

**Attempts:**

1. `SELECT T1.Educational_Option_Type FROM frpm AS T1 JOIN satscores AS T2 ON T1.CDSCode = T2.cds ORDER BY T2.AvgScrMath DESC LIMIT 1;`
   → The query failed with error: no such column: T1.Educational_Option_Type
2. `SELECT T1.Educational_Option_Type FROM frpm AS T1 JOIN satscores AS T2 ON T1.CDSCode = T2.cds ORDER BY T2.AvgScrMath DESC LIMIT 1;`
   → The query failed with error: no such column: T1.Educational_Option_Type
3. `SELECT T1.Educational_Option_Type FROM frpm AS T1 JOIN satscores AS T2 ON T1.CDSCode = T2.cds ORDER BY T2.AvgScrMath DESC LIMIT 1;`
   → The query failed with error: no such column: T1.Educational_Option_Type
4. `SELECT T1.Educational_Option_Type FROM frpm AS T1 JOIN satscores AS T2 ON T1.CDSCode = T2.cds ORDER BY T2.AvgScrMath DESC LIMIT 1;`
   → The query failed with error: no such column: T1.Educational_Option_Type

**Gold SQL:**
```sql
SELECT T2.EdOpsName FROM satscores AS T1 INNER JOIN schools AS T2 ON T1.cds = T2.CDSCode ORDER BY T1.AvgScrMath DESC LIMIT 1
```
**Gold rows:** `[('Traditional',)]`  
**Agent rows:** ``

## 7. Failure: empty_result

`q28` · california_schools · challenging · qwen2.5-coder:7b · run `c_retry` · FAIL (empty_result)

**Question:** Consider the average difference between K-12 enrollment and 15-17 enrollment of schools that are locally funded, list the names and DOC type of schools which has a difference above this average.

> **Why it matters:** SQLite `=` is case-sensitive: the data says 'Locally funded', the model wrote 'Locally Funded', got 0 rows and then confirmed the empty result instead of checking case.

**Attempts:**

1. `WITH avg_diff AS ( SELECT AVG("Enrollment (K-12)" - "Enrollment (Ages 5-17)") AS avg_diff FROM frpm WHERE "Charter Funding Type" = 'Locally Funded' ) SELECT s.School, s.DOCType FROM frpm f JOIN schools s ON f.CDSCode = s.CDSCode WHERE f."Enrollment (K-12)" - f."Enrollment (Ages 5-17)" > (SELECT avg_diff FROM avg_diff) AND f."Charter Funding Type" = 'Locally Funded';`
   → The query ran but returned 0 rows. Check WHERE values against the sample rows (spelling, case, format) and the join conditions. If 0 rows is truly the correct answer, return the same query unchanged.
2. `WITH avg_diff AS ( SELECT AVG("Enrollment (K-12)" - "Enrollment (Ages 5-17)") AS avg_diff FROM frpm WHERE "Charter Funding Type" = 'Locally Funded' ) SELECT s.School, s.DOCType FROM frpm f JOIN schools s ON f.CDSCode = s.CDSCode WHERE f."Enrollment (K-12)" - f."Enrollment (Ages 5-17)" > (SELECT avg_diff FROM avg_diff) AND f."Charter Funding Type" = 'Locally Funded';`
   → accepted

**Gold SQL:**
```sql
SELECT T2.School, T2.DOC FROM frpm AS T1 INNER JOIN schools AS T2 ON T1.CDSCode = T2.CDSCode WHERE T2.FundingType = 'Locally funded' AND (T1.`Enrollment (K-12)` - T1.`Enrollment (Ages 5-17)`) > (SELECT AVG(T3.`Enrollment (K-12)` - T3.`Enrollment (Ages 5-17)`) FROM frpm AS T3 INNER JOIN schools AS T4 ON T3.CDSCode = T4.CDSCode WHERE T4.FundingType = 'Locally funded')
```
**Gold rows:** `[('Mountain Oaks', '00'), ('Castle Rock', '00'), ('Charter Community School Home Study Academy', '00')]`  
**Agent rows:** `[]`

## 8. Failure: format_only

`q228` · toxicology · moderate · qwen2.5-coder:7b · run `c_retry` · FAIL (format_only)

**Question:** How much of the hydrogen in molecule TR206 is accounted for? Please provide your answer as a percentage with four decimal places.

> **Why it matters:** Right answer, but not rounded to the 4 decimals the question asked for. A miss under BIRD's rule; lenient EX accepts it.

**Attempts:**

1. `SELECT CAST(SUM(CASE WHEN element = 'h' THEN 1 ELSE 0 END) AS REAL) * 100 / COUNT(*) AS percentage FROM atom WHERE molecule_id = 'TR206';`
   → accepted

**Gold SQL:**
```sql
SELECT ROUND(CAST(COUNT(CASE WHEN T.element = 'h' THEN T.atom_id ELSE NULL END) AS REAL) * 100 / COUNT(T.atom_id),4) FROM atom AS T WHERE T.molecule_id = 'TR206'
```
**Gold rows:** `[(45.4545,)]`  
**Agent rows:** `[(45.45454545454545,)]`

## 9. Failure: syntax

`q1170` · thrombosis_prediction · moderate · qwen2.5-coder:7b · run `c_retry` · FAIL (syntax)

**Question:** How many patients hadn't undergone a medical examination until at least a year following their initial hospital visit?

> **Why it matters:** Dialect mix-up: `DATE_ADD(..., INTERVAL 1 YEAR)` is MySQL; SQLite uses `date(x, '+1 year')`. Repeated unchanged 3 times.

**Attempts:**

1. `SELECT COUNT(DISTINCT P.ID) FROM Patient P LEFT JOIN Examination E ON P.ID = E.ID WHERE E."Examination Date" IS NULL OR E."Examination Date" > DATE_ADD(P."First Date", INTERVAL 1 YEAR);`
   → The query failed with error: near "1": syntax error
2. `SELECT COUNT(DISTINCT P.ID) FROM Patient P LEFT JOIN Examination E ON P.ID = E.ID WHERE E."Examination Date" IS NULL OR E."Examination Date" > DATE_ADD(P."First Date", INTERVAL 1 YEAR);`
   → The query failed with error: near "1": syntax error
3. `SELECT COUNT(DISTINCT P.ID) FROM Patient P LEFT JOIN Examination E ON P.ID = E.ID WHERE E."Examination Date" IS NULL OR E."Examination Date" > DATE_ADD(P."First Date", INTERVAL 1 YEAR);`
   → The query failed with error: near "1": syntax error
4. `SELECT COUNT(DISTINCT P.ID) FROM Patient P LEFT JOIN Examination E ON P.ID = E.ID WHERE E."Examination Date" IS NULL OR E."Examination Date" > DATE_ADD(P."First Date", INTERVAL 1 YEAR);`
   → The query failed with error: near "1": syntax error

**Gold SQL:**
```sql
SELECT COUNT(DISTINCT T1.ID) FROM Patient AS T1 INNER JOIN Examination AS T2 ON T1.ID = T2.ID WHERE T1.Admission = '+' AND STRFTIME('%Y', T2.`Examination Date`) - STRFTIME('%Y', T1.`First Date`) >= 1
```
**Gold rows:** `[(11,)]`  
**Agent rows:** ``

## 10. Failure: syntax

`q158` · financial · simple · qwen3:4b-instruct-2507-q4_K_M · run `c_retry` · FAIL (syntax)

**Question:** What is the district Id of the account that placed the order with the id 33333?

> **Why it matters:** Our bug: schema.py shows the table as bare `order`, but it is an SQL keyword. qwen3 never thought to quote it. Fix: quote reserved words in `_show()`.

**Attempts:**

1. `SELECT DISTINCT d.district_id FROM order o JOIN account a ON o.account_id = a.account_id JOIN district d ON a.district_id = d.district_id WHERE o.order_id = 33333;`
   → The query failed with error: near "order": syntax error
2. `SELECT DISTINCT d.district_id FROM order o JOIN account a ON o.account_id = a.account_id JOIN district d ON a.district_id = d.district_id WHERE o.order_id = 33333;`
   → The query failed with error: near "order": syntax error
3. `SELECT DISTINCT d.district_id FROM order AS o JOIN account AS a ON o.account_id = a.account_id JOIN district AS d ON a.district_id = d.district_id WHERE o.order_id = 33333;`
   → The query failed with error: near "order": syntax error
4. `SELECT DISTINCT d.district_id FROM order o JOIN account a ON o.account_id = a.account_id JOIN district d ON a.district_id = d.district_id WHERE o.order_id = 33333;`
   → The query failed with error: near "order": syntax error

**Gold SQL:**
```sql
SELECT T3.district_id FROM `order` AS T1 INNER JOIN account AS T2 ON T1.account_id = T2.account_id INNER JOIN district AS T3 ON T2.district_id = T3.district_id WHERE T1.order_id = 33333
```
**Gold rows:** `[(64,)]`  
**Agent rows:** ``
