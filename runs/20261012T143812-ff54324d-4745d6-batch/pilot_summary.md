# Pilot summary 20261012T143812-ff54324d-4745d6-batch

PILOT - feasibility only, excluded from confirmatory analysis

## Completion and parse
| vendor    |   setup |   planned |   completed |   completion_rate |   parse_ok |   parse_rate_of_planned |   parse_rate_of_completed |
|:----------|--------:|----------:|------------:|------------------:|-----------:|------------------------:|--------------------------:|
| anthropic |       1 |       600 |         600 |             1     |        599 |                   0.998 |                     0.998 |
| anthropic |       2 |       600 |         479 |             0.798 |        473 |                   0.788 |                     0.987 |
| anthropic |       3 |       600 |         600 |             1     |        600 |                   1     |                     1     |
| google    |       1 |       600 |         600 |             1     |        531 |                   0.885 |                     0.885 |
| google    |       2 |       600 |         600 |             1     |        594 |                   0.99  |                     0.99  |
| google    |       3 |       600 |         600 |             1     |        600 |                   1     |                     1     |
| openai    |       1 |       600 |         600 |             1     |        600 |                   1     |                     1     |
| openai    |       2 |       600 |         600 |             1     |        600 |                   1     |                     1     |
| openai    |       3 |       600 |         600 |             1     |        600 |                   1     |                     1     |

## Setup 2 (AI + tool)
| vendor    |   n |   tool_called_rate |   override_rate_vs_tool |   override_rate_when_tool_called |
|:----------|----:|-------------------:|------------------------:|---------------------------------:|
| anthropic | 479 |                  1 |                   0.046 |                            0.046 |
| google    | 600 |                  1 |                   0     |                            0     |
| openai    | 600 |                  1 |                   0.182 |                            0.182 |

## Fallback trigger (pre-reg 15.3 #5; setup 1, majority-of-3, net of control)
|      |   n_pairs |   flip_rate |   control_flip_rate |   net_flip_rate | newcombe_95ci                               | method   | trigger_fallback(upper<0.10)   |
|:-----|----------:|------------:|--------------------:|----------------:|:--------------------------------------------|:---------|:-------------------------------|
| name |        75 |   0.0933333 |           0.0266667 |       0.0666667 | [0.0089680799764068, 0.1454716574690747]    | newcombe | False                          |
| zip  |        74 |   0.108108  |           0.0266667 |       0.0810811 | [0.019367996413587024, 0.16448004938668345] | newcombe | False                          |

## Spend (USD, batch prices)
{"openai": 1.6328, "anthropic": 6.9397, "google": 2.0085}  total 10.581

## Calls with nonzero reasoning tokens
| vendor    |   setup |   calls_with_reasoning_tokens |
|:----------|--------:|------------------------------:|
| anthropic |       1 |                             0 |
| anthropic |       2 |                             0 |
| anthropic |       3 |                             0 |
| google    |       1 |                           438 |
| google    |       2 |                            30 |
| google    |       3 |                           124 |
| openai    |       1 |                             0 |
| openai    |       2 |                             0 |
| openai    |       3 |                             0 |