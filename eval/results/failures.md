# Interesting examples from the BIRD runs

Picked automatically by `eval/report.py` from the final runs of the shipped model (qwen3:4b): cases where a switch helped or hurt, noise in the benchmark, then one example per failure type.

## 1. Self-correction rescued an SQL error

`q158` · financial · simple · qwen3:4b-instruct-2507-q4_K_M · run `final` · PASS (pass)

**Question:** What is the district Id of the account that placed the order with the id 33333?

> **Why it matters:** The keyword fix at work: `order` is now quoted. The error was a wrong alias (`d` is `disp`, which has no district_id). The model resent the identical query once; the 'you sent this exact query before' note made it rewrite the join.

**Attempts:**

1. `SELECT d.district_id FROM "order" o JOIN disp d ON o.account_id = d.account_id JOIN account a ON d.account_id = a.account_id JOIN district d2 ON a.district_id = d2.district_id WHERE o.order_id = 33333;`
   → The query failed with error: no such column: d.district_id
2. `SELECT d.district_id FROM "order" o JOIN disp d ON o.account_id = d.account_id JOIN account a ON d.account_id = a.account_id JOIN district d2 ON a.district_id = d2.district_id WHERE o.order_id = 33333;`
   → The query failed with error: no such column: d.district_id You sent this exact query before and it failed the same way: rewrite it differently.
3. `SELECT d.district_id FROM "order" o JOIN account a ON o.account_id = a.account_id JOIN district d ON a.district_id = d.district_id WHERE o.order_id = 33333;`
   → accepted

**Gold SQL:**
```sql
SELECT T3.district_id FROM `order` AS T1 INNER JOIN account AS T2 ON T1.account_id = T2.account_id INNER JOIN district AS T3 ON T2.district_id = T3.district_id WHERE T1.order_id = 33333
```
**Gold rows:** `[(64,)]`  
**Agent rows:** `[(64,)]`

## 2. The zero-aggregate warning rescued it (SUSPECT_ZERO_AGG)

`q293` · toxicology · simple · qwen3:4b-instruct-2507-q4_K_M · run `final` · PASS (pass)

**Question:** How many atoms belong to the molecule labeled with carcinogenic compounds?

> **Why it matters:** `molecule.label` holds '+' / '-', not words. COUNT = 0 looks like a valid answer, so without SUSPECT_ZERO_AGG it would have been accepted; the warning sent the model to the sample rows, where it found '+'.

**Attempts:**

1. `SELECT COUNT(a.atom_id) FROM atom a JOIN molecule m ON a.molecule_id = m.molecule_id WHERE m.label = 'carcinogenic compounds';`
   → The query returned a single row of 0/NULL, which often means a filter matched nothing (e.g. a misspelled value or wrong join). Check WHERE values against the sample rows. If 0 is truly the correct answer, return the same query unchanged.
2. `SELECT COUNT(a.atom_id) FROM atom a JOIN molecule m ON a.molecule_id = m.molecule_id WHERE m.label = '+';`
   → accepted

**Gold SQL:**
```sql
SELECT COUNT(T1.atom_id) FROM atom AS T1 INNER JOIN molecule AS T2 ON T1.molecule_id = T2.molecule_id WHERE T2.label = '+'
```
**Gold rows:** `[(3692,)]`  
**Agent rows:** `[(3692,)]`

## 3. The evidence hint was needed

`q52` · california_schools · simple · qwen3:4b-instruct-2507-q4_K_M · run `final+ev` · PASS (pass)

**Question:** What is the total number of schools whose total SAT scores are greater or equal to 1500 whose mailing city is Lakeport?

> **Why it matters:** Without the hint the model read 'total SAT score >= 1500' as the `NumGE1500` column (a count of test takers). 'Total = Read + Math + Write' is a definition no schema shows; the hint supplies it.

**Evidence (shown to the model):** Total SAT scores can be computed by AvgScrRead + AvgScrMath + AvgScrWrite

**Before (`final`, FAIL):**
```sql
SELECT COUNT(*) AS total_schools
FROM satscores s
JOIN schools sc ON s.cds = sc.CDSCode
WHERE sc.MailCity = 'Lakeport' AND s.NumGE1500 >= 1500;
```

**Attempts:**

1. `SELECT COUNT(*) AS total_schools FROM satscores JOIN schools ON satscores.cds = schools.CDSCode WHERE (satscores.AvgScrRead + satscores.AvgScrMath + satscores.AvgScrWrite) >= 1500 AND schools.MailCity = 'Lakeport';`
   → accepted

**Gold SQL:**
```sql
SELECT COUNT(T1.cds) FROM satscores AS T1 INNER JOIN schools AS T2 ON T1.cds = T2.CDSCode WHERE T2.MailCity = 'Lakeport' AND (T1.AvgScrRead + T1.AvgScrMath + T1.AvgScrWrite) >= 1500
```
**Gold rows:** `[(2,)]`  
**Agent rows:** `[(2,)]`

## 4. The fan-out prompt rule broke it (why CHECK_FANOUT is off)

`q54` · california_schools · simple · qwen3:4b-instruct-2507-q4_K_M · run `final_fanout` · FAIL (empty_result)

**Question:** Please specify all of the schools and their related mailing zip codes that are under Avetik Atoian's administration.

> **Why it matters:** With the fan-out rule in the prompt, the model joined `satscores` to a question that needs one table. This school has no SAT row, so the join returned 0 rows, and the model confirmed the empty result. Without the rule it wrote the one-table query.

**Before (`final`, PASS):**
```sql
SELECT 
    s.School AS school,
    s.MailZip AS mailing_zip_code
FROM 
    schools s
WHERE 
    s.AdmFName1 = 'Avetik' 
    AND s.AdmLName1 = 'Atoian';
```

**Attempts:**

1. `SELECT s.School AS school_name, s.MailZip AS mailing_zip_code FROM schools s JOIN satscores sc ON s.CDSCode = sc.cds WHERE s.AdmFName1 = 'Avetik' AND s.AdmLName1 = 'Atoian';`
   → The query ran but returned 0 rows. Check WHERE values against the sample rows (spelling, case, format) and the join conditions. If 0 rows is truly the correct answer, return the same query unchanged.
2. `SELECT s.School AS school_name, s.MailZip AS mailing_zip_code FROM schools s JOIN satscores sc ON s.CDSCode = sc.cds WHERE s.AdmFName1 = 'Avetik' AND s.AdmLName1 = 'Atoian';`
   → accepted

**Gold SQL:**
```sql
SELECT School, MailZip FROM schools WHERE AdmFName1 = 'Avetik' AND AdmLName1 = 'Atoian'
```
**Gold rows:** `[('Fairview Middle', '93926-0238')]`  
**Agent rows:** `[]`

## 5. The SQL-shape check broke it (extra column)

`q668` · codebase_community · simple · qwen3:4b-instruct-2507-q4_K_M · run `final` · FAIL (wrong_result)

**Question:** What is the display name of the user who acquired the highest amount of badges?

> **Why it matters:** The shape check did what it was built for: show the value the ranking is based on. BIRD's rule compares exact columns, so the extra `BadgeCount` column scores as wrong. For a user, seeing 456 badges is better; on BIRD it cost 2 questions (q668, q1173).

**Before (`final_noshape`, PASS):**
```sql
SELECT u.DisplayName
FROM users u
JOIN badges b ON u.Id = b.UserId
GROUP BY u.Id, u.DisplayName
ORDER BY COUNT(b.Id) DESC
LIMIT 1;
```

**Attempts:**

1. `SELECT u.DisplayName FROM users u JOIN badges b ON u.Id = b.UserId ORDER BY COUNT(b.Id) DESC LIMIT 1;`
   → The query failed with error: misuse of aggregate: COUNT()
2. `SELECT u.DisplayName FROM users u JOIN badges b ON u.Id = b.UserId GROUP BY u.Id, u.DisplayName ORDER BY COUNT(b.Id) DESC LIMIT 1;`
   → The query ranks rows by COUNT(b.Id) but doesn't return it. Add it to the SELECT list with a clear alias so the user can see the values. If the question asked for specific columns only, return the same query unchanged.
3. `SELECT u.DisplayName, COUNT(b.Id) AS BadgeCount FROM users u JOIN badges b ON u.Id = b.UserId GROUP BY u.Id, u.DisplayName ORDER BY BadgeCount DESC LIMIT 1;`
   → accepted

**Gold SQL:**
```sql
SELECT T1.DisplayName FROM users AS T1 INNER JOIN badges AS T2 ON T1.Id = T2.UserId GROUP BY T1.DisplayName ORDER BY COUNT(T1.Id) DESC LIMIT 1
```
**Gold rows:** `[('whuber',)]`  
**Agent rows:** `[('whuber', 456)]`

## 6. Run noise: same prompt, different answer

`q962` · formula_1 · challenging · qwen3:4b-instruct-2507-q4_K_M · run `final+ev` · PASS (pass)

**Question:** From 2000 to 2005, what percentage of drivers who were born before 1985 and the lap numbers were over 50?

> **Why it matters:** The shape check didn't fire here, so both runs sent the same prompt at temperature 0, yet one run used `driverStandings` and the other `results`. Differences like this are why ±1 point between runs is noise.

**Evidence (shown to the model):** born before 1985 refers to year(dob)<1985; in 2000 to 2005 refers to year between 2000 and 2005; percentage = Divide(COUNT(driverId where year (dob) <1985 and laps >50),COUNT(DriverID where year between 2000 and 2005) *100;

**Before (`final_noshape+ev`, FAIL):**
```sql
SELECT 
    (COUNT(CASE WHEN d.dob < '1985-12-31' THEN 1 END) * 100.0 / COUNT(*)) AS percentage
FROM 
    races r
JOIN 
    driverStandings ds ON r.raceId = ds.raceId
JOIN 
    drivers d ON ds.driverId = d.driverId
WHERE 
    r.year BETWEEN 2000 AND 2005;
```

**Attempts:**

1. `SELECT (COUNT(CASE WHEN d.dob < '1985-12-31' AND r.laps > 50 THEN 1 END) * 100.0 / COUNT(*)) AS percentage FROM results r JOIN drivers d ON r.driverId = d.driverId JOIN races ra ON r.raceId = ra.raceId WHERE ra.year BETWEEN 2000 AND 2005;`
   → accepted

**Gold SQL:**
```sql
SELECT CAST(SUM(IIF(STRFTIME('%Y', T3.dob) < '1985' AND T1.laps > 50, 1, 0)) AS REAL) * 100 / COUNT(*) FROM results AS T1 INNER JOIN races AS T2 on T1.raceId = T2.raceId INNER JOIN drivers AS T3 on T1.driverId = T3.driverId WHERE T2.year BETWEEN 2000 AND 2005
```
**Gold rows:** `[(64.6189376443418,)]`  
**Agent rows:** `[(64.6189376443418,)]`

## 7. Label noise: the gold answer is wrong

`q389` · card_games · simple · qwen3:4b-instruct-2507-q4_K_M · run `final` · FAIL (wrong_result)

**Question:** List down the name of cards with original types of Creature - Elf and the date of rulings for these cards.

> **Why it matters:** BIRD's gold SQL returns card ids although the question asks for names. The agent's answer is right and scores as wrong in every run of every config: a labeling error in BIRD dev, not an agent failure.

**Attempts:**

1. `SELECT c.name, r.date FROM cards c JOIN rulings r ON c.uuid = r.uuid WHERE c.originalType = 'Creature - Elf';`
   → accepted

**Gold SQL:**
```sql
SELECT T1.id, T2.date FROM cards AS T1 INNER JOIN rulings AS T2 ON T1.uuid = T2.uuid WHERE T1.originalType = 'Creature - Elf'
```
**Gold rows:** `[(240, '2007-05-01'), (242, '2004-10-04'), (242, '2005-08-01')]`  
**Agent rows:** `[('Elven Riders', '2007-05-01'), ('Elvish Champion', '2004-10-04'), ('Elvish Champion', '2005-08-01')]`

## 8. Failure: wrong_result

`q28` · california_schools · challenging · qwen3:4b-instruct-2507-q4_K_M · run `final` · FAIL (wrong_result)

**Question:** Consider the average difference between K-12 enrollment and 15-17 enrollment of schools that are locally funded, list the names and DOC type of schools which has a difference above this average.

> **Why it matters:** Two wrong choices in a wide schema: 'locally funded' became `"Charter Funding Type" = 'Directly funded'` in frpm instead of `schools.FundingType = 'Locally funded'`, and `DOCType` (a description) instead of `DOC` (the code).

**Attempts:**

1. `SELECT s.School, s.DOCType FROM frpm f JOIN schools s ON f.CDSCode = s.CDSCode WHERE f."Charter Funding Type" = 'Directly funded' AND (f."Enrollment (K-12)" - f."Enrollment (Ages 5-17)") > ( SELECT AVG(f2."Enrollment (K-12)" - f2."Enrollment (Ages 5-17)") FROM frpm f2 JOIN schools s2 ON f2.CDSCode = s2.CDSCode WHERE s2.Charter = 1 AND s2.FundingType = 'Directly funded' )`
   → accepted

**Gold SQL:**
```sql
SELECT T2.School, T2.DOC FROM frpm AS T1 INNER JOIN schools AS T2 ON T1.CDSCode = T2.CDSCode WHERE T2.FundingType = 'Locally funded' AND (T1.`Enrollment (K-12)` - T1.`Enrollment (Ages 5-17)`) > (SELECT AVG(T3.`Enrollment (K-12)` - T3.`Enrollment (Ages 5-17)`) FROM frpm AS T3 INNER JOIN schools AS T4 ON T3.CDSCode = T4.CDSCode WHERE T4.FundingType = 'Locally funded')
```
**Gold rows:** `[('Mountain Oaks', '00'), ('Castle Rock', '00'), ('Charter Community School Home Study Academy', '00')]`  
**Agent rows:** `[('Civicorps Corpsmember Academy', 'Unified School District'), ('Clayton Valley Charter High', 'County Office of Education (COE)'), ('Crescent View West Charter', 'County Office of Education (COE)')]`

## 9. Failure: empty_result

`q42` · california_schools · simple · qwen3:4b-instruct-2507-q4_K_M · run `final` · FAIL (empty_result)

**Question:** What is the type of education offered in the school who scored the highest average in Math?

> **Why it matters:** The education type is `schools.EdOpsName`; the model looked in frpm instead. The NULL and 0-row warnings made it rewrite the filter, but never the table choice, and it finally confirmed the empty result.

**Attempts:**

1. `SELECT DISTINCT s."Educational Option Type" FROM frpm f JOIN satscores s ON f."CDSCode" = s.cds ORDER BY s."AvgScrMath" DESC LIMIT 1;`
   → The query failed with error: no such column: s.Educational Option Type
2. `SELECT DISTINCT f."Educational Option Type" FROM frpm f JOIN satscores s ON f."CDSCode" = s.cds ORDER BY s."AvgScrMath" DESC LIMIT 1;`
   → The query ran but every value is NULL. Check the columns, joins and aggregates. If that is truly correct, return the same query unchanged.
3. `SELECT DISTINCT f."Educational Option Type" FROM frpm f JOIN satscores s ON f."CDSCode" = s.cds WHERE s."AvgScrMath" = (SELECT MAX("AvgScrMath") FROM satscores) LIMIT 1;`
   → The query ran but returned 0 rows. Check WHERE values against the sample rows (spelling, case, format) and the join conditions. If 0 rows is truly the correct answer, return the same query unchanged.
4. `SELECT DISTINCT f."Educational Option Type" FROM frpm f JOIN satscores s ON f."CDSCode" = s.cds WHERE s."AvgScrMath" = (SELECT MAX("AvgScrMath") FROM satscores) LIMIT 1;`
   → accepted

**Gold SQL:**
```sql
SELECT T2.EdOpsName FROM satscores AS T1 INNER JOIN schools AS T2 ON T1.cds = T2.CDSCode ORDER BY T1.AvgScrMath DESC LIMIT 1
```
**Gold rows:** `[('Traditional',)]`  
**Agent rows:** `[]`

## 10. Failure: format_only

`q228` · toxicology · moderate · qwen3:4b-instruct-2507-q4_K_M · run `final` · FAIL (format_only)

**Question:** How much of the hydrogen in molecule TR206 is accounted for? Please provide your answer as a percentage with four decimal places.

> **Why it matters:** Right answer, but not rounded to the 4 decimals the question asked for. A miss under BIRD's rule; lenient EX accepts it.

**Attempts:**

1. `SELECT CAST(SUM(CASE WHEN a.element = 'h' THEN 1 ELSE 0 END) AS FLOAT) * 100.0 / COUNT(*) AS percentage FROM atom a JOIN molecule m ON a.molecule_id = m.molecule_id WHERE m.molecule_id = 'TR206';`
   → accepted

**Gold SQL:**
```sql
SELECT ROUND(CAST(COUNT(CASE WHEN T.element = 'h' THEN T.atom_id ELSE NULL END) AS REAL) * 100 / COUNT(T.atom_id),4) FROM atom AS T WHERE T.molecule_id = 'TR206'
```
**Gold rows:** `[(45.4545,)]`  
**Agent rows:** `[(45.45454545454545,)]`

## 11. Failure: syntax

`q1232` · thrombosis_prediction · challenging · qwen3:4b-instruct-2507-q4_K_M · run `final` · FAIL (syntax)

**Question:** Provide ID, sex and age of patient who has blood glucose (GLU) not within normal range but with total cholesterol(T-CHO) within normal range.

> **Why it matters:** `CAST(x)` without `AS type` is invalid SQLite. The error only says 'near ")"', so the model kept rewriting the WHERE clause and never touched the CAST.

**Attempts:**

1. `SELECT p.ID, p.SEX, CAST(julianday('now') - julianday(p.Birthday)) AS age FROM Patient p JOIN Laboratory l ON p.ID = l.ID WHERE l.GLU IS NOT NULL AND l.GLU > 100 OR l.GLU < 50 AND l."T-CHO" IS NOT NULL AND l."T-CHO" BETWEEN 100 AND 200;`
   → The query failed with error: near ")": syntax error
2. `SELECT p.ID, p.SEX, CAST(julianday('now') - julianday(p.Birthday)) AS age FROM Patient p JOIN Laboratory l ON p.ID = l.ID WHERE l.GLU IS NOT NULL AND (l.GLU > 100 OR l.GLU < 50) AND l."T-CHO" IS NOT NULL AND l."T-CHO" BETWEEN 100 AND 200;`
   → The query failed with error: near ")": syntax error
3. `SELECT p.ID, p.SEX, CAST(julianday('now') - julianday(p.Birthday)) AS age FROM Patient p JOIN Laboratory l ON p.ID = l.ID WHERE l.GLU IS NOT NULL AND (l.GLU > 100 OR l.GLU < 50) AND l."T-CHO" IS NOT NULL AND l."T-CHO" BETWEEN 100 AND 200;`
   → The query failed with error: near ")": syntax error You sent this exact query before and it failed the same way: rewrite it differently.
4. `SELECT p.ID, p.SEX, CAST(julianday('now') - julianday(p.Birthday)) AS age FROM Patient p JOIN Laboratory l ON p.ID = l.ID WHERE l.GLU IS NOT NULL AND (l.GLU > 100 OR l.GLU < 50) AND l."T-CHO" IS NOT NULL AND l."T-CHO" >= 100 AND l."T-CHO" <= 200;`
   → The query failed with error: near ")": syntax error

**Gold SQL:**
```sql
SELECT DISTINCT T1.ID, T1.SEX , STRFTIME('%Y', CURRENT_TIMESTAMP) - STRFTIME('%Y', T1.Birthday) FROM Patient AS T1 INNER JOIN Laboratory AS T2 ON T1.ID = T2.ID WHERE T2.GLU >= 180 AND T2.`T-CHO` < 250
```
**Gold rows:** `[(57266, 'M', 103), (2371753, 'F', 94), (3354706, 'F', 90)]`  
**Agent rows:** ``
