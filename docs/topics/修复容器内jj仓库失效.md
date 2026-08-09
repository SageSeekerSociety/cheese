## 结论:仓库内可控的 bug,不是基础设施问题

**根因**:`backend/app/domain/workspace/service.py` 的 `_ensure_worktree` 用
`jj workspace add` 给每个话题建一个共享主仓存储的 jj workspace。workspace 的
`.jj/repo` 指针文件里写的是**相对于宿主机真实目录深度**的相对路径(固定形如
`../../../../<project_id>/.jj/repo`,由 `_worktree_path`
`workspace_root/.worktrees/<project_id>/<branch>` 与 `_repo`
`workspace_root/<project_id>` 之间的嵌套深度决定)。

而 `tmux_provider.py::_create_container`(以及 `service.py::exec_in_sandbox`)
起沙箱容器时,只把这个 workspace 目录单独挂到容器的 `/work`——深度骤降为 1,
且主仓完全没挂进容器。于是那条相对路径在容器里往上穿的层数会先撞到文件系统根
就停住,永远到不了真正的主仓,`jj status/log/diff` 全部报:

```
Error: Cannot access ../../../../<project_id>/.jj/repo
Caused by: No such file or directory (os error 2)
```

已在当前容器(本话题的 /work,和真实场景走的是同一套挂载逻辑)里用真实报错验证。

## 修复思路(已在无 docker 依赖的沙盒实验里验证可行)

不改指针文件内容(host 原生访问 workspace 不受影响),而是让容器**额外**挂载
主仓的 `.jj` 和 `.git` 到"那条既有相对路径本来就会解析到"的容器内路径——
用与 jj 完全相同的 `os.path.relpath` 算法,以 `/work/.jj` 为锚点算出这个路径
(而不是硬编码层数)。验证链路:workspace 指针解析成功 → 内部 `git_target`
(colocate 用,同样是相对路径)也跟着解析成功 → `jj status/log/diff/bookmark
/git export` 全部在"隔离后的目录"里正常工作。

## 已完成

- [x] `workspace/service.py` 新增 `sandbox_vcs_mounts(project_id, branch, *,
      container_workdir="/work")`,用 jj 同款 `os.path.relpath` 算出需要额外
      挂载的 `-v` 参数(主仓 `.jj`→容器内 `.jj`,主仓 `.git`→容器内 `.git`)
- [x] `tmux_provider.py::_create_container` 接入(交互式/tmux 沙箱)
- [x] `service.py::exec_in_sandbox` 接入(一次性执行沙箱,同一个挂载 bug)
- [x] `compute.py::_sandbox_config` + `backend/sandbox/claude-sbx` 接入
      (SDK/LocalDockerProvider 沙箱——**这是 `AGENT_BACKEND` 默认值下线上实际
      在跑的路径**,任何部署配置都没显式设过 `AGENT_BACKEND=tmux`,不修这条
      等于没修)。三条起沙箱容器的路径全覆盖。
- [x] `backend/tests/unit/test_sandbox_vcs_mounts.py`:不依赖 docker 的回归
      测试,用 `cp -a` 模拟"容器只挂 workspace 目录"的隔离效果——第一个测试
      复现原 bug(`jj status` 报 "Cannot access ..."),第二个测试验证挂载点
      计算 + 应用后 `jj status/diff/log` 恢复正常
- [x] 语法校验(`ast.parse` 四个改动文件)全部通过

## 未完成 / 新发现的阻塞项(超出本话题范畴)

**`task check`(ruff/pyright/pytest)没能跑——不是代码问题,是本沙箱宿主机磁盘
被写满了**:`df -h /work` 从 98% 一路涨到 100%、可用空间到 0(`uv run pytest`
想装 Python 3.13 解释器时先撞见 `ENOSPC`)。排查发现 `/work` 内容本身只有
478M(其中 404M 是预置的 `.pnpm-store`,不像是我这个话题产生的),但 `df`
报的是同一块盘(`/dev/vda1`)被用掉 60G——这部分对本容器的命名空间不可见,
清理我自己产生的临时文件(~2M)之后可用空间纹丝不动,说明大头是宿主机上其他
容器/层占的,不是这个仓库里能清的东西。这和最初简报提到的"跟沙箱切换杀进程
一样超出本代码仓库范畴"是同一类问题——需要有宿主机/节点权限的人去查。

修复本身的正确性没有靠 `task check` 兜底,而是靠直接用 jj/git 命令做的黑盒
验证(见上面"已完成"里的回归测试思路,在磁盘写满前已用真实 jj/git 二进制跑
通;回归测试文件本身语法已过 `ast.parse`,但没能实际跑 `pytest`)。建议磁盘
问题解决后,找一个干净的沙箱把 `task check` 补跑一遍确认。
