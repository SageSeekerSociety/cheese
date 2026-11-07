PR #5196 evidence (not for merge). Taken 2026-11-07 ~16:27 UTC.
before-*.png : local vite serving base 043455b5d (frontend/), app at http://127.0.0.1:5311
after-*.png  : local vite serving PR head 7241ff47f (frontend/), app at http://127.0.0.1:5312
Both: real app, every /api request proxied to production https://okcheese.com/api, dev account login.
Controllable stand-in (in-page fetch wrapper, see repro-playwright.cjs): /api/assistant/conversations*
and /api/assistant/routines only. Conversation c1 is answering q1; the answer stream sends its first part,
then an artificial 8 s delay, then the rest + done. Chromium (Playwright) 1440x900, zh-CN.
Steps: Settings > 芝士 > 例行事务 > click 查看对话; wait for the first part; click 查看对话 again;
click the back arrow; wait 10 s; screenshot. The middle inbox column is masked grey (internal data).
Result: before shows the answer 2x (answer stream read 2x), after 1x (read 1x).
