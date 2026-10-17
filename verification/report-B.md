# AI 队友界面交互实测 — 报告 B

## 0. 元信息（被测对象与资源）

| 项 | 值 |
|---|---|
| HEAD | `66f41abcbe3f2a197179b87f7c75e0efb7f54d1c`（= upstream/main，2026-09-23 10:07，commit "fix(auth): enforce unique account identifiers and one username rule (#1498)"） |
| 独立实例检出 | `/tmp/ui-verify/repo` |
| 被测前端 | `http://localhost:3100`（vite dev；已核实其进程 env `BACKEND_URL=http://127.0.0.1:8791`，把 `/api`、`/users` 反代到 8791） |
| 被测后端 | `http://127.0.0.1:8791`（`uvicorn app.main:app`，pid 4039075，cwd `/tmp/ui-verify/repo/backend`） |
| 数据库 | postgres `127.0.0.1:5433`，库 `ui_verify`。真实用户表是 `"user"`（12 行）+ `user_profile`；`users` 表为空（勿用） |
| 登录页 | `/account/signin`（表单 label：用户名 / 密码 / 「同意 用户协议 和 隐私政策」/「立即登录」；新建 context 默认英文，用 locale=zh-CN 得中文） |
| 测试账号 | 全部 demo 账号共用密码 `demo12345`（见 `e2e/tests/helpers.ts`）：alice(1) 是项目 owner+lead；bobby(2)、carol(3) 等 |
| 测试项目 | 「知是 2.0 融合演示」 id `3ddb2e1a-fc13-48ac-84cd-2bd6bffbe490`（三个项目都归 alice） |
| 工具约束 | 内置 Bash/Read/... 不可用，全部经 `mcp__native__invoke`；只读，未改 `/tmp/ui-verify/repo` 代码，未重启任何进程 |
| 我的产物 | 脚本 `/tmp/ui-verify/shots-B/verify*.mjs`，原始输出 `out.txt`/`out6.txt`/`out7.txt`，截图 `/tmp/ui-verify/shots-B/*.png` |

角色枚举（后端 `ProjectRole`，前端 `labels.ts`）：`lead`=组长、`mentor`=导师、`member`=成员。

---

## 第 6 项（原清单第 41 项）运行资源切换 — **无法确认**

### 这个版本里「运行资源」叫什么、入口在哪
- 界面主名是**「运行环境」**：话题头「更多」菜单里选择器标题为「选择运行环境」。
- 表单/下拉的字段 label 叫**「运行资源」**（组件 `ComputeChoiceForm.vue`）。
- 项目设置页叫**「运行环境 / 默认与常用算力」**。
- 后端类型是 compute profile（`profile: "cloud" | "device"`），接口 `GET /projects/{id}/compute-profiles`、`GET /topics/{id}/compute-profile`。

### 真实入口（完整点击路径 + 原文）
1. 登录 alice → 打开项目「知是 2.0 融合演示」→ 打开话题「搭建第一个原型」。
2. 话题头右侧「**更多**」按钮（`aria-label=更多`，图标 mdi-dots-horizontal）→ 菜单里出现一行「**运行环境**」，其 chip 文本「**自有设备 · 自动选择 / 项目默认**」。
3. 点该 chip → 弹出「**选择运行环境**」菜单。
4. 另一入口：项目设置 `/projects/{id}/settings` → 区块「**运行环境 / 默认与常用算力**」。

### 界面原文（照抄可见文本）
- 菜单：`选择运行环境` / `仅影响当前房间，首次运行后固定` / `自有设备 · 自动选择` `项目默认` `首次运行时选择在线设备` / `其他配置与设备`
- 展开「其他配置与设备」后：`运行资源` / `暂无可用资源，请在团队算力页添加设备或接入云服务` / `用于当前房间`
- 项目设置页：`运行环境` / `默认与常用算力` / `房间直接使用项目默认；临时选择只影响当前房间。已运行的房间保留原环境` / `自有设备 · 自动选择` `项目默认` / `首次运行时选择在线设备` / `添加常用配置` / `尚未接入机器`
- 话题头 chip：`自有设备 · 自动选择` / `项目默认`

### 操作结果
- 选择器里**没有任何第二条可选的档**：只有一条「自有设备 · 自动选择」的当前值显示行，加一个「其他配置与设备」（用来展开配置表单）。点「其他配置与设备」只把表单展开，而表单里的「运行资源」下拉**是空的**（`暂无可用资源…`）。
- 点后复查 `GET /api/topics/{id}/compute-profile`：`current` 仍为 `"device"`，`locked:false`，**未发生任何切换**。

### 可用运行资源（均为 0 / 不可用）
- `GET /api/topics/{id}/compute-profile` → `profiles`:
  `[{id:"device", label:"自托管设备（我的机器）", available:**false**}, {id:"cloud", label:"Cloud", available:**false**}]`，`devices:[]`，`current:"device"`，`locked:false`
- `GET /api/projects/{id}/compute-configs` → `{"cloud_available":false,"devices":[]}`
- DB：`device`=0 行，`project_machines`=0 行，`compute_grants`=0 行

### 「首次运行前 / 后」为什么测不了
因为**没有任何可用机器**，话题里的 agent 轮根本跑不起来。话题内可见原文（芝士运行状态）：
> 「芝士这轮没跑完：**这条会话的机器尚未配置或未连接**」

所以「首次运行前」能看到选择器、「首次运行后」被锁定，这一对照**无法实测**。

### 限制 / 未验证
- 只有 0 个可用档（两个列出的 profile 都 `available:false`），无法做两档之间的实测切换。
- 源码中「首次运行后锁定」的路径（`locked = AgentSessionService.has_run(topic) or 活动机器`，锁定时渲染带锁 chip，title 文案「运行环境已固定，新建房间可另选配置」）**我未实测**，仅记录源码所见，不用失败界面推断其行为。

### 验证状态：**无法确认**
截图：`F1-worktopic.png`、`F2-more.png`、`F3-picker.png`、`F4-after-row1.png`、`E5-project-settings.png`

---

## 第 7 项 导师 vs 普通成员权限对照（同一项目） — **已实测**

### 角色账号怎么造的
- 在**同一个项目**「知是 2.0 融合演示」(`3ddb2e1a…`) 里，用 lead alice 的身份调成员接口原语 `POST /api/projects/{id}/members`，把 `bobby` 设为 **mentor(导师)**、`carol` 设为 **member(成员)**。
  - 返回：`bobby role=mentor` 200；`carol role=member` 200。
  - 说明：源码注释称 `POST /members` 是「脚本和测试用的原语」；界面上的加人走「**邀请成员**」对话框（按 uid 邀请、被邀者接受才加入）。为避免跨账号接受流程，我用了原语；界面邀请对话框入口与原文一并记录如下。

### A. alice（owner + lead）成员页 `/projects/3ddb2e1a…/members`
- 可见按钮：**「转让项目」「邀请链接」「邀请成员」**；行内管理按钮 `aria-label=管理成员`（计 2 个，对应 Bob、Carol）。
- 行内「管理成员」菜单原文：**`角色` / `设为组长` / `设为导师` / `设为成员` / `移除项目`**。
- 「邀请成员」对话框原文：**「邀请成员 / 填对方的 uid（个人主页地址里那个数字）。邀请发出去之后，要他自己接受才算加入——进来之后他能看到这个项目的全部话题 / uid / 角色 / 取消 / 邀请」**。
- 分组：`组长 · 1`（Alice `所有者`）、`导师 · 1`（Bob）、`成员 · 3`（Carol、David、Eve）；David/Eve 标「来自小队 · 在小队中管理」（source=team，不可被改角色/移出）。

### B. bobby（导师 / mentor）成员页
- 「邀请成员」按钮 **0** 个、「管理成员」按钮 **0** 个；只有「**退出项目**」。
- 页面文案：**「改角色和移除成员由组长来做」**。
- 越权实测（UI 无入口，改调 API）：
  - `PUT /api/projects/{id}/members/carol {role:mentor}` → **403 `只有项目的 owner / lead 能管理项目成员`**
  - `POST /api/projects/{id}/members {user_handle:david}` → **403 `只有项目的 owner / lead 能管理项目成员`**

### C. carol（成员 / member）成员页
- 「邀请成员」**0**、「管理成员」**0**，同样只有「退出项目」，文案同上。
- 越权实测：
  - `PUT /api/projects/{id}/members/bobby {role:member}` → **403 `只有项目的 owner / lead 能管理项目成员`**
  - `POST /api/projects/{id}/members {user_handle:david}` → **403 同上**

### 结论
在这个版本里，**导师(mentor) 与普通成员(member) 在项目管理 / 成员管理上完全没有差别**：两者都看不到任何管理入口，越权调用都被 403 拦为同一句原文。权限只属于 owner/lead（前端判据 `canManage = me==owner || myRole=='lead'`，后端 `MemberService` 同判）。
限制：导师与成员权限相同这一条是实测；我未构造「导师是否有 lead 不具备的某项能力」之外的差异项。

### 验证状态：**已实测**
截图：`A2-members-lead.png`、`A3-members-after-add.png`、`D1-manage-menu.png`、`D2-invite-dialog.png`、`B1-members-mentor.png`、`C1-members-member.png`

---

## 第 8 项 GitHub OAuth 连接账号/选仓库流程 — **无法确认（未配置）**

### 「是否已配置」的证据（全部指向未配置）
1. `GET /api/users/auth/oauth/providers` → **`{"code":200,"message":"OK","data":{"providers":[]}}`**（空列表）。
2. 登录页 `/account/signin` 只有「用户名 / 密码」和「**通行密钥登录**」，**没有 GitHub 按钮**（截图 `A0-signin.png`，文本已照抄）。
3. 项目设置页存在入口按钮「**连接 GitHub 账号**」；点它之后页面出现原文 **`OAuth provider 'github_app' not found`**（后端 `app/domain/oauth/services.py:390` 抛出）。
4. 被测实例 8791 的 `backend/.env` **没有任何 `OAUTH_*` / `GITHUB_APP_*` 键**；其进程 env 也没有——配置字段是 `oauth_enabled_providers` / `oauth_github_client_id` / `oauth_github_app_client_id` 等。
5. `GET /api/projects/{id}/github/install-url` → **403 `请先在项目设置中连接或重新连接 GitHub 账号，再连接仓库`**；`GET /api/projects/{id}/github/connection` → `{"connected":false}`。
6. DB：`user_o_auth_connection` 0 行；`project_git_installations` 0 行。

### 对照（非被测，防止误判）
另一个**不属被测对象**的实例 `cheese-backend-1`（`:8081`/`:18081`）返回 `providers=[{"id":"ruc","name":"微人大"}]`，说明「空 providers」是被测实例 8791 自身的事实，不是接口坏了。

### 结论 / 限制
被测实例没有配置 GitHub OAuth / GitHub App，登录页无 GitHub 入口，「连接账号 → 选择仓库 → 完成连接」这条流程**进不去**，无法核验其界面原文与结果。GitHub 侧只有「连接账号」按钮存在，点下即失败。

### 验证状态：**无法确认**
截图：`A0-signin.png`、`G1-github-connect.png`、`E5-project-settings.png`
