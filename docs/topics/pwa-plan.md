# 目标

让 cheese 变成一个真正能"装到手机/桌面上用"的 PWA：能安装、图标像样、离线不白屏、有推送、小屏上能用。

一句话结论：**PWA 的技术底座已经在 main 上了，缺的是"看起来像 App"和"用起来像 App"这两层。** 剩下的活按价值排序是：安装引导 → 移动端布局 → 推送。

---

# 现状：已经做完的部分（都在 main 上，我逐条核实过）

| 能力 | 在哪 | 状态 |
|---|---|---|
| Service Worker + 离线缓存 | <&frontend/vite.config.ts> 里的 `VitePWA`（vite-plugin-pwa 1.3.0） | ✅ 完整 |
| SW 注册 | <&frontend/src/pwa.ts>，`main.ts:80` 调用 | ✅ |
| Web App Manifest | vite.config.ts 内联声明（name / short_name / standalone / theme_color） | ✅ 基础字段齐 |
| 图标 | <&frontend/public/> 有 pwa-192、pwa-512、pwa-maskable-512、apple-touch-icon-180 | ✅ |
| 离线导航兜底 | `navigateFallback: index.html` + denylist 保住 `/api/`、`/connector/`、`/users/` 和两个 live iframe | ✅ 而且考虑得很细 |
| 运行时缓存策略 | API GET 走 NetworkFirst（30 分钟窗口）、`/assets/` 走 CacheFirst、字体 CacheFirst | ✅ |
| 缓存的安全边界 | 不缓存 auth/oauth/refresh-token、不缓存带 `token=` 查询参数的 URL、不缓存 SSE 流、登出时清缓存 | ✅ |
| 自动更新 | `registerType: 'autoUpdate'` + skipWaiting + clientsClaim，发版后已打开的页面自己刷新 | ✅ |
| nginx 配合 | <&frontend/nginx.conf>：`sw.js` 和 `manifest.webmanifest` 都 `no-cache`，manifest 补了正确的 content-type | ✅ |
| 离线提示 | <&frontend/src/components/common/OfflineBanner.vue>，断网横幅 + 恢复自动重连 | ✅ |

**别重做这一层。** 这套配置里的每条注释都写明了取舍理由（为什么 Monaco worker 不进 precache、为什么 iframe 路径要进 denylist），改动前先读注释。

---

# 缺口：还差什么

## A. iOS 上装不成样子（成本最低，影响最大）

`index.html` 里只有一行 `apple-touch-icon`。iOS Safari **不完全认 manifest 的 `display: standalone`**，需要 Apple 自己那套 meta：

- `apple-mobile-web-app-capable` — 缺了这条，iOS 上"添加到主屏幕"后打开还是带 Safari 地址栏，跟浏览器书签没区别
- `apple-mobile-web-app-status-bar-style` — 状态栏配色
- `apple-mobile-web-app-title` — 主屏图标下面的名字
- splash screen（可选，缺了启动时白屏一两秒）

## B. 没有任何安装引导

全仓库 0 处 `beforeinstallprompt`（我 grep 过）。也就是说：**Chrome/Edge 能装，但用户完全不知道**；iOS 压根没有这个事件，必须手写"分享 → 添加到主屏幕"的图文引导。

## C. 没有推送（工作量最大，也是 PWA 相对网页最大的价值）

全仓库 0 处 `PushManager` / `VAPID` / `pywebpush` / `Notification.requestPermission`。

好消息是接入点现成：后端已有完整的 <&backend/app/domain/notification/> 域（models / publisher / handlers / dedup），推送只需要在 publisher 那条链上挂一个新的发送通道，不用重做通知模型。

已知约束：iOS 要 16.4+，而且**必须先"添加到主屏幕"才允许申请推送权限** —— 这反过来说明 A 和 B 是 C 的前置。

## D. 移动端布局基本没做（真正的大头）

`src/views` 下有 **110 个 .vue**，但只有 **5 个文件**用了 Vuetify 的 `useDisplay`（断点判断）。有一个 `MobileAppBar.vue`，但仅此而已。

意思是：现在就算装到手机上，打开也是一个被缩小的桌面站。**这块跟 PWA 技术无关，但决定成败。**

## E. manifest 缺进阶字段

- `id` —— 缺了它，以后改 `start_url` 浏览器会当成"另一个应用"，已安装的用户要重装
- `screenshots` —— Android Chrome 只有带截图才弹**富安装卡片**，否则是底部一条很容易被忽略的小提示
- `shortcuts` —— 长按图标的快捷入口（比如"我的话题"）
- `display_override`、`categories`

## F. 没有 PWA 的自动化验证

<&e2e/tests/> 只有 auth / smoke / topic-and-chat 三个 spec。SW 注册没起来、manifest 404、离线导航白屏，这些现在**只能靠人肉发现**。

注意：沙箱里 `pnpm run build` 必然 OOM（2GB 上限），所以 SW 的产物只能在 CI 上验证 —— 这条要写进任何相关子任务的简报里。

---

# 计划：分四段做

## P0 · 装得像个 App（小改动，1～2 天）

1. `index.html` 补齐 iOS meta 四件套 + splash
2. manifest 补 `id` / `screenshots` / `shortcuts` / `display_override` / `categories`（截图需要出素材）
3. 新增安装引导组件：Chrome 系接 `beforeinstallprompt` 存下事件、给一个"安装到桌面"按钮；iOS 检测到 Safari 且非 standalone 时给图文引导。**克制**：只在合适时机露出，不要一进站就弹
4. 加 `e2e/tests/pwa.spec.ts`：manifest 可达且字段正确、SW 注册成功、离线后导航不白屏

产出即可验收：手机上"添加到主屏幕"后是全屏应用，桌面 Chrome 有安装按钮。

## P1 · 移动端能用（大头，按页面切）

先定**最小可用集**，不要全站铺开：

- 登录 / 注册
- 项目列表 + 项目详情
- 话题房间（聊天面板 + 活文档）← 核心
- 通知收件箱

其余页面（终端、设备实时画面、复杂看板）在小屏上明确降级：给一句"这个页面请用电脑打开"，**不要装死**。

## P2 · 推送（价值最高，链路最长）

- **后端**：VAPID 密钥进 `app.core.config.settings`；新增 `push_subscription` 表 + alembic 迁移（注意 <&backend/alembic/HEAD> 单行冲突约定）；订阅/退订接口；在 notification publisher 上挂一条 web push 发送通道，复用现有的 dedup
- **前端**：权限请求 UI（在用户做了明确动作之后再问，不要冷启动弹）、订阅上报、SW 里加 `push` 和 `notificationclick`（点开直达对应话题）
- **前置**：依赖 P0 完成（iOS 必须先安装才能授权）

## P3 · 打磨

- 更新提示改成非强制：现在 `skipWaiting` + 自动 reload，用户正在编辑文档时会被直接刷掉。改成提示"有新版本，点击刷新"
- 多账号缓存隔离：现在 SW 缓存是 per-browser 不是 per-user，只靠 30 分钟窗口 + 登出清理兜着；不登出直接切账号仍可能串数据
- `share_target`：从系统分享菜单直接分享到某个话题

---

# 待办 / 需要拍板

- [ ] **@李甘** 确认从哪一段开始拆活。我的建议是 P0 先做（一天见效，且是 P2 的前置）
- [ ] P1 的最小可用集页面清单要不要按上面这四类定
- [ ] P0 的 `screenshots` 需要设计出图，或者先用真实截图顶上
