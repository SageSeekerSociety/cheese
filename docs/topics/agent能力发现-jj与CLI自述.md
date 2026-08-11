## 我理解的任务

一个新分身开工时，有哪些**本项目特有**的东西是它没有任何途径知道的？找出来、分类、**有取舍地**补上。

抄 Buzz 的前两层：**CLI 自己描述自己**（`--help` 和代码同源，永不过期）+ **散文只写 `--help` 说不了的**。不抄它那个 2.9 万字常驻大文件。

## 已核实的前置事实（两条纠正了简报）

### ✅ cheese CLI 源码在本仓，能改

<&backend/sandbox/cheese>，27KB Python + argparse，容器里 `/usr/local/bin/cheese` 是它的 bind-mount。简报里标"我没替你验证过、改不了就降级成建议文案"的那条——**不用降级，真的能改**。

### ⚠️ 本工作区当时落后 main 一天，已 rebase

`main@upstream` 上 PR #260 刚动过 <&backend/sandbox/cheese>（给它加了 `--version`），而我的工作区父提交停在前一天的旧 main。**直接在那个基上改会把 #260 revert 掉。** 已 `jj rebase -s <本话题的 change> -d main@upstream` 同步后再动手。

这件事本身就是清单里最值钱的一条：它没有任何征兆，做完才会有人发现自己的 PR 被无声回滚。

## 能力缺口清单（取舍后）

「工具没自述」补 `--help`（永不过期），「文档没写」补散文。**取舍原则：只收「不知道就会做错事」的，不收「知道了更好」的。**

| # | 缺口 | 类 | 处置 |
|---|---|---|---|
| 1 | 18 个 cheese 子命令**全部没有 `description`**，`--help` 只有裸参数名 | 工具没自述 | ✅ 已补，并加测试守住 |
| 2 | `split --brief` 的 help 在源码里就是断句的（"……尽量写"戛然而止） | 工具没自述 | ✅ 已补 |
| 3 | 本仓 VCS 是 jj，环境却告诉 agent `Is a git repository: false` | 文档没写 | ✅ 进 <&CLAUDE.md>（理由见下） |
| 4 | 工作区可能落后于 main，在旧基上改共享文件 = 无声 revert 别人的 PR | 文档没写 | ✅ 进 <&CLAUDE.md> |
| 5 | <&.claude/settings.json> 白名单里 6 条 git、0 条 jj，且 `git add`/`git commit` 在这儿永远不可能成功 | 工具没自述 | ✅ 换成 jj 只读命令 + `git config`（跑测试要用） |
| 6 | 分身的沙箱是从 main 新建的 workspace，gitignored 的东西（`reference/`、`tmp/`、clone 来的参照仓库）**不会跟过去** | 工具没自述 | ✅ 写进 `split --help` |
| 7 | 简报不用重复父话题活文档——平台自动附快照（<&backend/app/domain/topic/services.py> 的 `_initial_brief`） | 工具没自述 | ✅ 写进 `split --help` |
| 8 | 闸门跑在另一个容器，只和沙箱共享 `/work`，`$HOME` 不同 →"我这儿绿的"≠"闸门绿的" | 工具没自述 | ✅ 写进 `accept-request --help` |
| 9 | `doc set` 是整块覆盖、无版本历史；人在面板上 2.5 秒自动保存，覆盖上去捞不回来 | 工具没自述 | ✅ 写进 `doc set --help` |
| 10 | 散文和 `--help` 的分界没人立过，所以两边互相重复、各自过期 | 文档没写 | ✅ 进 <&CLAUDE.md>，一段话 |

### jj 为什么进 <&CLAUDE.md> 而不是 <&.claude/rules/>

`.claude/rules/` 是**按路径触发**的——你碰到匹配的文件它才加载。而 jj 这个洞的触发点在**第一轮、碰任何文件之前**：环境块直接告诉 agent "Is a git repository: false"。路径触发结构上就太晚了。

代价是常驻 token，所以写成一张**症状 → 会被误判成什么 → 真因**的紧凑表（抄 <&.claude/rules/e2e.md> 的写法），不写教程。

## 查出来但**故意没补**的

- **`.agents/skills/lark-*` 和 `.claude/skills/lark-*` 逐字节相同，零同步脚本**。这正是父话题"明确不学"里点名的 Buzz 漂移隐患，本仓自己有一份。没动：删哪一份取决于另一套工具链怎么加载它，超出本话题，且删错会静默掉技能。**建议单开一条。**
- **<&CLAUDE.md> 里"Pre-commit hook enforces this"没有事实支撑**（全仓搜不到 hook 的安装入口）。已被交接给改 <&.claude/scripts/check.sh> 的那个 agent，不重复动。
- **`reference/` 被 <&CLAUDE.md> 当作可查阅的目录，但它是 gitignored、新沙箱里根本不存在**。没单独补文档——第 6 条（写进 `split --help` 的"gitignored 的东西不会跟过去"）已经覆盖了根因，再加一条散文就是重复。
- **`sub.add_parser("__await-child")`** 是内部命令，故意不给 help，测试里也按 `__` 前缀排除。
- 一堆"知道了更好但不知道也不会做错事"的：Taskfile 有哪些 task、领域包怎么分。`--help` 和 `ls` 就能答，不写。

## 守卫（规矩长在机器上）

<&backend/tests/unit/test_cheese_cli.py> 新增两个测试：每个对外子命令必须有 `description`、每个参数必须有 `help=`。

**验过它会咬人**：拿改动前的 <&backend/sandbox/cheese> 跑同一个断言，18 个子命令全部命中。绿是修出来的，不是构造出来的。

## 状态

- [x] 前置核查（CLI 可改性 / 工作区落后 / jj 症状复现）
- [x] 能力缺口清单（含取舍与"故意没补"）
- [x] cheese CLI 18 个子命令的 `--help`
- [x] jj 那条进 <&CLAUDE.md>
- [x] 散文/`--help` 分界那段
- [x] 守卫测试（19 passed）
- [x] `check.sh --no-tests`：3/3 passed
- [ ] 递 PR

## 硬边界（照办）

没碰 `.github/workflows/`、<&.claude/scripts/check.sh>、`deploy/`。守卫做成 <&backend/tests/> 下的测试，不进 `check.sh`。
