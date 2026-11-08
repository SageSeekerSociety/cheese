# PR #5211 证据：技能面板「移除已带」

截图来源：本机 headless Chromium（playwright chromium-headless-shell 1254）打开 vite 开发服务器上的隔离页 `temp-skills.html`，视口 1100×900。

- 改前：`f8ddb66d1`（task/7ad53052 头，已含 #5195、#5201）。
- 改后：`5d59ad5bb`（task/f0c157ff 头）。

模拟范围：页面挂的是真 `AgentTeamSettings`（真 `useAgentSkills`、真 `AgentSkillsDialog`、真 Vuetify/i18n/提示条）；浏览器里的 `fetch` 换成内存假后端（`temp-skills.ts`），队友「芝士」带 `[wolfram, pdf, documents]`，平台目录 wolfram/documents/cheese，机器技能 pdf、ponytail。`?held=1` 让编辑租约回 409（模拟别人正在编辑），`?offline=1` 让机器技能索引回 503（模拟机器离线），`?fail=1` 或页顶勾选让下一次保存回 500。

自己跑：把 `temp-skills.html`、`vite.harness.config.ts` 放进 `frontend/`、`temp-skills.ts` 放进 `frontend/src/`，`pnpm exec vite --config vite.harness.config.ts --port 5791`，打开根地址，点队友行上的「技能」。
