# PR #5214 截图证据

频道首屏消息页任务卡「在做」记号静止/转动的改前/改后真浏览器截图，只供 PR #5214 正文引用，不合并进任何分支。

- 改前：主干 37767844d 的后端；改后：任务分支 efd445bfa 的后端。前端同一份（本 PR 前端只改了一句类型注释）。
- 环境：本机 `node e2e/dev-stack.mjs` 隔离栈（自建 Postgres/Redis、种子用户 alice），无任何线上数据。
- 模拟范围：`seed.py` 新建两件任务，只对其中一件用 `session_state.register/declare(RUNNING)` 宣告它队友的会话在跑（与集成测试同一个端口），另一件不动；未接真模型、未起真 runner。
- 截图：`shot.mjs` 用 Playwright Chromium headless 打开 `/projects/<id>/topics/<room>`，挡掉 WebSocket（只看首屏 `/blocks` 这一路，不让订阅快照重画），关掉条款/内测弹窗和右栏后截图，并读出首屏 `/blocks` 回包里的 `session_running`。
