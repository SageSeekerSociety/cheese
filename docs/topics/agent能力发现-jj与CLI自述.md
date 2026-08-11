> 状态：开工首轮。已完成环境核实，正在做「还有多少不会的」系统清点。

## 目标

让一个新分身开工时，本项目特有的东西它**有途径知道**。分两类解，不混为一谈：

| 类别 | 症状 | 解法 |
|---|---|---|
| **工具没自述** | `--help` 是裸参数名，语义只能口口相传 | 补 `help=` / `description=`——**这一层永不过期，因为它和代码是同一份东西** |
| **文档没写** | 根本没有任何地方写过 | 补 <&.claude/rules/>（按路径加载）优先于 <&CLAUDE.md>（常驻，每轮都要付 token） |

界线抄 Buzz 那句枢纽：**散文只写 `--help` 说不了的东西**（语义、坑、输出契约、什么时候**不**该用）。

## 已核实（含两条对简报的修正）

### ✅ cheese CLI 源码在本仓，能改

简报里标了「这一条我没替你验证过」。答案是**能改**：

- 源码 <&backend/sandbox/cheese>，27KB Python + argparse，容器里的 `/usr/local/bin/cheese` 就是它的挂载副本。
- 已有单测 `backend/tests/unit/test_cheese_cli.py`，改动有地方落测试。

### ⚠️ 但我的工作区落后于 main —— 直接改会**吃掉别人的提交**

```
backend/sandbox/cheese 里有没有 source_fingerprint()：
  main            → 有
  main@upstream   → 有
  @（我的工作区）  → 没有
```

PR #260「修复：cheese await 与沙箱 CLI 同步」于 2026-08-11 05:43 落地，加了 `--version` / `build_parser()`。我的工作区父提交是 08-10 15:17。**先 rebase 再动这个文件**，否则 PR 会把 #260 revert 掉。

这条本身就是要写进 jj 规则的坑：`jj status` 说「无改动」不代表「你在最新的 main 上」。

### ✅ jj 的误导信号，现场抓到一条实证

不用推测——本轮第一条 `gh pr list` 就撞上了：

```
$ gh pr list --state open
failed to run git: fatal: not a git repository (or any parent up to mount point /)
```

工作区是 jj 仓（`.jj/` 在，`.git/` 不在），凡是自己去发现仓库的工具全瞎。加上环境给 agent 的 `Is a git repository: false`，分身收到的信号是「这儿没有版本控制」。**这是症状层面的东西，正是规则该写的。**

## 硬边界（来自简报，照办）

- 不碰 `.github/workflows/`、不碰 <&.claude/scripts/check.sh>（另一个 agent 在那两处）。
- 不碰 `deploy/`（归属待拍板）。
- 走 PR，不直推 main。
- 别把 <&CLAUDE.md> 撑大——能进 rules 就不进常驻。

## 下一步

1. **清点**「新分身没有任何途径知道的东西」——主要矿脉是 `docs/topics/` 下 67 篇历史话题文档，其中一眼可见的候选：`修复容器内jj仓库失效`、`沙箱跑测试标准方法`、`推PR分支前先同步base`、`文档内引用语法遵循`、`agent不知道自己在协作平台里`。产出清单并标类别，**要有取舍**。
2. rebase 到最新 main，再补 <&backend/sandbox/cheese> 的子命令 help（重点 `split --brief`）。
3. 写 jj 规则，按 <&.claude/rules/e2e.md> 的「症状 → 会被误判成什么 → 真因」写法。
4. 立那条界。
