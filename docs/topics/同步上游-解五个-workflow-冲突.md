## 目标

把上游 `SageSeekerSociety/cheese` 领先的三个提交（#264 前端闸门 / #265 Go CLI 闸门 + 并发键 / #266 全量 action SHA 固定）合进本地 main，逐文件解掉 `.github/workflows/` 下的五个冲突。merge-base `806d30d2`，已核实。

**状态：五个冲突全部解完，静态验证全绿；两项真跑验证在跑（见下）。**

## 开工时先纠正的一件事

父话题简报没提到、但会直接影响结果的：**这个话题工作区的基线是旧的**——它挂在 `a2691f2a` 上，比当时的 main 落后十几个提交，`frontend.yml` 在那个基线里根本不存在。若直接在原基线上解，等于对着一份不存在的文件做"二选一"。

已先把工作区挪到当前 main（`b9dd6de6`），再以 `main × main@upstream` 起真正的 merge。合并落在本话题工作区，**没有碰共享底仓**。

冲突文件与父话题判断一致，正好五个，全在 `.github/workflows/`。

## 逐文件怎么解的

| 文件 | 结果 | 取舍 |
|---|---|---|
| <&.github/workflows/build-tmux.yml> | 上游为底 + 本地的 `timeout-minutes: 30` | SHA 固定全留；超时取 30m（有实测：构建 4.8m、2026-08-07 wedged job 占 8h11m），上游 60m 的理由（对齐 build.yml 的 build-sandbox）写进注释保留 |
| <&.github/workflows/deploy-dev.yml> | 两边**都要** | 本地的 `runs-on: ubuntu-latest`（含 1h45m 排队事故、执行 0.0m 的实测）＋ 上游的 `timeout-minutes: 5` 与 SHA 固定。注释里写明：挪到 hosted 后"占用盒子唯一槽位"这个理由失效，但 5m 上限改成防 hosted 分钟数被 6h 默认值吃掉，仍然保留 |
| <&.github/workflows/e2e.yml> | 上游为底 + 本地三处实质内容 | 上游：`permissions: contents: read` ＋ SHA 固定 ＋ sha 并发键。本地补回：三个 cheese-ci 槽位的口径、Playwright 8081 端口冲突注释、**收回 postgres 匿名卷的 prune 步骤**（57 卷/19GB 撑爆过一次，池机只有 40G，这步上游没有） |
| <&.github/workflows/test.yml> | 上游为底 + 本地两处注释重写 | 上游：`permissions` ＋ SHA 固定。本地补回：cheese-ci 池的口径、cargo target 目录按 job 名分开的新理由（"第二台 runner 是 P3 设想" → 已经是三台，provision.sh 每机一个槽位） |
| <&.github/workflows/frontend.yml> | **以上游那份为底，不缝**，再挑进本地四样 | 见下 |

### frontend.yml 为什么必须以上游为底（不是风格偏好，是硬事实）

合并后的 `frontend/package.json` 是上游那份：`lint` 已经变成只读的 `eslint .`、`typecheck` 变成 ratchet（`node scripts/tsc-ratchet.mjs` + `tsc-baseline.json` 冻结 31 个存量错误）、并且多了 `packageManager: pnpm@9.15.3`。在这棵树上，本地那份 workflow **会直接坏掉两处**：

- `pnpm exec vue-tsc --noEmit` 会撞上那 31 个存量错误——直接红；
- `pnpm/action-setup@v4` 带 `version: 11`，同时 package.json 又声明了 `packageManager`，该 action 会以"指定了多个 pnpm 版本"报错退出。

所以以上游为底。但**上游那份漏掉了本地建这个文件的初衷**：它的 `Ratchet unit tests` 跑的是 `node --test scripts/*.test.mjs`，测的是 ratchet 脚本自己，**应用自己的 27 个 vitest 文件 / 289 条断言一条都没跑**。已挑进来的四样：

1. `Unit tests: pnpm exec vitest run`（补回上面这个缺口；`pnpm run test` 是 watch 模式，CI 不能用）
2. `persist-credentials: false`（package.json 有 `postinstall: husky install`，第三方脚本跑的时候没理由把 token 留在 `.git/config` 里）
3. 类型检查的 `NODE_OPTIONS=--max-old-space-size=4096`（2 核 7G 的 hosted runner 上 Node 默认堆约 2G，vue-tsc 实测峰值 1.7G）
4. 本地的实测耗时注释，以及"`vite build` 是**故意**没放进来的"那段说明——免得有人把绿勾读成它也覆盖了构建

### 并发键：两边形式不同，但在这两个 workflow 上完全等价

本地是 `${{ github.ref }}-${{ github.ref == 'refs/heads/main' && github.sha || '' }}` + `cancel-in-progress: true`；上游是 `${{ github.event_name == 'pull_request' && github.ref || github.sha }}` + `cancel-in-progress: ${{ ... == 'pull_request' }}`。

`test.yml` / `e2e.yml` 的触发器只有 `push:main` 和 `pull_request`（已确认没有 `workflow_dispatch`），两种写法在这两类事件上分组与取消行为**逐位相同**。取了上游的写法（与上游新建的 frontend.yml 一致），两边的注释理由都保留下来了。唯一遗留的不齐：`build.yml`（本次没冲突、两边一字未改）仍是本地那种写法——**没顺手统一，本卡只碰这五个文件**。

## 已经跑过的验证

- **YAML 语法**：21 个 workflow 全部 `yaml.safe_load` 通过，且都有触发块和 jobs（不只验这五个）。
- **上游新加的 pin 闸门** `.claude/scripts/check-action-pins.sh`：自测 + 全仓扫描，`PASS: every action is pinned to a commit SHA`。
- **`permissions: contents: read`**：`e2e` / `test` / `frontend` 三处都在。
- **双方观测数据逐条点名**：8h11m 占用、4.8m 构建、1h45m 排队、0.0m 执行、1.7% vs 15%/33% 取消率、三个 cheese-ci 槽位、57 卷/19GB、8081 冲突、27 文件/289 断言、实测耗时、1.7G 峰值，以及上游的"第二次合并把第一次的 run 取消掉"事故、`} catch {` 的 ReferenceError、node:21-slim 偏离说明、31 条存量类型错误——**逐条 grep 确认全在**。
- **反向核对**：`本地版 → 合并结果` 的 diff 只多出上游的东西（pin / permissions / 并发表达式 / 注释合并），本地内容一样没掉。
- **repo rules 闸门**：PASS。
- **迁移链**：本次合并对 `backend/alembic/versions/` 零改动（与 main 文件集合完全一致），不存在合并叉链。

## 在跑

- `frontend.yml` 的五个步骤在本地真跑一遍（install → lint → test:ratchet → typecheck → **vitest run**）。这是唯一一处我加了新 gating 步骤、静态看不出来结果的地方，必须实跑确认。
- 后端 `check.sh --no-tests`（ruff + pyright），递卡前先自己跑绿。

两项都出结果后递验收卡。若 vitest 在这棵树上不绿，我不会硬塞——会带着失败输出回来说明，由人决定是先修还是这张卡先不带这一步。
