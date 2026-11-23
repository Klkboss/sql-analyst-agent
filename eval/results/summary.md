# BIRD dev benchmark

Generated 2026-11-24 by `python -m eval.report`. Sample: 200 questions (80 simple / 80 moderate / 40 challenging), seed 42, ids in `bird_sample.json`. Ollama `num_ctx=4096` (pinned), query timeout 30s, temperature 0.

- **EX** = execution accuracy, BIRD's official rule (same set of result rows).
- **Adjusted EX** re-weights the per-difficulty scores to BIRD dev's real mix (925 / 464 / 145), so it is comparable to published dev numbers.
- **Lenient EX** also ignores column order and rounding; the gap to EX is formatting-only misses.
- `+ev` runs add BIRD's human-written evidence hint to the question.
- **Table selection %**: questions whose full schema didn't fit the context, so the agent picked tables first.

## qwen2.5-coder:7b

| Run           | Adds                         |   N |   EX % |   Adjusted EX % |   Simple % |   Moderate % |   Challenging % |   Lenient EX % |   Avg retries |   Avg s/question |   tok/s |   Table selection % |
|:--------------|:-----------------------------|----:|-------:|----------------:|-----------:|-------------:|----------------:|---------------:|--------------:|-----------------:|--------:|--------------------:|
| `a_names`     | names only                   | 200 |   25.5 |            28.8 |       35   |         20   |            17.5 |           25.5 |          0    |              4.2 |      31 |                 0   |
| `b_schema`    | + full schema                | 200 |   34.5 |            38.3 |       45   |         30   |            22.5 |           35   |          0    |              4.7 |      30 |                22   |
| `c_retry`     | + self-correction            | 200 |   35.5 |            39.3 |       46.2 |         30   |            25   |           36   |          0.36 |              6.2 |      30 |                22   |
| `c_zero`      | + fix 1 (zero aggregate)     | 200 |   35.5 |            39.3 |       46.2 |         30   |            25   |           36   |          0.43 |              6.9 |      30 |                22   |
| `c_strict`    | + fix 2 (strict retry)       | 200 |   35.5 |            39.3 |       46.2 |         30   |            25   |           36   |          0.36 |              6.3 |      30 |                22   |
| `c_both`      | + fix 1 + fix 2              | 200 |   35.5 |            39.3 |       46.2 |         30   |            25   |           36   |          0.43 |              6.5 |      30 |                22   |
| `a_names+ev`  | names only + evidence        | 200 |   47.5 |            50.7 |       55   |         48.8 |            30   |           48   |          0    |              4   |      32 |                 0   |
| `b_schema+ev` | + full schema + evidence     | 200 |   49.5 |            53   |       58.8 |         46.2 |            37.5 |           50   |          0    |              4.6 |      30 |                23.5 |
| `c_retry+ev`  | + self-correction + evidence | 200 |   51.5 |            55.5 |       62.5 |         46.2 |            40   |           52   |          0.32 |              6.1 |      30 |                23.5 |
| `c_both+ev`   | + fix 1 + fix 2 + evidence   | 200 |   51.5 |            56   |       63.7 |         46.2 |            37.5 |           52   |          0.34 |              6.3 |      29 |                23.5 |

### Effect of each change (paired, same questions)

| Change                     |   N (both done) |   Fixed |   Broke |   Net EX points |
|:---------------------------|----------------:|--------:|--------:|----------------:|
| `b_schema` → `c_retry`     |             200 |       2 |       0 |               1 |
| `c_retry` → `c_zero`       |             200 |       0 |       0 |               0 |
| `c_retry` → `c_strict`     |             200 |       0 |       0 |               0 |
| `c_retry` → `c_both`       |             200 |       0 |       0 |               0 |
| `b_schema` → `b_schema+ev` |             200 |      36 |       6 |              15 |
| `c_retry` → `c_retry+ev`   |             200 |      38 |       6 |              16 |
| `c_retry+ev` → `c_both+ev` |             200 |       1 |       1 |               0 |

### Failure reasons

| reason           |   a_names |   b_schema |   c_retry |   c_zero |   c_strict |   c_both |   a_names+ev |   b_schema+ev |   c_retry+ev |   c_both+ev |
|:-----------------|----------:|-----------:|----------:|---------:|-----------:|---------:|-------------:|--------------:|-------------:|------------:|
| ambiguous_column |         1 |          1 |         0 |        0 |          0 |        0 |            2 |             2 |            0 |           0 |
| bad_column       |        26 |         17 |        10 |       10 |         12 |       12 |           23 |            18 |            9 |          11 |
| bad_table        |         0 |          0 |         1 |        1 |          0 |        0 |            0 |             0 |            1 |           0 |
| empty_result     |        39 |         15 |        18 |       18 |         17 |       17 |           22 |             8 |            9 |           8 |
| format_only      |         0 |          1 |         1 |        1 |          1 |        1 |            1 |             1 |            1 |           1 |
| guard            |         0 |          0 |         0 |        0 |          0 |        0 |            1 |             0 |            0 |           0 |
| pass             |        51 |         69 |        71 |       71 |         71 |       71 |           95 |            99 |          103 |         103 |
| sql_error        |         0 |          0 |         0 |        0 |          0 |        0 |            0 |             1 |            0 |           0 |
| syntax           |         2 |          4 |         1 |        1 |          2 |        2 |            4 |             4 |            0 |           1 |
| wrong_result     |        81 |         93 |        98 |       98 |         97 |       97 |           52 |            67 |           77 |          76 |

## qwen3:4b-instruct-2507-q4_K_M

| Run                | Adds                                       |   N |   EX % |   Adjusted EX % |   Simple % |   Moderate % |   Challenging % |   Lenient EX % |   Avg retries |   Avg s/question |   tok/s |   Table selection % |
|:-------------------|:-------------------------------------------|----:|-------:|----------------:|-----------:|-------------:|----------------:|---------------:|--------------:|-----------------:|--------:|--------------------:|
| `a_names`          | names only                                 | 200 |   21   |            25.1 |       32.5 |         15   |            10   |           21   |          0    |              3.3 |      58 |                 0   |
| `b_schema`         | + full schema                              | 200 |   36   |            38.3 |       42.5 |         32.5 |            30   |           36.5 |          0    |              4   |      55 |                22   |
| `c_retry`          | + self-correction                          | 200 |   40   |            42.5 |       47.5 |         35   |            35   |           40.5 |          0.27 |              4.9 |      53 |                22   |
| `c_zero`           | + fix 1 (zero aggregate)                   | 200 |   41   |            44   |       50   |         35   |            35   |           41.5 |          0.35 |              5.1 |      54 |                22   |
| `c_strict`         | + fix 2 (strict retry)                     | 200 |   39.5 |            41.8 |       46.2 |         35   |            35   |           40   |          0.28 |              4.9 |      54 |                22   |
| `c_both`           | + fix 1 + fix 2                            | 200 |   40.5 |            43.3 |       48.8 |         35   |            35   |           41   |          0.35 |              5.2 |      53 |                22   |
| `a_names+ev`       | names only + evidence                      | 200 |   44   |            46.5 |       50   |         43.8 |            32.5 |           44   |          0    |              3.6 |      53 |                 0   |
| `b_schema+ev`      | + full schema + evidence                   | 200 |   52.5 |            54.8 |       58.8 |         50   |            45   |           53   |          0    |              4.3 |      51 |                23.5 |
| `c_retry+ev`       | + self-correction + evidence               | 200 |   56   |            58.5 |       63.7 |         50   |            52.5 |           56.5 |          0.21 |              4.8 |      52 |                23.5 |
| `c_both+ev`        | + fix 1 + fix 2 + evidence                 | 200 |   55.5 |            58.3 |       63.7 |         50   |            50   |           56   |          0.28 |              5.7 |      43 |                23.5 |
| `final`            | shipped defaults                           | 200 |   38.5 |            41   |       46.2 |         32.5 |            35   |           39   |          0.42 |              6.1 |      56 |                39.5 |
| `final+ev`         | shipped defaults + evidence                | 200 |   57   |            58   |       61.3 |         50   |            62.5 |           58   |          0.32 |              5.5 |      56 |                39.5 |
| `final_fanout`     | shipped + fan-out check                    | 200 |   37   |            39.4 |       43.8 |         33.8 |            30   |           37.5 |          0.52 |              6.4 |      54 |                39.5 |
| `final_fanout+ev`  | shipped + fan-out check + evidence         | 200 |   55   |            57.4 |       62.5 |         48.8 |            52.5 |           56   |          0.32 |              5.8 |      55 |                39.5 |
| `final_noshape`    | shipped without SQL-shape check            | 200 |   39.5 |            42   |       47.5 |         32.5 |            37.5 |           40   |          0.41 |              6.2 |      55 |                39.5 |
| `final_noshape+ev` | shipped without SQL-shape check + evidence | 200 |   57.5 |            58.7 |       62.5 |         50   |            62.5 |           58.5 |          0.29 |              5.5 |      55 |                39.5 |

### Effect of each change (paired, same questions)

| Change                          |   N (both done) |   Fixed |   Broke |   Net EX points |
|:--------------------------------|----------------:|--------:|--------:|----------------:|
| `b_schema` → `c_retry`          |             200 |       8 |       0 |             4   |
| `c_retry` → `c_zero`            |             200 |       2 |       0 |             1   |
| `c_retry` → `c_strict`          |             200 |       0 |       1 |            -0.5 |
| `c_retry` → `c_both`            |             200 |       2 |       1 |             0.5 |
| `b_schema` → `b_schema+ev`      |             200 |      40 |       7 |            16.5 |
| `c_retry` → `c_retry+ev`        |             200 |      39 |       7 |            16   |
| `c_retry+ev` → `c_both+ev`      |             200 |       1 |       2 |            -0.5 |
| `c_zero` → `final`              |             200 |       8 |      13 |            -2.5 |
| `c_retry+ev` → `final+ev`       |             200 |      15 |      13 |             1   |
| `final` → `final_fanout`        |             200 |       6 |       9 |            -1.5 |
| `final+ev` → `final_fanout+ev`  |             200 |       6 |      10 |            -2   |
| `final_noshape` → `final`       |             200 |       0 |       2 |            -1   |
| `final_noshape+ev` → `final+ev` |             200 |       1 |       2 |            -0.5 |

### Failure reasons

| reason       |   a_names |   b_schema |   c_retry |   c_zero |   c_strict |   c_both |   a_names+ev |   b_schema+ev |   c_retry+ev |   c_both+ev |   final |   final+ev |   final_fanout |   final_fanout+ev |   final_noshape |   final_noshape+ev |
|:-------------|----------:|-----------:|----------:|---------:|-----------:|---------:|-------------:|--------------:|-------------:|------------:|--------:|-----------:|---------------:|------------------:|----------------:|-------------------:|
| bad_column   |        24 |         10 |         0 |        0 |          0 |        0 |           19 |            12 |            1 |           1 |       0 |          1 |              0 |                 0 |               0 |                  1 |
| bad_function |         1 |          0 |         0 |        0 |          0 |        0 |            1 |             1 |            0 |           0 |       0 |          0 |              0 |                 0 |               0 |                  0 |
| bad_table    |         4 |          0 |         0 |        0 |          0 |        0 |            0 |             0 |            0 |           0 |       0 |          0 |              0 |                 0 |               0 |                  0 |
| empty_result |        50 |         25 |        20 |       20 |         24 |       24 |           32 |            15 |           14 |          16 |      22 |         12 |             27 |                12 |              22 |                 12 |
| format_only  |         0 |          1 |         1 |        1 |          1 |        1 |            0 |             1 |            1 |           1 |       1 |          2 |              1 |                 2 |               1 |                  2 |
| guard        |         1 |          0 |         0 |        0 |          0 |        0 |            0 |             0 |            0 |           0 |       0 |          0 |              0 |                 0 |               0 |                  0 |
| pass         |        42 |         72 |        80 |       82 |         79 |       81 |           88 |           105 |          112 |         111 |      77 |        114 |             74 |               110 |              79 |                115 |
| sql_error    |         1 |          0 |         0 |        0 |          0 |        0 |            0 |             0 |            0 |           0 |       0 |          0 |              0 |                 0 |               0 |                  0 |
| syntax       |         6 |          8 |         1 |        1 |          1 |        1 |            3 |             3 |            2 |           2 |       1 |          0 |              1 |                 0 |               1 |                  0 |
| wrong_result |        71 |         84 |        98 |       96 |         95 |       93 |           57 |            63 |           70 |          69 |      99 |         71 |             97 |                76 |              97 |                 70 |
