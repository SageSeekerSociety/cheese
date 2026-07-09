# 融合设计：cheesex 主干 + cheese-agent-layer 精华

> 决策汇编（2026-07-09，andyl 逐条拍板）。背景：主项目仓 `cheese-backend-py` 上有队友的
> `design/cheese-agent-layer` 分支（agent 层设计，六幕基本建完）。我们的 `cheesex`（dogfood
> 平台）代码更好、产品面更全。**方针：以我们为主干，选择性嫁接他们后端的精华，前端全保留我们的，
> 最终 merge 进主 repo cheese-backend-py（我们代码为准）。**
>
> **执行模式**：一阶段一阶段自主推进（P0→P4→收敛），不问、不等协调——我们有全部权限。
> 每阶段用 subagent 并行攻坚 + 联检 + 提交。所有实现**用我们的代码**。

## 0. 一句话公式

> 我们的主干（前端 + 产品后端 + **tmux/hooks 感知**）
> ＋ 嫁接他们的 [agent-as-user 身份] ＋ [权限纪律] ＋ [self-hosted 设备流骨架 cli]
> ＋ 话题=群聊（我们的选择，非他们的 thread/workitem 拆分）
> ＋ merge 进主 repo（我们代码为准）
> ＝ 用 hooks 换掉他们的读屏，取其骨架去其脆弱点。

## 1. 感知层：hooks，不是读屏（决定性，保留我们的）

- 他们 `cheeselets/claude.js`：正则匹配屏幕尾部推断 busy/idle/choices，注释满是
  "verified against real v2.1.x screens"——TUI 改版即碎。
- 我们 tmux/hooks 后端（已建，`TmuxHooksProvider`）：Claude Code hooks 吐结构化 JSON
  （SessionStart/PreToolUse/MessageDisplay/Stop → AgentEvent），连控制态都不猜。
- **融合时用我们的 hooks 感知替换他们的读屏**，消除他们最大脆弱点。这也让 self-hosted 更稳
  （hooks 在任何机器一致，读屏依赖具体 TUI 版本）。

## 2. agent-as-user 身份（嫁接，地基级）

- 他们：唯一身份类型 `user`；人和 agent 只有**登录方式**之别（人用密码/passkey，agent 用
  连接器铸的 session token）；业务代码永不 `if is_agent`；"是不是 agent"是**派生**的
  （有无 live 执行绑定），不入库。
- 我们现状：芝士是特殊作者 `CHEESE_AUTHOR` + 轻量 profile/role。
- **改造**：把"芝士特殊作者"重构成"agent = 一个真 user 行"。收益：多 agent、权限、agent 被
  邀入群（需 owner 同意）全部变干净。平滑迁移：先建 agent-user 行、CHEESE_AUTHOR 映射到它，
  再逐步把作者字段从字符串 handle 迁到 user_id。
- **前端不受影响**（作者仍是名字/头像，只是底层从字符串变 user_id）。

## 3. 话题 = 群聊（我们的选择，与队友不同）

队友把 thread（群聊，项目无关）和 workitem（事项）拆两层。**我们不拆**——话题本身就是群聊房间：
一个话题 = 一个群（成员 + 多 agent + 表情 + ＠all）＋ 一份文档 ＋ 工作状态。理由：保住我们
"文档是核心界面、对话是过程"的产品魂，不像他们把聊天彻底剥离。

要补的功能（按优先级）：
1. **表情回应前端 bug 修复**（已知有 bug，先修）。
2. **话题成员名册**：成员（人 + agent）、加/移除、角色（owner/admin/member）。群聊感的地基。
3. **@all / @here**（现只有 @单人）。
4. **多 agent 就绪**：agent 作为话题成员，可被 @、可邀请（接 §2 agent-as-user）。
5. 飞书进阶：回复串 UI、置顶、presence/正在输入、已读回执、转发、富卡片。

借鉴他们的：`thread_membership`（角色 + attention_policy_override）、成员同意入群
（invite 需 owner 批准，复用通知系统）——但落在**我们的话题模型**上，不新建 thread 表。

## 4. 权限纪律（嫁接纪律，非代码）

抄他们这套（`viewer_authz.py` / architecture §3）：
- **actor 在信任边界注入**，永不从 request body 读；
- **权限属于项目**：一个权限分享给项目，则项目内所有人/agent/自动化都有，可撤销；
- **组合式授权策略**：授权是纯策略函数 + 注入适配器（resolve_user/is_member/shares_thread），
  脱离 DB/WS 可单测；
- **"群是共享访问的单位"**：和 agent 同群者可看/操作其现场；
- 一个有效 token 是**必要非充分**——每次调用按 actor 真实权限授权。
- **待办**：对我们现有 authz（require_auth_user / 各 service 的权限检查）做一次审计，按这套
  纪律收敛。这是点4"仔细研究权限"的落点。

## 5. self-hosted / BYO 设备算力（战略，要做）

确认要做（self-hosted 应有之义）。他们的**设备流 + 瘦客户机**是把 agent 送上用户机器的机制：
- `domain/device`：设备流（start/approve/poll）、durable 设备 token、设备→项目绑定；
- frozen `cli/` Go 瘦客户机 + `link.Msg` 线协议：`curl … | sh` 装、device-flow 登录、
  常驻连接器；所有逻辑在后端，客户端永不因业务改动重装；
- 一个 agent = 一个屏幕（screen），screen token 归属调用。
- **我们的做法**：借鉴设备流/连接器把 agent 落到用户机器，但**感知走我们的 hooks**（不是他们
  的读屏 cheeselet）。算力模型二选一并存：平台容器（现状，`AGENT_BACKEND` 已支持切换）＋
  用户设备（新增 self-hosted provider）。
- 这是大件，单独分阶段，不是抄一段代码。

### 5.1 他们的 `cli/` 瘦客户机（读过代码，确实好——self-hosted 的现成骨架）

> 注意命名冲突：他们的 `cheese` = 连接器瘦客户机；我们的 `cheese` = 沙箱内平台动作 CLI。
> 同名不同物，真融合时须理顺（改一方的名）。

三个亮点，价值在 self-hosted 场景完全释放：
1. **`cheese api` 从服务器 OpenAPI 实时生成**（Restish）：每个服务器操作自动变 `cheese api
   <op>`，每次运行从 live spec 重建，客户端零业务逻辑、API 变了永不过时、永不需更新。
   对比我们手写硬编码的 cheese 子命令——值得借鉴"客户端 = 服务器 API 薄壳"的纪律
   （甚至可从 FastAPI OpenAPI 生成）。
2. **拨出式连接、穿 NAT**：`cheese` 总向服务器拨出（非入站），退避重连 + 心跳。用户笔记本躲在
   NAT/防火墙后也能托管 agent、零入站端口——**这是 BYO 设备算力最硬的骨头，他们干净解决了，
   直接借鉴。**
3. **frozen 瘦客户机 + `link.Msg` 版本化协议**：单一 JSON union 承载会话/变量/RPC/屏幕/exec，
   握手协商版本，客户端纯传输、永不因业务改动重装。
- **P3 定位**：到 self-hosted 阶段，他们的 `cli/` 是**最该整体拿来的骨架**（尤其拨出连接 +
  frozen 协议），把其中的读屏 cheeselet 换成我们的 hooks 感知即可。

## 5.2 cheese CLI 设计（用我们的代码，curated + raw 逃生口）

不整体照搬他们 OpenAPI 全生成的 `cheese api`。学 Notion 的分层，**在我们代码上**建：
- **顶级 curated 工具**（推荐路径，80%）：`cheese write` / `cheese ask` / `cheese title` /
  `cheese doc` 等明确指令——语义清晰、有校验、是芝士该用的一等接口（我们现有 cheese CLI 就是
  这个形态，保留扩展）。
- **raw API 逃生口**（20%，不推荐但可用）：`cheese api <op>`（可从我们 FastAPI 的
  `/openapi.json` 生成，同他们思路）**甚至直接 `curl` 打我们的 API**——相当于放行原始 API 调用，
  给需要时兜底。
- 纪律不变（规则4 + §4 权限）：无论走 curated 还是 raw，都在信任边界注入 actor、按真实权限授权；
  raw 逃生口不绕过鉴权。
- **必须用我们的代码实现**（不 wholesale 引入他们的 Go 客户端做平台动作层）；他们 cli 的价值在
  §5.1 的 self-hosted 连接器骨架，与这里的平台动作 CLI 是两回事（重名须理顺）。

## 6. 分身 / clone（纠正：另起会话是对的）

- **分身 = 标准 subagent（fresh 会话 + 任务简报）**，不 fork。依据：spec §8.4 分身专注、
  彼此不感知、靠文档对齐；Claude Code 默认 subagent 也是 fresh，委派 prompt 是唯一通道。
  我们"子话题带简报 + 父文档快照"就是对的，**不改**。
- **transcript-fork（他们 clone.py）是另一个功能**："整体克隆一个 agent"或"从当前状态并行
  探索"（对应 Claude Code `/fork`）。未来做"克隆 agent"时再嫁接；我们 tmux 后端的 Stop hook
  已给 `transcript_path`，届时白捡。

## 7. 同源共识（两边一致，无需融合）

OpenViking 记忆（viking:// L0/L1/L2）、万物皆块（reply_to 对话树 + struct_parent 文档树 +
block_ref）、改文档=下指令、人验收才算数——同源 spec。

## 8. 分期建议

- **P0（即刻）**：表情 bug 修复；成员名册 + @all/@here（话题群聊感）。
- **P1**：agent-as-user 身份重构 + 权限纪律审计。
- **P2**：现场升级为 ttyd 真终端镜像（已备，容器 7681 就绪）；多 agent 编排借鉴。
- **P3**：self-hosted 设备流 + 瘦客户机（战略大件，单独立项）。
- **P4**：clone agent（transcript-fork）。

## 8.5 仓库收敛（接入主 repo cheese-backend-py）

现状：主 repo `cheese-backend-py` 上有队友的 `design/cheese-agent-layer`（含 cli + 设备流 +
connector 服务器，self-hosted 服务器端已建完）。我们的 `cheesex` 是独立 dogfood 仓（产品面更全）。
**目标：我们的工作往主 repo 收敛，不再长期两仓并行。**

关键判断（实证：他们 cli `go build` 零改动即成、服务器端点仅 `/auth/device/{start,poll}` +
`/agent` WS + `/openapi.json`，后者 FastAPI 已免费提供）：
- **self-hosted 不在 cheesex 重造**——直接复用主 repo 已有的 cli + 设备流 + connector 骨架；
- 我们的贡献 = 把他们的**读屏 cheeselet 换成我们的 hooks 感知** + 带入我们的产品面
  （前端 / 记忆真集成 / 闸门 / 算力 / 群聊）；
- cli 唯一必改：与我们沙箱内 `cheese`（平台动作 CLI）**重名冲突**须理顺（改一方名）。

**做法**：不现在做 git merge——**一直在我们这一个分支上工作**，把主 repo/design 分支里我们没有
的骨架（cli 连接器 + 设备流 + agent-as-user + 权限 + 多 agent 编排）搬过来、用我们的代码实现，
**最终达到"可以 merge 进主 repo 的水准"**（我们代码为准、质量/完整度达标、命名理顺）。有全部权限，
自主分阶段推进，不等协调。

## 9. 与队友对齐

这份文档也是和 `design/cheese-agent-layer` 作者对齐"融合而非替代"的靶子。核心信息：
我们保留他们的骨架（agent-as-user / 权限 / 设备流 / 多 agent 编排），但**感知层用我们的
hooks 替换读屏**——那是他们代码里最脆弱、补丁最多的一块，换掉是净收益。
