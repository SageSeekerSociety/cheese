# AI 队友界面交互实测报告（report-A / work-6b91c1e4fa9c4c10ae64f32b84fc3791）

## 0. 环境与运行资源

- 检出：`/tmp/ui-verify/repo`，HEAD = `66f41abcbe3f2a197179b87f7c75e0efb7f54d1c`
- 前端：`http://localhost:3100`（vite，pid 3497568/3497616，env `BACKEND_URL=http://127.0.0.1:8791`）
- 后端：`http://127.0.0.1:8791`（`uvicorn app.main:app`，pid 4039075，cwd `/tmp/ui-verify/repo/backend`）
- DB：postgres `127.0.0.1:5433`，库 `ui_verify`（`DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:5433/ui_verify`）；Redis `6380/3`
- 测试角色：`alice` / `demo12345`（种子账号，见 `e2e/tests/helpers.ts`；`alice` 拥有演示项目「知是 2.0 融合演示」）
- 浏览器：Playwright chromium 1.59.1（`e2e/node_modules`），`locale=zh-CN`，headless
- 截图与脚本目录：`/tmp/ui-verify/shots-A/`（未改动 repo 内任何代码，未重启任何进程）

## 0.1 前置判断：这份实例里「芝士跑完一轮」的链路**不通**（先说了）

三项独立证据，都在界面上/进程上实测到：

1. 后端进程真实 env（`/proc/4039075/environ`）：`ANTHROPIC_BASE_URL=`（空）、`ANTHROPIC_AUTH_TOKEN=`（空）、`AGENT_MODEL=glm-5.2`。`backend/.env` 注释原文：
   > leave the token empty and agent turns are simply unavailable — nothing else breaks.
2. 数据库计数（库 `ui_verify`）：`agent_turns=0`、`ai_message=0`、`ai_conversation=0`、`deliveries=0`（`agent_instances=3`，即已有 3 个 AI 队友定义，但从未跑过任何一轮）。
3. 我用真实浏览器在话题房里 @ 芝士 发了一条最简单的任务，界面**自己**报了这一轮没跑完（原文见第 2 项）。

结论：模型网关与任务机器都不可用 → 第 2–5 项无法在「跑通一轮」的前提下核验，按任务要求标「无法确认」，**不用报错界面去推断正常行为**。

---

## 1. 从哪里进入「修改 AI 队友」 —— 【验证状态：已实测】

### 真实入口（完整点击路径）

**主路径（房间内）：**
1. 登录 `http://localhost:3100/account/signin`（用户名/密码/勾选协议/「立即登录」）→ 落到「我的工作」
2. 左侧 rail 点项目磁贴（`alice` 的第一个项目 = 「知是 2.0 融合演示」）→ 进入项目看板
3. 点某个话题行（`.topic-row`，本例「搭建第一个原型」）→ 进入话题房
4. 点**项目标题右边的 ⌄ 按钮**（DOM：`.rail-header__more`，`title="项目菜单"`，`aria-label="项目菜单"`）→ 弹出下拉菜单
5. 菜单里点 **「项目设置」**（`mdi-cog-outline`）
6. 进入 `/projects/<id>/settings`，页面标题 **「项目设置」**，副标题「这个项目的队友、运行环境、交付规则和仓库连接」
7. 找到 **「队友」** 段（小节 eyebrow 为 **「AI 队友」**，右侧按钮 **「新建队友」**）
8. 队友卡片上点 **「编辑」** → 弹出对话框，标题即 **「修改 AI 队友」**

**备用路径（成员页）：** 侧栏「成员」→ 成员页「**AI 队友 · N**」段，每个队友行有按钮 **「设置」**（另有一颗「私聊」），点「设置」同样跳到项目设置，再走第 7–8 步。

### 界面原文（照抄可见文本）

下拉菜单项（照抄，顺序即所见）：`看板` / `日历` / `项目设置` / `转让项目`

项目设置页（「队友」段）：
```
队友
AI 队友            新建队友
每个队友有自己的角色设定和自己的记忆。新开话题默认交给标了「默认」的那一个，也可以在话题里单独换
芝士 | 芝士 | 默认 | @cheese · 通用        编辑   停用
0 条记忆
默认模型   模型设置
AI 队友默认使用项目主模型，也可以为每位队友单独指定
```

「修改 AI 队友」对话框（全部字段照抄）：
```
修改 AI 队友
名字
标识 · cheese
角色设定（可留空）
为这个队友指定模型
不指定时使用项目主模型
修改只影响这个队友，从下一轮开始生效，已有记忆保留
取消    保存
```

### 操作结果 / 限制
- 上述点击全部成功，对话框标题实测取到 `"修改 AI 队友"`。
- 限制：未保存（不发无意义的写操作）。小组件路径 `/projects/<id>/agents` 已废弃，会重定向到 `project-settings`（源码 `frontend/src/router/workspaceRoutes.ts`），界面上看不到该入口。
- 截图：`06-project-menu.png`、`07-project-settings.png`、`08-agent-editor-dialog.png`（另有 `02-project.png`、`03-topic-room.png`、`11-room-full.png`、`13-room-detail.png`）

---

## 2. 让芝士正常完成一轮 —— 【验证状态：无法确认（链路不通，已实测失败）】

### 我实际做了什么
登录 `alice` → 进项目 → 进话题房「搭建第一个原型」→ 在输入框输入 `请回复一句话：收到。(<ts>)` → 点 **「交给芝士」** 按钮（`aria-label="交给芝士（也可以直接按 ⌘/Ctrl+Enter 发送并交给它）"`，`.summon-btn`；按钮把 `@芝士 ` 写进正文）→ 回车发送。发送时间 `2026-09-23T11:30:08Z`。

### 操作结果（界面原文）
发送后输入框内容变成 `@芝士 请回复一句话：收到。(1790163007629)`，消息进时间线。约 8 秒内出现芝士侧的**系统行**（`data-testid="platform-notice"`，class `sys-row sys-row--warn`），原文：

```
芝士
运行状态   04:30
芝士这轮没跑完：这条会话的机器尚未配置或未连接      待人工处理
```
展开「详细说明」后原文：
```
稍后再 @ 它重试。

服务原话：
这条会话的机器尚未配置或未连接
```

此后 90 秒内（T+8/20/35/55/90s）界面**没有任何变化**：没有进度条、没有「施工中」任务、没有产物、没有回复，「看板」一直停在 `暂无派出去的任务`。90 秒后仍无动静。

### 因此无法确认的部分
- **启动后的进度提示**：未复现（本轮从未真正开始）。
- **怎样判断这一轮已完成**：未复现。
- **结果首先出现在哪里 / 用户接下来能做什么**：未复现；对话/文件/改动/预览四个入口**都没走到**。
  - 旁证（不算结论）：话题的预览接口 `GET /api/topics/<id>/preview` 返回 **422**，控制台可见 `Failed to load resource: ... 422`。这只是预览不可用的现象，不能说明正常完成后的行为。

### 限制
- 阻断点在两层：(a) 模型网关未配置（`ANTHROPIC_BASE_URL` 空）；(b) 界面自报「这条会话的机器尚未配置或未连接」。界面里「运行环境」选择器显示 `自有设备 · 自动选择 [项目默认]`，用量卡显示 `#7432a9 已连接`，但轮次依然起不来。
- 未尝试连接机器/改配置（会干扰环境）。
- 截图：`09-room-before-send.png`、`10-after-8s.png`（=T+20/35/55/90 内容相同）、`11-room-full.png`

---

## 3. 执行中发消息 / 补充 / 改方向 —— 【验证状态：无法确认（执行中）；仅记录到闲时反馈】

要求是「芝士**执行中**」。本轮从未进入执行态，因此**发送时机不在执行中**，无法核验「补充影响当前轮还是下一轮」。标「无法确认」。

闲时实测到的相关反馈（**仅闲时**，不代表执行中行为），供参考：
- **带 @ 的任务消息**：进时间线，出现上面的「运行状态」失败系统行。
- **普通消息（不带 @）**：进时间线，并在其下方出现一条提示行，原文：
  ```
  这条没叫芝士，它不会现在动
  让它现在就动
  ```
  （源码 `frontend/src/components/room/RoomMessage.vue:197` 的 `summon-hint-text`；「让它现在就动」是一个只对最后一条消息显示的补交按钮。）
- 输入框旁按钮（`.composer button`）实测只有三颗：`上传文件（每个最大 10MB）` / `交给芝士（…⌘/Ctrl+Enter…）` / `发送`。

截图：`12-after-plain-message.png`

---

## 4. 执行中是否有停止 / 中断入口 —— 【验证状态：无法确认】

- 无法进入执行态，故无法实测「正常执行时」是否出现停止入口，也无法实测「操作后任务与界面变成什么状态」。
- 页面全文检索：话题房当前状态下 `停止` / `中断` / `取消` 出现次数均为 **0**（即此刻没有渲染任何停止控件）。
- 旁证（源码，**非界面实测**）：`frontend/src/components/AgentControls.vue` 存在按钮 `中断当前任务`（调用 `run({ subtype: 'interrupt' })`）与 `停止`，但它在本话题房里以 `questions-only` 模式挂载（`frontend/src/components/ChatPanel.vue:1389` `<AgentControls ... :pushed="agentControl" questions-only />`），并且受 `state?.connected || busy` 门控——本轮未连接，按钮未渲染。**因此不能据此断言运行时会怎样。**

---

## 5. 芝士主动请求确认 / 补充信息 —— 【验证状态：无法确认】

- 无法稳定复现（本轮从未跑起来，芝士没有任何输出，谈不上请求确认）。
- 旁证（源码，非界面实测）：该交互应由 `AgentControls.vue` 的 `questions-only` 模式渲染（ChatPanel 在房间内以该模式挂载它），但整个会话里我从未在界面上看到任何提问/确认控件。按任务要求，不推断正常完成后的行为，标「无法确认」。

---

## 附：证据清单（/tmp/ui-verify/shots-A/）
- `01-home.png` 登录后「我的工作」
- `02-project.png` 项目看板
- `03-topic-room.png` 话题房
- `06-project-menu.png` 项目菜单（含「项目设置」项）
- `07-project-settings.png` 项目设置页「队友 / AI 队友」
- `08-agent-editor-dialog.png` 「修改 AI 队友」对话框
- `09-room-before-send.png` 发送前
- `10-after-8s.png` 发送后 8s（失败系统行已出现）
- `11-room-full.png` / `12-after-plain-message.png` / `13-room-detail.png` 其它状态
- 脚本：`probe1.mjs`…`probe5.mjs`
