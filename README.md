# PR #5207 截图证据

现场栏「往后读的行和 socket 先到的行时间错序」改前/改后的真浏览器截图，以及验收页的三个临时文件。只供 PR #5207 正文引用，不合并进任何分支。

- 改前：主干 37767844d 的 useSiteTranscript.ts；改后：任务分支 ca96434a9。
- 模拟范围：后端取数接口与 socket 推送是替身（accept-9083.vite.config.ts 里的中间件、页面按钮调用真的 receive）；现场栏组件 PanelSite、usePanelSite、useSiteTranscript 都是产品代码。
- 复现：放到 frontend/ 下（accept-9083.ts 放 frontend/src/），`npx vite --config accept-9083.vite.config.ts`，打开根地址，依次点「① 模拟 socket 推送 t2」「② 后端放出 [t1, t2] 并刷新」。
