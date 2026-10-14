# PR #3724 cold-open rerun

All times are milliseconds. Cells are base / head medians.

## cold

| Page | ready_ms | long_task_ms | api_serial_ms |
|---|---:|---:|---:|
| inbox | 1193.5 / 1219.8 | 434.0 / 450.5 | 357.4 / 311.7 |
| settings-devices | 1237.7 / 1178.3 | 436.0 / 446.0 | 411.8 / 394.1 |
| settings-connections | 991.8 / 1103.6 | 379.0 / 493.0 | 208.6 / 253.8 |
| settings-notifications | 930.5 / 935.9 | 316.0 / 338.0 | 165.1 / 178.9 |
| user-profile | 1283.0 / 1203.9 | 498.5 / 483.0 | 340.4 / 293.5 |
| project-channels | 1261.3 / 1248.5 | 477.5 / 558.0 | 506.4 / 483.1 |
| project-tasks | 1376.2 / 1263.7 | 606.0 / 584.5 | 469.9 / 403.4 |
| project-library | 1390.7 / 1420.1 | 677.5 / 698.5 | 400.1 / 385.7 |
| project-docs-charter | 2090.4 / 1971.7 | 758.0 / 668.5 | 1017.5 / 953.2 |
| project-artifact | 1371.0 / 1258.2 | 559.5 / 513.0 | 473.1 / 456.6 |
| project-members | 1322.4 / 1313.3 | 564.0 / 571.5 | 532.6 / 482.9 |
| project-settings | 1136.4 / 1150.7 | 513.5 / 518.0 | 240.9 / 164.6 |
| project-skills | 1297.8 / 1335.5 | 598.5 / 562.0 | 475.8 / 462.0 |
| teams | 1279.5 / 1276.2 | 478.0 / 518.0 | 476.3 / 461.0 |
| team-detail | 1194.9 / 1256.3 | 348.5 / 413.0 | 428.1 / 423.9 |
| spaces | 1206.7 / 1214.8 | 492.0 / 452.0 | 369.5 / 416.9 |
| channel | 1799.2 / 1881.1 | 924.0 / 995.0 | 662.5 / 791.3 |
| thread | 2397.7 / 2231.6 | 903.0 / 880.5 | 1205.5 / 1199.8 |
| workspace-task | 1906.7 / 1841.2 | 789.0 / 766.0 | 867.7 / 769.8 |

Aggregate definitions and paired deltas are in summary.json.

## walk

| Page | ready_ms | long_task_ms | api_serial_ms |
|---|---:|---:|---:|
| inbox | 322.6 / 380.8 | 64.5 / 81.0 | 0.0 / 0.0 |
| settings-devices | 358.0 / 366.9 | 95.0 / 86.5 | 0.0 / 0.0 |
| settings-connections | 295.5 / 307.0 | 101.5 / 96.0 | 0.0 / 0.0 |
| settings-notifications | 231.1 / 228.0 | 77.0 / 68.0 | 0.0 / 0.0 |
| user-profile | 400.8 / 400.8 | 177.0 / 172.0 | 0.0 / 0.0 |
| project-channels | 247.6 / 248.3 | 72.0 / 77.5 | 0.0 / 0.0 |
| project-tasks | 125.2 / 119.2 | 75.0 / 73.5 | 0.0 / 0.0 |
| project-library | 224.8 / 195.4 | 84.0 / 95.0 | 0.0 / 0.0 |
| project-docs-charter | 409.9 / 431.1 | 74.0 / 122.0 | 127.7 / 133.4 |
| project-artifact | 242.4 / 207.3 | 59.0 / 53.0 | 0.0 / 0.0 |
| project-members | 261.6 / 242.2 | 98.0 / 85.5 | 0.0 / 0.0 |
| project-settings | 151.2 / 138.2 | 89.5 / 79.0 | 0.0 / 0.0 |
| project-skills | 310.2 / 285.8 | 131.0 / 122.0 | 0.0 / 0.0 |
| teams | 533.0 / 493.6 | 122.5 / 120.5 | 0.0 / 0.0 |
| team-detail | 400.6 / 420.9 | 110.5 / 115.5 | 0.0 / 0.0 |
| spaces | 435.8 / 446.5 | 114.5 / 155.5 | 0.0 / 0.0 |
| channel | 718.3 / 707.8 | 373.0 / 452.5 | 108.2 / 133.8 |
| thread | 1180.1 / 1043.9 | 245.5 / 300.0 | 0.0 / 0.0 |
| workspace-task | 455.7 / 476.6 | 198.5 / 255.0 | 0.0 / 0.0 |

Aggregate definitions and paired deltas are in summary.json.

## Limits

- All samples kept, including the first pair; no outlier exclusions.
- Descriptive medians only; eight cold pairs/four walk pairs do not establish a stable benefit.
- API later-burst totals and main-thread totals overlap; neither proves a critical-path bottleneck.
- Long tasks use the original accumulated-window metric, sampled just after content ready.
- The old lost run is not pooled with this rerun.
- Project-docs-charter retains the original tabs-ready criterion; both first-pair screenshots still show the document-body skeleton. It is not document-body readiness.
- Screenshots use English UI and lack CJK fonts; Chinese demo text renders as missing-glyph boxes in both variants.

Measurement errors: 0; page errors: 0.
HTTP failures, including warm-up, are listed separately in summary.json.
