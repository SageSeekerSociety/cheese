# 调研汇总：文件头 schema 注释 + 代码生成索引

读者：AI 队友平台负责人。设想：**每个代码文件开头加 schema 化的注释（含 description），索引由代码生成，只给当前所在位置的一级情况，更深的按需解析。**

本汇总基于 6 路调研（外部方案 / 加载机制实测 / 注释工具链 / 标准清单 / 反面实证 / 学术与结构工具），材料经核实，出处均为实际打开过的页面或 `gh api` 读到的 issue。

---

## 1. 结论（verdict）

**「官方生态里存在文件级描述这个位置」成立；「索引由脚本生成」成立；但「schema 化的 description 字段」不成立，且「人写描述」这一环会长期腐烂。** 六路材料一致显示：各语言都有描述文件/模块/包的一等公民机制（JSDoc `@file`、Python 模块 docstring、Go package comment、Rust `//!`、Java `package-info.java`、TSDoc `@packageDocumentation`），「写注释、脚本聚成索引、只给一级、按需展开」正是 TypeDoc / Sphinx / pdoc / MkDocs / Doxygen 几十年的成熟形态——这部分站得住。

但**没有任何跨语言、机器可读的「文件级 schema」标准**：这些机制里唯一稳定的是「一段自由文本 description」，附带的 `@author/@since/@see` 是各生态私有标签，JSDoc 与 TSDoc 连标签名都不通用。所以「schema 化」若指望套用现成标准，会落空；必须自造约定并用 linter 强制，而「逐文件强制描述」这条线**被 Go 的 golint（2021 弃用）和 ESLint 的 require-jsdoc（v9 移除）两个官方先后放弃**，理由正是「不可按项目关闭、产出无信息量的填充」。

最硬的反面证据在「description 会不会过期」：注释与代码的不一致**不是无害噪音，不一致改动导致引入 bug 的概率约为 1.5 倍**；Linux 内核 v6.18-rc1 上自动查出 869 处引用已消失函数的陈旧注释。而所有「文件必须有描述」的检查（`jsdoc/require-file-overview`、ruff `D100`）**只判断存在、不判断正确**——一段过期多年的 `@fileoverview` 能照过全部检查。因此建议：**把意图描述从「人手写」改为「LLM 从代码生成 + 回填」，把机器强制力花在「结构索引」上（SCIP / ctags / tree-sitter，零人工、可脚本化），并接受「生成的一级索引只负责指路、不负责讲清用途」。**

---

## 2. 可能的做法（routes）

### 路线 A：文件头 schema 注释 + 脚本生成索引（原设想）

- **how**：每个文件头写一段结构化注释，description 为必填字段。JS/TS 用 `/** @fileoverview ... */`，Python 用模块 docstring，Go 用 package comment，Rust 用 `//!`。索引由工具扫注释生成：TypeDoc（`--entryPoints` + `--navigation` 生成分层 sidebar）、sphinx-apidoc（脚本生成 RST 骨架 + autodoc 填内容）、pdoc（`index.html` + `search.js` 自动聚成总索引）、Doxygen（`\file` + `\brief` 汇总进 File List）。强制存在用 `eslint-plugin-jsdoc` 的 `require-file-overview`（默认即 `mustExist:true`）、ruff 的 `D100`/`D104`。
- **adoption**：不是新方案。TypeDoc / Sphinx / pdoc / MkDocs（`nav` 定层级）/ docfx（metadata→build 两阶段）都在「人写注释、工具生成整包索引」这条路上。Doxygen 的 `\file`+`\brief` 是最贴近的现成用法——brief 文本按官方说明「used in lists and at the start of the documentation page」。
- **cost**：检查本身成本极低（加一条规则）。真实成本在 (a) 每个新建文件都要手写描述，没有就红；(b) 存量文件一次性补齐；(c) ruff 只覆盖 public 模块（私有模块不报），「每个文件都有」的保证打折扣；(d) **描述会静默过期，且没有现成工具能发现**——要能检测过期，description 必须结构化到可机检（日期/版本/依赖/可解析字段），否则只能靠人；(e) 构建期解析会执行代码：sphinx autodoc 在 build 时 import 有副作用的模块、pdoc 动态分析真的执行模块、docfx 走项目输入需 `dotnet restore`。
- **verdict**：**可作为补充，不宜作主干。** 「写注释 + 生成索引 + 只给一级」机制成立，但「schema 化」无标准可依，且描述的正确性无人能自动守护。

### 路线 B：逐级上下文文件（AGENTS.md / CLAUDE.md，按目录作用域）

- **how**：在每个目录放一个 `AGENTS.md`（或工具的 native 名），就近优先（`agents.md` 规范：`The closest AGENTS.md to the edited file wins`），靠目录层级实现「只给一级、深处按需」。跨工具统一用 `@AGENTS.md` 导入或软链。
- **adoption**：agents.md 规范 + Claude Code + Cursor（嵌套 AGENTS.md）+ Gemini CLI + Codex。OpenAI 主仓有 88 个 AGENTS.md。
- **cost**：**加载时机没有规范约束，各家实测都在翻车。** Claude Code 子目录 CLAUDE.md 不按需加载（issue 24987，已 closed）；触发只认 Read/Edit/Write、不认 Bash/Grep（issue 93403，**仍 open**），auto 模式下整段排查可"静默漏加载"；Cursor 嵌套 AGENTS.md 不加载被员工确认为 bug，根 AGENTS.md 在 2.1.26 也随机失效；反过来 Cursor 3.6 起把整仓嵌套 AGENTS.md 当 Always 规则全量加载，monorepo 撑爆上下文。文件名/格式不符会**静默失效**（Cursor 要 `.mdc`、Claude Code 不认 `AGENTS.override.md`、子目录 CLAUDE.md 会压掉同目录 AGENTS.md）。
- **verdict**：**不推荐作为「按需解析」的实现载体**（加载不可靠、不可控、还缺机器可读的结构字段）。可作为「给 agent 的人和团队约定」补充。

### 路线 C：纯结构摘要，不写注释

- **how**：不要求任何注释，索引完全从语法结构生成：tree-sitter 抽 AST（`tags.scm` 抽 def/ref）、ctags 生成 tags 文件、SCIP 做跨文件符号关系、aider 的 repo map 用 networkx **个性化 PageRank**（节点=文件、边=引用者→定义者）排序后裁到 `map_tokens` 预算（默认 1024，二分拟合 `ok_err=0.15`）。产出是「文件→关键符号+被谁引用+重要度排名」。
- **adoption**：aider repo map（生产在用）、GitHub code navigation（基于 tree-sitter，只做定义/引用跳转，限 <100k 文件仓库）、Sourcegraph SCIP（替代 LSIF，gzip 后小约 4x）、Kythe、stack-graphs。
- **cost**：**零人工、可脚本化、无注释腐烂问题**，且天然支持「只给一级 + 按需深挖」（token 预算二分、跳转）。但**它是「指路」不是「讲清用途」**：所有结构工具的产出都是符号关系与位置，没有任何一个给出「这个文件是干什么的」的自然语言。且**大仓库会失灵**：aider issue #2405 显示 80 万行 monorepo 里纯引用频率的 PageRank 被高频 import/别名主导，连图距离 1 的直接 import 都挤不进 map。另需每语言维护查询，`stack-graphs` 已被 GitHub 弃维护、GitHub code nav 只支持 active branch。
- **verdict**：**推荐作为「一级索引」的主干。** 负责「文件在哪、有哪些符号、谁引用谁」，不承诺给出用途。

### 路线 D：结构 + LLM 分层聚合摘要（结构指路，意图由模型生成）

- **how**：仍由结构工具（tree-sitter/SCIP）切出单元并排名，但「这个文件/模块是干什么的」由 LLM 生成并回填：学术做法（arXiv 2501.07857）先用语法分析切出函数等小单元、用 local LLM 逐单元摘要、再聚合到 file/package 级，靠「custom prompts that capture the intended purpose」注入领域/业务语境。回填后写回源码（填补 SCIP `SymbolInformation.documentation` 这个 optional-but-strongly-recommended 的可选字段），或存在生成的索引旁。
- **adoption**：学术界已有完整 pipeline（arXiv 2501.07857）；工程侧 SCIP proto 明确支持用 `documentation` 字段携带 docstring，tree-sitter tags 有可选 `@doc` capture；`pydoclint` README 提到 AI agent 写的 docstring「mostly correct」但会遗漏与幻觉，需用与签名的机检补漏。
- **cost**：需要 LLM 调用（成本/延迟/需本地模型）、需要描述质量验收（BLEU/ROUGE 与人类判断相关性差，不能用 n-gram 重叠当验收）。好处是**解决了「谁写描述」和「描述腐烂」**——生成时点与代码同步，且不依赖人记得改。
- **verdict**：**推荐。** 这是唯一能让「一级索引带上意图」且不陷入「人写描述必然腐烂」的路径；与路线 C 组合即「结构指路 + 意图回填」。

### 路线 E：让索引可被检测为过期（把 description 结构化到可机检）

- **how**：把「描述」拆成能自动判对的字段：可判定的条件写成注释里的机器可读指令——日期 / package.json 版本 / engines 引擎版本 / 依赖被装/卸（+/-）/ 依赖版本 / peer 依赖版本（`eslint-plugin-unicorn` 的 `expiring-todo-comments`，六类条件，recommended 默认开但**日期检查默认关**，需 `checkDates:true`；`no-expired-todo-comments` 专抓过期日期；Ruby 的 `todo_or_die` 运行时抛错）。或比对外部事实源：`pydoclint` 检查 docstring 的参数/返回/yields/raises/类型注解是否和真实签名一致（自述目标 `catch the drift from everyday edits`）。
- **adoption**：ESLint 生态（expiring-todo-comments）、pydoclint、todo_or_die、kinto.js（用 TSDoc 解析器自动比对 JSDoc 与 TS 签名）、numpy（clang `-Wdocumentation` 批量发现上百条损坏的 C 注释）。
- **cost**：**只能覆盖「有可判定条件」的部分**——一个自由文本 `@fileoverview` 里没有这种条件，就没有任何工具能自动发现它过时。编译器/linter 只能抓格式错（`@param` 指向不存在的参数），抓不到「这段描述还对不对」。`todo_or_die` README 自己警告散布定时炸弹有反噬成本。
- **verdict**：**可作为补充。** 给 description 里能机检的字段上这类检查；别指望它守护自由文本的正确性。

---

## 3. 现有标准清单（standards）

「文件级描述」在各语言里的名字、是否官方规范、能否强制检查：

| 语言 | 机制 | 官方规范程度 | 可强制检查 |
|---|---|---|---|
| JavaScript | JSDoc `@file`（别名 `@fileoverview`、`@overview`） | jsdoc.app 官方标签，原文 "Describe a file"，但只给一句描述+示例，无结构化字段 | `eslint-plugin-jsdoc` / `require-file-overview`（默认 `mustExist:true`、`preventDuplicates:true`、`initialCommentsOnly:true`） |
| TypeScript | TSDoc `@packageDocumentation` | tsdoc.org 正式规范；必须放在入口 `.d.ts` 且是该文件第一个 `/**` 注释；不得描述单个 API 项 | 由 API Extractor 等消费；无独立 lint 规则 |
| Python | 模块 docstring（PEP 257） | 官方 PEP，但**明文自认 "conventions, not laws or syntax"** | ruff `D100`（public module）、`D104`（public package）；`interrogate --fail-under`（阈值制，默认 80%) |
| Go | package comment（package 子句上方、无空行；首句 "Package \<name\>"） | 官方 go/doc 文档，措辞用 should；多文件包只写一处，多写会被拼接 | golint 的 exported 规则已弃用；社区分叉 `revive` 的 `exported`（可配置、可关） |
| Rust | 内层文档注释 `//!` 与 `/*! */`（等价 `#![doc=...]`） | **语言参考里有正式语法定义**（是语言特性，最硬）；rustdoc 书要求 lib.rs 顶部写 `//!` | 编译器 lint `missing_docs`（allow-by-default，`#![deny(missing_docs)]` 转编译错误） |
| Java | package 注释（package 声明正上方）或专门的 `package-info.java` | Javadoc 规范级；一个包只能有 `package-info.java` 或 `package.html` 之一 | javadoc 工具强制 |

**统一字段不存在**：JSDoc 与 TSDoc 同属 JS/TS 一脉却用不同标签；跨语言更无一致 schema。可复用为索引格式的**结构**标准倒是成熟：SCIP（协议 + `scip.proto`）、ctags tags 格式（`!_TAG_FILE_FORMAT` 做版本标识）。

---

## 4. 会踩的坑（pitfalls）

1. **描述会静默腐烂**：注释与代码不一致导致引入 bug 的概率约为一致改动的 1.5 倍；内核 v6.18-rc1 自动查出 869 处引用已消失函数的陈旧注释——任何「人写描述」的方案都要正面回答「谁来发现漂移」。
2. **「存在」检查防不住「过期」**：`require-file-overview` / ruff `D100` / `missing_docs` 只判断标签或 docstring**存在**，一段过期多年的 `@fileoverview` 照过全部检查。
3. **自由文本无法机检过期**：能报「过期」的工具都要求注释里带可判定条件（日期/版本/依赖/可与签名比对的字段），纯粹的 description 没有这种条件，没有工具能自动发现它不符。
4. **强制注释会产出填充噪音**：无信息量、不可关闭 → golint 2021 冻结弃用、ESLint v9 移除 `require-jsdoc`；用户要「关掉一条规则」被官方拒绝（golang/lint#263），最终逼出分叉 `revive`。
5. **手写 comment 与类型/签名重复必然漂移**：kinto.js 发现 JSDoc 参数与 TS 声明不一致，解法是用解析器自动校验而非人工维护；numpy 靠 clang `-Wdocumentation` 才批量发现上百条损坏注释。
6. **粒度选错**：JSDoc/Python/Rust 是「文件/模块」级，Go/Java/TSDoc 是「包」级——选哪个决定索引的键是路径还是包名；一个文件一个注释的模型更贴近前者，Go/Java 包级先例不能直接套。
7. **文件头注释有位置约束**：Doxygen 里文件只能用结构命令 `\file` 文档化（no doc block can precede a file）；TSDoc `@packageDocumentation` 必须放入口 `.d.ts` 且是该文件第一个 `/**`——不是随便一个注释位置都行。
8. **抽取范围陷阱**：Doxygen 里全局函数/变量/枚举只有在「所属文件被文档化」后才出现（除非开 `EXTRACT_ALL`），即文件级注释缺失会连带实体一起消失，索引出现空洞。
9. **注释当结构化字段时富文本会带来歧义**：docfx 默认把 C# XML 注释当 Markdown 解析，在不支持处（VS intellisense）渲染出错，需 `shouldSkipMarkup`。
10. **构建期解析会执行代码**：sphinx autodoc 在 build 时执行有 import 副作用的模块、pdoc 动态分析真的执行模块、docfx 需 `dotnet restore` 做 design-time build。
11. **纯频率排名在大仓库失灵**：aider #2405，80 万行 TS monorepo 里 PageRank 被高频 import/别名主导，图距离 1 的直接 import 都进不了 map。
12. **把「索引/导航」当「摘要」**：SCIP / ctags / tree-sitter tags / stack-graphs 的产出是符号关系与位置，不含用途说明；docstring 字段是**可选**（SCIP `documentation` optional、tree-sitter `@doc` 可选），不写注释结构索引不会凭空补出意图。
13. **「一个脚本搞定所有语言」不成立**：tree-sitter 是纯语法、能识别什么 kind 取决于 grammar；跨文件/跨语言要额外层（stack-graphs 的 TSG、SCIP 的专用 indexer、Kythe 的 indexer）；tags 的 capture 名应用往往只支持子集，新语言要自己维护查询。
14. **ctags 是 pattern/贪心解析不是编译前端**：宏会漏、同名行会指错、假定语法正确（garbage in garbage out），当「可信结构源」需额外校验。
15. **专有工具的可持续性风险**：Sourcetrail（16k+ star 的源码浏览器）2021 年底被作者归档、官方博文即 "Discontinue Sourcetrail"；stack-graphs 被 GitHub 标「no longer supported」；mkdocstrings 处于 maintenance mode。
16. **机制会「欠载」也会「过载」**：Cursor 3.6 起把整仓嵌套 AGENTS.md 当 Always 规则全量加载撑爆上下文；另一头子目录 CLAUDE.md 又可能从不加载——同一机制在不同实现里两个极端都出现。
17. **触发绑定具体工具**：Claude Code 只认 Read/Edit/Write，Bash(cat/sed/grep)、Grep 工具都不触发（issue 93403，open），auto 模式会静默漏加载子树上下文。
18. **文件有硬闸门**：Claude Code 单个文件超 4 MiB 被跳过、建议 200 行内，超限静默截断；`/compact` 后嵌套子目录 CLAUDE.md 要重新按需触发。
19. **「自动生成 = 全部文档」是常见误区**：Diátaxis 点名批评；生成式索引有价值但只覆盖四类文档之一，仍需解释/任务型内容兜底。
20. **验收指标会骗人**：BLEU/ROUGE（甚至 CodeBLEU）与人类判断相关性差；若用「结构+LLM」生成描述，别拿 n-gram 重叠当验收。
21. **语言自身的边角**：Rust `////`（四斜杠）、`/***` 都不是文档注释；块文档注释在第一个 `*/` 处终止、lexer 不认 Markdown；Go 多文件包都写 package comment 会被拼接成一个大注释；Java 一个包只能一个 `package-info.java` 或 `package.html`。
22. **工具版本时效**：`jsdoc/check-examples` 只在 ESLint 7 生效；unicorn 的 `expiring-todo-comments` 日期检查默认关；「开箱即用」不等于「开了你想开的那项」。

---

## 5. 需实测才能定的问题（openQuestions）

1. **「每个文件一句」的数据规模对 agent 到底有没有用**：需要在真实仓库跑一次「结构索引 vs 结构+LLM 描述」的 A/B，看 agent 定位目标文件的命中率/token 消耗是否显著改善——现有材料只能证明结构索引能指路，不能证明文件级一句描述带来增量价值。
2. **LLM 生成 description 的回填策略**：生成时机（提交时 / CI / 按需）、放哪（写回源码注释 vs 只进生成索引）、怎么避免与源码污染——需实测。
3. **在 monorepo 规模下排名算法要加什么语义信号**：aider #2405 证明纯频率失效，但「加 personalization / 邻近度 / embedding」哪种有效、代价多少，材料未回答。
4. **「过期」的自动发现能否不依赖机检条件**：CCI（code-comment inconsistency）检测方法精度 >90% 是学术结果，工程化成本与误报率需实测。
5. **跨工具统一事实源的实测效果**：用 `@AGENTS.md` 导入 / 软链能否绕过各工具原生嵌套加载的 bug，需在 Claude Code + Cursor + Gemini CLI 上逐一实测。
6. **本项目主要语言到底是哪些**：决定优先用哪套机制（Rust `//!`+`missing_docs` 最硬、Python ruff D100/D104、JS/TS jsdoc/TSDoc），材料无法替本项目定。
7. **索引的格式选择**：复用 SCIP（proto + 多语言 indexer）还是 ctags tags 格式，还是自造——需评估接入成本。
8. **未读项**（明确声明，未据此下结论）：Sourcegraph SCIP design doc（页面 404）、DeepWiki 的 aider repository-mapping 页（429）；关于 SCIP 的结论只基于 README、`scip.proto` 与 announcement 博文。

---

*方法说明：本环境的 WebSearch 工具不可用（返回的是模型自述答案、无真实结果链接），全部结论改由 WebFetch + `gh api` 实际打开页面/接口得到；凡未真正读到的内容一律标注。*
