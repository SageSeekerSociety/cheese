## Native first failure

Source: `13ca2d6ed51f3179f31b9039ddaaae986030ac4b`.
Pinned Claude Code 2.1.282, isolated ask_s_concurrency slot, Redis 12.

The parameterized HTTP continuation test stopped at busy creation: HTTP 403, “无法确认原生提问会话和执行区间”. The native working assertion passed, but the captured backend log records the initial turn as done before creation. The source/timing cause has not been established. No production authorization change was made.

Exit 1; first-failure stop. The remaining native modes and echo[True] were not executed. Root owns diagnosis and the independent two-seat/two-process HTTP test. Earlier history early-return cases are not evidence for that new test.

No accepted green behavior, repository guard or PDF check was rerun. This is not a Required CI result.
