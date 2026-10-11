# AI 时代的材料：说明文件、文件描述、索引

方案页 `_plan/out.html` 里每一条外部结论的出处。这一轮只收 AI 时代的材料（官方 agent 文档、仓库先例、2026 年论文），不含语言文档规范那一套。

## 1. 官方推荐（Anthropic）

**`code.claude.com/docs/en/monorepo`（大仓库/单仓多包专页）** 推荐按目录分层放说明文件，并逐条给了配套设置：

- 从仓库根启动 → 只加载根文件，子目录文件「按需加载」；从子目录启动 → 加载该目录及其所有祖先。
- `claudeMdExcludes`：按 glob 排掉不碰的包/别的团队的子树（静态排除，非按任务切换）。
- `permissions.deny` 的 `Read` 规则挡住生成物、构建产物、vendored 代码。
- 代码智能插件（语言服务器）：跳定义、找引用，替代满仓扫描来定位符号——官方专门给 TS/Python/Go/Rust 等做了插件。
- 每目录 skills：`<pkg>/.claude/skills/`，按相关性按需加载。
- 「分层不够用时」把约定迁出常驻 CLAUDE.md，改成 skills / plugins / MCP 服务器。

**`code.claude.com/docs/en/memory#agents-md`** 给出加载细节：

| 你的仓库有 | Claude 读 |
|---|---|
| 一份 AGENTS.md，且工作目录（含上层）没有 CLAUDE.md / CLAUDE.local.md | 该 AGENTS.md |
| 一份 AGENTS.md，且同时有 CLAUDE.md 或 CLAUDE.local.md | 只读 CLAUDE.md |

- 子目录的 `AGENTS.md`：当 Claude 用 **Read** 打开该目录下的文件、且该目录自己没有那三种 CLAUDE.md 时加载。Bash 的 `cat`/`grep`、Grep 工具都不触发。
- 直接读 `AGENTS.md` 需要 Claude Code **≥ 2.1.277**；Bedrock 或关遥测的会话在 < 2.1.281 上读不到，只能靠 `CLAUDE.md` 里的 `@AGENTS.md` 导入。
- Project instructions 四个值：`claude-md-or-agents-md`（默认）、`claude-md-and-agents-md`、`claude-md`、`managed-only`。可在 `pluginConfigs` 下的 `cc-plugin-agents-md@builtin` 设定，只在 `~/.claude/settings.json`、`--settings` 文件、managed settings 生效，项目/本地设置里被忽略。
- 残留 `CLAUDE.local.md` 会让 AGENTS.md 被整仓忽略、静默无警告（issue #96117，5/5 复现，open）；对策是把 Project instructions 设为 `claude-md-and-agents-md`。
- 软链约束：Edit/Write 工具拒绝穿过软链写；`core.symlinks=false`（Windows）会把软链检出成纯文本。

**Gemini CLI（`GEMINI.md`）**：同样是有层级的上下文文件机制，名不同。说明多工具并存时「一份共享事实源」有现实需求。

## 2. 仓库先例

- **vercel/next.js**：`#88105`（2026-01-05）"Rename CLAUDE.md to AGENTS.md with symlink"；`#99043`（2026-09-22）"Use AGENTS.md for managed agent instructions"。分两步：先软链，后去软链。
- **AGENTS.md 数量**（`gh api .../git/trees/HEAD?recursive=1` 实测，2026-10-11）：apache/airflow 14、grafana/grafana 13、microsoft/vscode 8（根文件正文只有一句指针）、openai/openai-python 1。
- 采用率抽样（前一轮材料）：AGENTS.md 约占活跃仓库 6.2%，CLAUDE.md 5.4%——两种并存。

## 3. 实测效果（arXiv 2026）

**说明文件对「做对」无显著效果**

- **2607.27250**《Do Context Files Help Coding Agents? A Two-Agent Ablation Study on Real Repositories》——Claude Code + Codex，3 仓库 17 任务（15 共享 + 2 仅 Codex），288 次带金标测试的运行。上下文注入策略对两个 agent 的正确率影响都被夹在 ≤10–15pp 内。作者结论：agent 输在实现能力（功能设计、方案选择、接线），不是缺说明文件能提供的仓库知识；操纵探针显示真实 AGENTS.md 从未把一次 near-miss 变成 pass。

**文件/实体描述对「找对文件」有实测效果**

- **2607.11046**《Retrieval-Oriented Code Representations in Agentic Bug Localization》——文件级 bug 定位当作「表示驱动的检索」问题。角色感知摘要（role-aware summaries）比文件路径高最多 40% Hit@5，占用比裸源码小 10.4–20.9 倍，是各项里性价比最好的。Agentless 案例 94% Hit@6（+4.7%）。
- **2609.31176**《Semantic Navigation for Issue Localization in Code Repository》(SemNav)——Issue-conditioned Semantic Cards（每个实体一句源信息的角色说明）把 File Hit@10 从 68.33% 提到 82.67%（Gemma 4B），工作上下文比读全文少 48.2%，下游解决率 44.00%→52.33%。
- **2606.30963**《Loc2Repair》——文件级定位是稳定的修复杠杆：基线 44.7% → 模型定位 48.9%/49.1% → 金标定位 52.4%；平均耗时也下降。
- **2606.22417**《Code Isn't Memory: A Structural Codebase Index Inside a Coding Agent》——固定 harness（Claude Opus 4.7）加结构索引：定位大涨、解决率统计显著上升、每格成本不升、$/solved 比 agentic grep 更低（SWE-PolyBench Verified + SWE-bench Pro，3 seed）。
- **2607.25431**《CodeNib》——多视图数据系统。更短的答案完成更多任务（78/90 vs 65）；选定上下文策略比 grep/read 少 50–87% token；上下文在「先给候选、首次读到源码后收回」时最有用（200 个 SWE-bench Verified 任务里解决 79，对比按需探索 66、aider repo map 56）。
- **2608.29675**《Cost-Effective Repository Exploration for Agentic Issue Localization》——仓库探索可交给更便宜的模型：保留约 78–94% Hit@3，耗时省 41–88%。
- **2610.05099**《Small Agents with Semantic Search》——语义搜索改善文件定位；ColGREP 在 CPU 上端到端延迟降 44.1%，token 少 29.1%。

**描述替代不了代码 / 成本教训**

- **2607.09691**《What Context Does a Coding Agent Actually Need to Act?》——自然语言摘要几乎答不出源码能答的行为问题（4/45 vs 27/45）；把文件剩余部分渲染成 UML 骨架和签名，跟直接删掉它解决的 issue 数一样（N=70，p=0.75）；压缩上下文能在大约三分之一 token 下追平整文件。**推论：描述负责指路，不负责讲清怎么改。**
- **2607.12161**《Token Reduction Is Not Cost Reduction》——最大压缩配置砍掉 38.4% 工具输出 token，账单反而贵 6.8%；token 削减与成本削减相关仅 r=0.15。**别拿「省 token」当卖点。**

## 4. 与本仓库相关、上一轮留下的证据

- aider repo map 用 PageRank（`repomap.py`）；aider #2405：80 万行 monorepo 里纯引用频率排名被高频 import 淹没，图距离 1 的直接 import 都进不了 map。
- 注释与代码不一致使引入 bug 概率约 1.5 倍；Linux 内核 v6.18-rc1 查出 869 处指向已消失函数的陈旧注释。
- 「文件必须有描述」的检查（`jsdoc/require-file-overview`、ruff `D100`）只判断存在、不判断正确。

## 5. 六路 workflow 的补充（w1hax2kog，7 个 agent，453k token）

这一轮把上面几节的判断压得更实，并推翻了我原先「描述值得做」的推荐。

**三条路的名分**

- **每目录一份手写说明 = 事实标准**：agents.md（自述 6 万+ 项目，2026 起由 Linux Foundation 下的 AAIF 托管，规范repo 约 24.9k★）。Codex 原生按目录读：root→cwd 逐层、每目录最多一个、近者覆盖远者、上限 32 KiB（`learn.chatgpt.com/docs/agent-configuration/agents-md`）。真实大仓量级：airflow 14、vscode 8、openai/codex 2、rust 2——**无一做到每文件**。
- **每文件/目录 schema 描述 + 生成索引 = 孤例或已死**：Agentic-Insights/codebase-context-spec（CCS）是最完整的 RFC 实现，已归档（最后推送 2025-04-08）；它自己的 `.context/index.md` 停在 2024-12-08，而仓库继续提交到 2024-12-15——**自证过期**；它想做的「LLM 生成描述」在 issue #16 只说「以后会加」，从未交付。aoci-spec/aoci-code（1,470★，单人）是唯一还活的同类，反而明文**禁止 AST/符号抽取/正则/模板生成**，要求语义模型手写。sno-ai/mda（680★）有 YAML frontmatter schema（description/doc-id/version/requires…，JSON Schema 2020-12 校验）。
- **生成索引的工具几乎不写自然语言描述**：repomix（28.8k★）、aider repo map、code2prompt（7.7k★）、files-to-prompt、gitingest（15.9k★）——全是文件树 + 符号/签名 + token 数；code2prompt 自述「This map is not a dependency graph or semantic text search」。
- **采纳度差约 4000 倍**（`gh api search/code`，2026-10-11）：`.context/index.md` 242 vs AGENTS.md 1,058,816 / CLAUDE.md 872,448 / llms.txt 180,224。

**官方明确不要做**

- Anthropic best-practices 的 ✅Include/❌Exclude 表：**「File-by-file descriptions of the codebase」在 ❌ 栏**，同栏还有「Anything Claude can figure out by reading code」。我复核过原文（`bp.md` 第 183 行）。
- 官方推荐按目录分层的说明文件（monorepo/large-codebases 页），单文件目标 <200 行；索引用 glob/grep 即取，理由原文「bypassing the issues of stale indexing and complex syntax trees」；规模大就搬到 Skills/Plugins/MCP。反过来也警告按目录分层「随代码库增长难以治理，约定会漂移、文件会过时、根文件没人负责」。

**实测：端到端那一半基本是负的**

- **2602.11988**《Evaluating AGENTS.md: Are Repository-Level Context Files Helpful for Coding Agents?》——**这是直接测 AGENTS.md 的那篇**。结论：上下文文件一般不提升成功率，推理成本平均涨 **20% 以上**，跨模型/跨 agent/LLM 生成与人工提交都成立。**单独点名「仓库概述」（repository overviews）——正是给 agent 描述目录/文件——不有用**；有用的只有「非标准的编码约定」。
- 2607.27250（预注册消融）：正确率效应界定在 ≤10–15pp，失败点在实现技能。
- 2607.09691：UML 骨架 vs 删掉，解决率无差（N=70，p=0.75）；摘要答行为问题 4/45 vs 源码 27/45。
- 2607.25431：静态 repo map 56 vs 按需探索 66 vs 按需候选+首读退役 79（200 题）。
- 2608.13568：LSP 语义检索不比 grep 省 token，多花 6%–118%，且免费给 agent 也不用（使用率 0–6%）。
- 定位侧的正面证据都不转化成功率：SemNav 68.33%→82.67% Hit@10、2607.11046 最高 +40% Hit@5、RepoGraph（2410.14684）、LocAgent 92.7%（2503.09089）。

**运维成本（具体事故表）**

- codeindex-sync（低星新项目，但有具体失败模式）：一次 rebase 触发几十个 hook→几十个索引进程抢 GPU；每个 agent 各自 checkout、重复建索引、路径一小时后就不存在；worker 中途死掉永久漏一个仓库；部分写入被记成功留下 claimed-but-empty 条目。因此内建 `verify --repair`。
- 近年成批停更/归档：Mintlify Writer（2026-06 弃用并停托管版）、bloop（9,483★，2024-12-04 archived）、refact archived、RepoAgent（v0.2.0 停在 2024-12-23）、facebookresearch/DocAgent（2025-04-23 最后推送）。
- 活得好的「由代码生成」路线是 aider repo map：不存人工描述、tree-sitter 从符号生成、每次按需重算、**不存在过期**；代价是只有符号和签名、没有语义。

**越深越没人读（Augment 博客，中等可信度）**

- AGENTS.md 100% 被发现、被引用文件 90%+ 会话被读、目录 README 80%+，但**嵌套 README 仅约 40%、孤儿文档 <10%**。
- 有案例：一份记录旧架构（REST+polling）的 AGENTS.md 让 agent 做出「能用但架构错」的轮询方案，作者结论是「换一份更好的 AGENTS.md 不解决问题」。

**没查到的**：「把 schema 描述写进每个源码文件头、专门给 AI 读」的主流先例——**空白**；「目录级描述懒展开」的工具——**没有任何工具字面这么做**；权威的目录级描述 schema 模板——没有；加描述 vs 不加的第三方 A/B——没有（所有 token 节省宣称都无第三方复现）。

## 6. 未核实 / 待实测

- codex harness 对说明文件的取法未核（本仓库代码未覆盖）。
- 本仓 6706 文件、2828 py + 1450 ts/tsx 是否已到「结构索引失灵」区间，需实跑。
- Cursor 等第三方 harness 的嵌套加载行为，本轮未逐一实测。
- 2606–2610 那批 arXiv 论文只能从页面读到，本环境无法独立复现，也未找到第三方复核。
- codegraph 的基准（省 88% 调用、62% token、44% 成本）是自评，量级（73,707★）异常，未证实。
