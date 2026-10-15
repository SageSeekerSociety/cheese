---
paths:
  - "backend/app/**"
  - "frontend/src/**"
  - "backend/Dockerfile"
---

# Module boundaries: the conventions no check can hold

The boundary checks run in CI on matching commits, and locally as one command,
`task boundaries` (`Taskfile.yml`), which dispatches to `backend/Taskfile.yml`
and `frontend/Taskfile.yml`. Each check's own file and failure output say what
it enforces and how to fix a failure, so this file keeps only (a) the
conventions no machine holds and (b) the landing points other files point at.

A check cannot say whether the tree is getting *better*: a ratchet against a
frozen baseline holds still and reports success forever. That half is the board,
`arch-metrics.py`, and the per-commit ratchet snapshot beside it; the board
reports and never fails. Counts, history and per-contract reasoning:
`docs/manual/dev/architecture.md`, `docs/manual/dev/arch-metrics.md`,
`docs/topics/棘轮页方案`, `backend/.importlinter`.
For editing the import-linter configuration and resolving its conflicts, read
`.claude/rules/importlinter.md`.

## Backend: the import graph is checked, not assumed

The import graph and the contracts that describe it — `api -> domain -> core`,
sibling domains acyclic, `app/domain`'s seven layers (dependencies point down,
siblings may import each other, every child of `app.domain` must sit in exactly
one layer), route modules independent of one another, and the `turn` layering —
are declared in `backend/.importlinter` (AST-based, so an import inside a
function counts too). A new child of `app.domain` — a package or a top-level
module — is a contract violation until the layer table names it.
`check_boundaries.py` names the fix contract by contract (no wildcards; exit 2 is
"could not judge", never a pass). What it judges this tree against is the tree at
the merge base with `origin/main`, graded with the same contracts: the
violations that tree already has are held open, and a violation only this tree
has is the verdict. Nothing about that baseline is stored, so no branch carries
a list of exemptions; the file's own `ignore_imports` lines are for a violation
somebody decided to accept, and are meant to stay rare and short.

Conventions no contract sees:

- **A module-level mutable singleton** — a shared client, engine or cache built
  at import time — is a boundary leak no contract sees. If one is unavoidable it
  belongs in `app.core` with a comment saying what owns its lifetime; a new one
  is an admission, not a habit.
- **Boundaries follow real dependencies, not a directory table.** The contracts
  name packages the code actually imports, and the baseline is measured from the
  same graph rather than transcribed by hand.
- **A deferred import's reason names the cycle** it breaks (or an optional
  dependency, a start-up cost, a test double). "Avoid circular import" with no
  cycle named is not a reason — check whether the cycle still exists first.
  `check_deferred_imports.py` asks for a reason; only a reader can judge it.

## Frontend: a component does not fetch

Enforced by ESLint under `src/components/**` (no `@/api`, `@/services/*`,
`vue-router`); the rule's own message is the fix. Outside the component
boundary the whole tree is barred from `@/network` — that stack was retired and
`frontend/eslint.config.mjs` keeps a `no-restricted-imports` rule so it cannot
come back. The four principles, in the order they matter:

1. **Data in by prop, out by emit.** A component that takes what it renders
   can be reasoned about from its template; **the shape of the props is on
   you**.
2. No fetching from the network layer — `pnpm run lint:boundary`.
3. No route knowledge — the same rule.
4. **Testable without a server**. No check decides which side of that line a
   file is on: a file that needs a server to render is a view or a container,
   not a component.

## Frontend: one API module per domain behind `src/api.ts`

`frontend/src/api.ts` holds nothing but `export … from` statements
(`frontend/eslint.config.mjs` enforces that); the code lives in
`frontend/src/api/<domain>.ts`, and importers keep writing `@/api`.

**A module under `src/api/` imports `./http` and its siblings, not `../api`.**
Going through the facade puts the call inside whatever a test mocked `@/api`
with. Some older modules still do, and changing one changes what its tests
intercept, so it is its own change.

## Files have a size cap

Judged against the merge base. Split the file: `check-file-sizes.py`'s failure
names the cap and its reason, and `EXEMPT` is the one considered exception. A
file over its cap is a prompt to split, not a defect; raising the cap helps
nothing.

Scenes and the catalog live in `frontend/AGENTS.md` and
`docs/manual/dev/scenes.md`.

## moved-name-in-package-breaks-outside-scripts

`backend/app` 里被 backend 之外引用的公共名字（harness 包的类、函数）要搬家时，除了
跑本地闸门，再 `grep -rn "<旧模块路径>" --include=*.py . | grep -v '^\./backend/'`，
把 backend 之外的调用点一起改。

**Why:** `backend/pyproject.toml` 的 `[tool.pyright]` 是 `include = ["app"]`，`scripts/`
和 tests 都不查；`scripts/remote_execution/*.py` 又习惯按模块路径直接取包内的名字，不
走包门口。2026-10-08 把 `ClaudeLaunch` 从 `session_launch` 搬到 `device_launch` 时，
`scripts/remote_execution/acceptance.py` 仍从旧模块取它——ruff、边界闸门、延迟 import
账、文件大小、单测全绿，只有 CI 的 `remote / acceptance` 抛 ImportError；那个 job 要
docker 和 npm 装的那个 claude，本机跑不完整，只能看日志。

**How to apply:** 动 backend 包里的公共名字（尤其 harness 包）时，本地全绿不等于没有
调用点，backend 之外的 Python 导入点要自己 grep 一遍。仓内调用点一律走包门口
（`from app.domain.agent.harness.claude_code import ClaudeLaunch`），别按模块路径深入。

## container-command-needs-a-copy-in-the-image

dev 跑的是 backend 镜像的 `production` 阶段（`.github/workflows/build.yml` 的 `target: production`，compose 用 ghcr 镜像、没有源码 bind mount），而这个阶段是**逐个点名** COPY `scripts/` 下的文件（`backend/Dockerfile:184-192`），没点名的脚本不在容器里，`python -m scripts.<名字>` 必然 `ModuleNotFoundError`。

2026-10-14 实测：`docker exec -i <backend 容器> python -m scripts.seat_throughput --window 24h` 跑不起来。这条命令同时出现在证据文档和脚本自己的 docstring 里，两边都写错了，是独立评审逮到的（PR #3753）。

**Why:** 脚本 docstring 里写着「进容器这么跑」，看起来就是权威用法；仓里 grep 也找得到这个文件，于是默认容器里也有。
**How to apply:** 在文档、PR 正文或任务总结里写出任何「进容器跑 X」的命令之前，先 `grep -n 'COPY' backend/Dockerfile` 看 production 阶段点没点名；没点名就别写，或改成在够得着平台库的环境里跑。同理：容器名在 `cheese-backend-1` 与 `cheese-backend-b-1` 之间轮换，别写死（`deploy/app-container.sh backend` 问得到）。

## deleting-a-module-alias-breaks-string-monkeypatch

动一个模块的 import 别名（例如删掉 `backend/app/domain/topic/services.py` 里的 `from app.domain.repository import service as ws`）时，grep 别只找 `ws.` 这种用法，还要找**按字符串打的补丁**：`monkeypatch.setattr("app.domain.topic.services.ws.session_dir", fake)`。这种写法在解析目标时按点号逐段 `getattr`/`__import__`，别名一没就抛 `ImportError: ... is not a package`，整个用例 error，不是断言失败。

**Why:** 2026-10-15 这一步的 CI 集成分片就红在这里（`tests/integration/test_clone_api.py`）。本机测试库同时只允许一个进程，我按惯例没跑全量 pytest，本地全绿、CI 才发现。

**How to apply:** 删别名时 `grep -rn 'setattr("' | grep <模块名>` 一遍。修法是把补丁打到路由真正递进去的那个对象上（就地 `monkeypatch.setattr(ws, "session_dir", fake)`——`ws` 是测试自己 import 的模块，改它的属性对所有持有同一模块对象的调用方都生效），断言不用动。
