# PR #5210 截图证据与亲验脚本

队友技能名单「机器上能加、还没带的技能被画进名单」的改前/改后截图，以及亲验入口用的两个脚本。只给 PR #5210 正文引用，不合并进任何分支。

- before-*/after-*.png：组件预览站 /demo/catalog/agent-skills-dialog，真组件、props 模拟。
- real-*.png：真产品设置页 → AI 队友 → 测试队友A（假）→ 技能。读请求照转 okcheese.com（开发号），队友A、假机器由 accept-proxy.mjs 模拟，写请求全部拦下。
- accept-proxy.mjs / accept-front.mjs：亲验入口的代理与门口，改前/改后由控制台切换。
