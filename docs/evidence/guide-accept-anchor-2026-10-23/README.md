# 导览「采纳」那一站，改前改后三张图，2026-10-23

这三张是 PR #4706 的对照截图。同一台沙箱、同一份 Chromium、同一段假数据，唯一变的是
`frontend/src/components/TopicAcceptCard.vue` 的哪一版：`before-main.png` 用主干
（`446dd248d`）那一版，另两张用这一版。

| 文件 | 那一刻 | 看到什么 |
|---|---|---|
| `before-main.png` | 卡刚递来，合并态 `unknown` | 浮窗圈住横条让人点「采纳」，横条上只有「状态更新中」 |
| `after-card-still-settling.png` | 同一刻，改后 | 浮窗不圈横条，说「做完会把结果交给你审阅」 |
| `after-buttons-out.png` | 合并态收敛成 `clean` | 横条上有「退回 / 采纳并完成任务」，浮窗这时才圈它 |

## 怎么重拍的

挂的是仓库里真的 `TopicAcceptCard.vue` 和真的 `GuideBubble.vue`，不是照样式画的假界面；
假的是接口——`/topics/k1/accept-card` 回一张钉死的卡，`/pr-checks`、`/review-comments`、
`/users/me/invites` 回空。最小入口只有三个文件：一个 `shot.html`、一个
`src/temp-shot/main.ts`（装 `@/plugins/vuetify`、i18n、pinia，注册 `v-guide-anchor`，
把 `fetch` 换成桩）和一个 `src/temp-shot/Scene.vue`（任务页骨架 + 真的卡 + 真的浮窗，
浮窗的目标按 `AppShell.vue` 的 `guideStep` 同一套逻辑算）。它们随这一轮一起删掉了，
要重拍就照上面重新拼一份。

浏览器在标准沙箱里要自己补齐运行库：`pnpm dlx playwright@1.59.1 install chromium`
下 headless shell（注意 `PLAYWRIGHT_DOWNLOAD_HOST` 指的镜像当时坏了，要临时去掉它走官方源），
缺的 `.so` 从 `mirrors.tuna.tsinghua.edu.cn/debian` 下 deb 用 `dpkg -x` 解到一处，
`LD_LIBRARY_PATH` 指过去；中文要 `NotoSansCJKsc-Regular.otf` 加一份 `FONTCONFIG_FILE`，
否则字全是方块。截图命令本身是一条
`chrome-headless-shell --headless --no-sandbox --window-size=1280,860 --screenshot=… --virtual-time-budget=9000`。

那两张改后的图是 1280 宽；窗口、缩放、字体一致，所以三张可以直接对着看。
