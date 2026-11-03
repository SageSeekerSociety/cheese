# 「任务页」那一站的导览：改前 / 改后

四张图，同一台机器、同一个浏览器（Chromium 156）、同一份真前端（本机 `vite` 指向
`https://okcheese.com/api`，用开发号登录），导览真的停在 `taskPage` 那一站。

| 文件 | 宽度 | 这一版的行为 |
|---|---|---|
| `before-desktop.png` | 1440×900 | 主干（没有 `areas`）：圈住任务页顶部那一条标题栏 |
| `after-desktop.png` | 1440×900 | 本分支：不画圈，浮窗落在页面上 |
| `before-phone.png` | 390×844（DPR 2） | 同上，手机宽度 |
| `after-phone.png` | 390×844（DPR 2） | 同上，手机宽度 |

## 怎么测的

- 前端：任务目录里 `pnpm exec vite --port 5199 --host 127.0.0.1 --strictPort`，
  `VITE_API_BASE_URL=/api`，`BACKEND_URL` 指向本机一层保留 `/api` 的小反代
  （vite 的 dev proxy 会剥掉一层 `/api`，okcheese.com 的外网入口不剥）。
- 登录：开发号，Playwright 把 `accessToken` / `user` 写进 `localStorage`，并把
  `POST /api/users/auth/refresh` 换成直接回同一对凭据（否则启动时 401 会清掉会话）。
- 停在那一步：`localStorage['cheese.tour.stop'] = 4`（第 5 站 = `taskPage`），
  并把 `GET /projects/<id>/start-steps` 换成 `learned: false`（这个人还没走过引导）。
  事实由组件问回来时写进引导，与真跑一遍引导到这里是同一个状态。
- **改前/改后是同一份构建**：`after` 就是工作区这一版；`before` 用
  `page.route('**/src/composables/useProjectTour.ts*')` 取 vite 转换后的模块文本，
  把这一版新增的那一行 `areas: ['task-page'],` 去掉再喂回去——即主干那一版的行为。
  脚本会打印 `stub: old` 或 `!! STUB MISS`，落空时不会有图。
- 图里用量出来的框核对（脚本打印）：改前 `.gb__ring` 与 `.task-header` 是同一个框
  （桌面 1107×60 对 1095×48，多出的 6px 是圈自己的留白），改后没有 `.gb__ring`，
  浮窗仍在页面上（桌面 280×125 @ 753,91；手机 280×125 @ 59,66）。
- 两张图都裁掉了「连接断开，正在自动重连」那条提示（它属于本地反代，不属于这次改动）：
  用 CSS 把 `.v-alert` / `.v-snackbar` 隐藏，没有动页面结构。

## 不是整天引导全程

测的是「任务页」这一站本身，不是从一个新账号把整条 7 站引导走完（那需要新账号与
真芝士跑一轮）。这一站的两版差异就是这次改动，其余各站不在图里。
