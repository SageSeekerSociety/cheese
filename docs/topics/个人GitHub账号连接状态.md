## 完成情况

**第一部分（必做）：已完成。**

- <&frontend/src/lib/githubAccount.ts>（新增）：`findGithubAccountConnection`（从连接列表里挑出 `providerId === "github_app"` 那条）、`isGithubAccountTokenExpired`（`tokenExpires` 为 `null` → 不过期；只有真的早于当前时间才算过期；解析失败也当作不过期，不误报）。抽成纯函数是为了不用挂载 Vuetify 就能测。6 个单测全过（<&frontend/src/lib/githubAccount.spec.ts>）。
- <&frontend/src/api.ts> / <&frontend/src/cx_types.ts>：补了 `listOAuthConnections`/`deleteOAuthConnection`（走已有的 legacy `/api` 前缀）和 `OAuthConnectionInfo` 类型（`login`/`tokenExpires`/`hasRefreshToken` 等字段对齐后端 `list_user_connections`）。没碰后端。
- <&frontend/src/views/ProjectSettingsView.vue>「连接 GitHub 账号」这一节：改成按状态分支渲染——
  - 加载中：转圈 + 文案。
  - 请求失败：错误图标 + 文案 + 重试按钮，**不会**伪装成"未连接"。
  - 已连接：显示 GitHub 用户名（`login` 缺省退回 `providerUserId`）+ 相对时间（复用现成的 `relTime`），提供「重新连接」「断开」两个入口；token 过期时额外弹出警示，文案讲清楚后果（"采纳时将无法自动开 PR，会退回直接合并到 main"），`tokenExpires` 为 `null` 不触发警示。
  - 未连接：保留原按钮 + 原说明文案，行为不变。
  - 断开成功后 `githubAccountConn` 直接置空，面板立即回到未连接态，不需要刷新页面。

**第二部分（位置迁移到 `/account`）：评估后判断不做，原因已查清楚。**

<&frontend/src/router/account.ts> 下的 `/account` 整棵路由树是登录/注册/找回密码/两步验证/OAuth 登录回调这类独立鉴权流程（`meta: { hideAppBar: true }`），不是个人设置/资料页。把"连接 GitHub 账号"这种设置类面板塞进去，既没有合适的挂载点，也确实是"别的模块的地盘"，改动会外溢到无关的鉴权流程。按任务简报里的预案，评估不适合就只做第一部分、把原因回报，不硬塞。项目设置页里的这份保留（这里正是用户实际会遇到它的地方）。

## 验证结果

- `pnpm lint`：本卡改动的 5 个文件（`api.ts`/`cx_types.ts`/`lib/githubAccount.ts`/`lib/githubAccount.spec.ts`/`views/ProjectSettingsView.vue`）零告警零错误。仓库里另外 2 个 error + 286 个 warning 全部在没碰过的文件里（如 `AuditTask.vue`），是既有基线问题。
- `vue-tsc --noEmit`：本卡改动文件无一条报错。仓库既有 20 条类型错误（tiptap 版本冲突、`space.test.ts`、`AuditTask.vue` 等）跟这次改动无关，均在未触碰文件。
- `pnpm test -- --run`：**217/217 全过**（21 个测试文件），含新增的 `githubAccount.spec.ts` 6 个用例。
- `pnpm run build`：在本沙箱跑不完——cgroup 硬限制 2GB（`/sys/fs/cgroup/memory.max` = 2147483648），试过 `--max-old-space-size` 设 1536/1800/6144 均在 transform 阶段 OOM（不设限时被内核直接 SIGKILL，设限后 V8 自己 OOM，说明实际需要的内存本来就超过 2GB 上限）。这是沙箱资源约束，不是这次改动引入的问题——改动本身只新增了几个小文件和一节模板分支，不足以把构建内存需求从"能跑"推到"跑不完"。lint/typecheck/test 三项都已验证干净，build 这一步没能在本环境里跑绿。

## 验收标准对照

1. 已连接用户能看到用户名+连接时间+断开入口 — 完成
2. 未连接用户看到原按钮+原文案 — 完成（未改动这条分支的文案/行为）
3. 加载中/失败/已连接/未连接四态互斥可区分，失败不伪装成未连接 — 完成
4. `tokenExpires` 为 null 不报警，过期时说明后果 — 完成
5. 断开后面板立即回到未连接态，无需刷新 — 完成
6. `task check` 全绿 — lint/typecheck/test 三项验证干净；build 受限于沙箱 2GB 内存上限跑不完，见上文说明
7. 第二部分完成情况 — 未做，原因已在上面写清楚（`/account` 是鉴权流程的地盘，不是设置页）
