> 结论稿。对标对象：`block/buzz`（Rust relay + Tauri 桌面 + Flutter 移动端 + CLI），只看「测试策略与本地可运行性」。参照仓库 clone 在 `tmp/buzz`（gitignored）。本轮不改我们的测试代码。

## 一句话结论

buzz 值得抄的是**三样具体东西**（`--only-changed` 预推送、失败留视频、端口占用时拒绝启动而不是复用），值得**明确不抄**的是它的两处「显式登记制」和整个 benchmark 目录。第 4 问的前提需要更正：**buzz 并没有性能回归门禁**，我们"完全没有"并不是差距。

---

## 1. 测试分层与命名

**buzz 的实际分层**（不是文档宣称的，是 `Justfile` + CI 里跑的）：

| 层 | 位置 | 触发条件 |
|---|---|---|
| unit | `just test-unit` 枚举的 8 个 crate | 无基础设施 |
| integration | `scripts/run-tests.sh integration` | 需 PG + Redis（docker 自动起） |
| e2e | `crates/buzz-test-client/tests/e2e_*.rs`，全部 `#[ignore]` | 需活着的 relay，手动 `-- --ignored` |
| conformance | `crates/buzz-conformance` | 无基础设施，**跑在 unit job 里** |
| playwright | `desktop/tests/e2e/`，分 `smoke` / `integration` 两个 project | 需构建产物 / 需 relay |

关键观察：**它的分层轴是「运行条件」而不是「测试对象」**——`#[ignore]` 就是"需要外部依赖"的标记，conformance 明明是最抽象的一层却跑在 unit job 里，理由写在 Justfile 注释里："pure in-process trace replay — so it belongs in the unit job"。

我们的 unit / integration / contract 用的是同一根轴（unit = 不碰 DB），只有 contract 是按对象切的（API 形状）。**这个不一致无害**，contract 本来就是 DB-backed，跟 integration 同条件、不同意图，分开是为了读的人，不是为了跑的人。不建议改。

**唯一真正有意思的一层是 conformance。** 它是 `docs/spec/MultiTenantRelay.tla` 这份 TLA+ 规约的运行时对账器：relay 在 ingest/auth/read 接缝上吐 trace，checker 用**独立重写的**转移关系回放。`Cargo.toml` 里写死了独立性规则——不许依赖任何生产 crate，连 `buzz_core::CommunityId` 都不许复用，"so the checker cannot inherit a bug from production type machinery"。它的北极星写得很好：

> Don't ask "did the model pass." Ask "did the running code emit a trace the model accepts."

**但这层我们抄不了，也不该抄**：它的前提是先有一份形式化规约。我们没有 TLA+ 规约，为了上这层去补一份规约的成本远大于收益。可以剥离出来的只有那条**独立性约定**——校验器不复用被校验方的类型和函数。我们真要用，场景是权限判定这类不变量密集的地方，而不是新起一层测试。本轮不建议动。

**明确不要抄的：枚举登记制。** `just test-unit` 是一串手写的 `cargo nextest run -p <crate>`，Justfile 自己的注释承认了后果：

> nothing in CI runs `cargo test --workspace` — workspace membership alone buys clippy/check, not a single executed test.

也就是说，新加一个 crate，它的测试**默认一行都不跑**，直到有人想起来补一行。`desktop/playwright.config.ts` 是同一个病：`smoke` project 的 `testMatch` 是约 130 条手写的 glob，AGENTS.md 还专门叮嘱"写完 spec 记得去 config 里登记"。

我们的 `testpaths = ["tests"]` + 按约定收集是对的。**新写的测试默认会跑**，这个性质比它换来的任何细粒度控制都值钱。

---

## 2. 起测试依赖的手法

对比对象是 `scripts/start-isolated-test-relay.sh`（235 行）和我们的 <&.claude/scripts/dev-db.sh>（187 行）。两者解决的是**不同的问题**：

- buzz：机器上已经有别的 relay 和 dev 栈在跑，要**在冲突中挖出一块隔离区**。手法是命名 compose project（`buzz-harness`）+ 整块端口位移（PG 5471 / Redis 6471 / relay 3030）+ 每次启动 `DROP SCHEMA public CASCADE` 重置 + 单一写入者 seed 脚本。
- 我们：沙箱里**什么都没有**（无 docker、无 PG），要凭空造出服务。手法是 `uv` 拉 `pgserver` + `redislite` 两个预编译 wheel，跑在一个一次性的 3.12 解释器上。

各自的约束不同，架构没得比。有三点细节值得讲：

**(a) 端口占用时的行为——这是我们该改的。** buzz 的脚本在启动前检查端口，占用就**直接退出**：

```
err "Port ${RELAY_MAIN} is already in use; refusing to report a stale relay as this harness."
```

我们的 dev-db.sh 相反，是 `log "postgres already running on port $PG_PORT"` 然后**复用**。在沙箱里这通常没事（就是我们自己上一轮留下的），但它复用的是"端口上有个 PG"，不是"端口上有个**我们的** PG"——一旦复用到了一个 schema 不对、或迁移版本更老的实例，症状会表现成一堆莫名其妙的测试失败，而不是一句"端口被占"。这是个**真实的误诊放大器**，成本也低：复用前确认一下 role/库名/数据目录对得上，对不上就报错退出。建议改，但不在本轮。

**(b) tmux 守护那段值得知道，但不适用于我们。** buzz 用 tmux 起 relay 而不是前台，注释解释了原因：脚本从临时 shell 调起，进程组被回收会在几秒后 SIGTERM 掉前台进程。我们的 dev-db.sh 用 `pg_ctl start` 和 redis 自己的 daemonize，本来就正确脱离了，不需要这招。而且沙箱**没有 procps**（见 <&CLAUDE.md>），任何依赖 `kill`/`pgrep`/tmux 的方案在这里反而会碎。

**(c) 我们在 DB 隔离上比它强，不用自卑。** buzz 是"每次启动重置整个 schema"这种粗粒度做法。我们的 <&backend/tests/conftest.py> 是按迁移历史指纹建 template 库、per-worker clone、每个测试 truncate——粒度和并行度都高一档。这块没有可抄的。

---

## 3. e2e / Playwright

**配置层面基本平手。** 逐项对齐后，`timeout` / `retries: CI?2:0` / `workers: CI?1` / `trace: on-first-retry` / `screenshot: only-on-failure` / `reuseExistingServer: !CI` 我们和它**一模一样**，属于社区共识而非它的洞见。我们的 <&e2e/playwright.config.ts> 在注释质量上更好——每条配置都写清了是哪次真实失败逼出来的（冷编译 >30s、并行冷编译风暴、CI runner 就是 dev box 所以端口要可覆盖、`pnpm run dev -- --port` 把 `--` 当成位置参数导致 vite 静默服务不存在的目录）。

**它有而我们没有、值得抄的，按性价比排序：**

1. **`playwright test --only-changed=origin/main`**（它的 `just desktop-e2e-pre-push`）。只跑相对 main 改动过的 spec。一行命令，推送前的反馈环从"整套"缩到"你动过的"。我们 e2e 套件虽小，但 CI 上是串行跑的，这个收益是实的。
2. **`video: "retain-on-failure"`**。一行配置。我们的 CI runner 就是 dev box，失败现场很难本地复现，trace 之外再留一段视频，排查成本差很多。
3. **per-project 的 `expect.timeout`**（它给 relay-backed 的 `integration` project 单独放宽到 CI 15s）。我们现在是全局一档；等我们的 e2e 分出"轻/重"两组时再说，现在不急。

**它的两条 UI 测试纪律也是真经验**，将来我们做截图类断言会撞上：截图前必须等 CSS 动画结束（`toBeVisible()` 会在动画中途就 resolve），以及发图前用 `shasum` 卡一遍哈希唯一性——因为同一个 grid 里的多个元素做全页截图会产出**逐字节相同**的 PNG，看起来却像是拍了好几张。

**不要抄的**：前面说过的 `testMatch` 白名单。

**我们已经比它强的地方**：<&.claude/rules/e2e.md> 里那几条 buzz 没有对应物——登录限流器状态存在 Redis 里**跨运行存活**（且 `retries: 2` 一次就烧 3 次尝试）、seed 链条依赖、以及那条最狠的：

> A new spec only counts once you've seen it run in CI.

有意思的是 buzz 有**一模一样的风险却没写**：它的 `ci.yml` 用 `dorny/paths-filter` 按路径跳过重活，我们的 `test.yml` 用 scope gate 跳过 squash-merge 后的重复跑——两边都可能把"跳过"显示成绿。这条我们该继续留着。

---

## 4. 性能回归：前提需要更正

**读完之后，第 4 问的前提是错的。buzz 没有性能回归门禁。** 逐个查证：

- `benchmarks/harbor-buzz-orchestra/` **不是性能基准**，是 Terminal-Bench 式的**agent 能力排行榜**（manifest、persona、容器 runtime、leaderboard 脚本）。
- `benchmark-harbor.yml` 这个 workflow 只对上面那个目录的 Python 代码跑 `pytest -q` + `ruff check`，**从不执行 benchmark 本身**，也不比对任何数字。它是一个普通的 Python 单测 job，名字容易误导。
- `perf/` 是三个文件（一个 558 行脚本 + 一份说明 + 65 行测试），为**某一个 PR 的 Redis 扇出扩展性主张**做一次性举证。`grep` 确认：CI 和 Justfile **都没有引用 `perf/`**。
- 没有 criterion，没有任何 `[[bench]]` target。

**它真正在做的性能防护是另一种形状**——不建新层，把**阈值断言塞进已有的套件**：
- `cold-switch-longtask.perf.ts` 直接躺在 playwright `smoke` project 的 testMatch 里；
- `g3_renderer_acquire_stays_within_frame_budget` 是个 `#[ignore]` 的 Rust 测试，由 `just desktop-terminal-performance-test` 单独调起。

`perf/RELAY_BUS_SCALING.md` 里那个设计是唯一值得记住的点子：**把性能写成可证伪的断言而不是可追踪的数字**——除非实测削减达到理想值的 95%、且 scoped 模式下无关投递为 0，否则脚本非零退出。"That makes the scaling claim load-bearing."

**建议：不补。** 理由不是"没空"，是两条针对我们的具体反对：

1. 新起一个 perf 层要配一个 CI job，而**我们的 CI runner 就是 dev box 本身**（这事已经写在 <&e2e/playwright.config.ts> 的注释里了，端口可覆盖就是为了这个）。在一台同时在跑 dev 部署、还在构建和发布的机器上做时延断言，产出的是噪声，不是信号——它会变成第二个"红了先重跑一次"的东西，反过来腐蚀现有门禁的可信度。
2. 我们目前没有任何由性能引发的实际问题在待办里。为不存在的问题建门禁，代价是每个 PR 都要付。

真到了需要的那天，正确形状是**照 buzz 的做法**：在现有 e2e 里加一条带明确预算的断言，而不是开 `benchmarks/` 目录、开新 workflow。

---

## 5. agent 友好度

buzz 的 `AGENTS.md`（就是它的 `CLAUDE.md`）比我们的 <&CLAUDE.md> 长得多，但**多出来的部分大多是我们已经拆进 `.claude/` 的东西**。机制上我们更好：它是一个大文件全量常驻，我们是 `.claude/rules/` 按路径自动加载 + skill 按需调用，token 花在刀刃上。

**它写得比我们好的一类内容：把"看起来像产品 bug 的环境陷阱"点破。** 最好的例子是 e2e 构建那条——用 `pnpm run build` 而不是 `pnpm build:e2e`，mock bridge 不会被编进去，于是每个 spec 都报 `Cannot read properties of undefined (reading 'invoke')`，界面渲染成 "Community connection failed"：

> That looks exactly like a product bug rather than a build mistake, so it burns real time.

这个**写作角度**值得学：不只说"该怎么做"，而是说"做错了会长成什么样、为什么会骗到你"。

**而我们在这件事上其实已经有一个更强的样本**——CLAUDE.md 里沙箱那段：直接点名 65 个已知失败、拆成 procps 缺失（22 个）和 git identity 缺失（43 个）两类、说明"这是缺主机工具不是代码缺陷"、给出验证方法（改与不改 conftest 跑出**逐字节相同**的失败集合），最后一句"Don't spend time re-diagnosing them"。**buzz 全文没有任何等价物。** 这段是两个仓库里单条价值最高的 agent 文档，方向是对的，继续保持。

**可以补的一件小事**：buzz 有一份明确的"agent 禁止执行的命令"清单（`NEVER run flutter run / build / clean`，只允许 `test` / `analyze` / `format`）。我们零散地有几条（box 操作只走 `deploy/deploy-docker.sh`、绝不 `git add -A`），但没有归拢成一处。不急，等下次改 CLAUDE.md 时顺手归并即可。

---

## 落到行动上

值得做的只有三条，都很小，**已全部落地**：

| 建议 | 落点 | 理由 |
|---|---|---|
| 端口占用时校验身份，不匹配就报错退出 | <&.claude/scripts/dev-db.sh> | 原来会静默复用别人的实例，把"端口冲突"伪装成一堆测试失败 |
| 加 `--only-changed=origin/main` 的预推送入口 | <&e2e/Taskfile.yml>（`test:changed`） | 一行命令换一个短得多的反馈环 |
| `video: 'retain-on-failure'` | <&e2e/playwright.config.ts> | 一行配置；CI runner 就是 dev box，失败现场难复现 |

### 落地时发现：真正静默的那条是 Redis，不是 Postgres

写第一条时才看清楚风险的分布，和上面第 2 节的判断有出入，记在这里：

- **Postgres 其实不会静默复用。** `pg_running()` 查的是我们自己 `$PGDATA` 里的 postmaster，端口被外人占着时它返回 false，接着 `pg_ctl start` 会因为 bind 失败而报错退出——响是响的，只是错误被埋在 log tail 里。真正会错的是**端口对不上**：同一个数据目录用不同的 `CHEESEX_DEV_PG_PORT` 再起一次，会打印 "already running on port 5433" 然后导出另一个端口的连接串。
- **Redis 才是真的静默。** `redis_running()` 只是往端口上 `ping` 一下，**任何** Redis 都能满足它；而默认端口 6379 正是标准端口，dev box 上一定有真的 Valkey 在跑。也就是说：在门禁 runner（就是 dev box）上跑 dev-db.sh，测试会直接连到线上开发环境那个 Valkey 上去，限流锁定和会话状态双向串。
- **`stop` 比 `start` 更危险。** 旧的 `cmd_stop` 只要 ping 得通就 `shutdown nosave`——那会**关掉别人的 Redis**。

身份判据用的是"数据目录"而不是版本号：Redis 比 `CONFIG GET dir`（服务端自己的 cwd），Postgres 读 `postmaster.pid` 第 4 行的端口。拒绝的方向取安全侧——`CONFIG GET` 被改名或禁用导致问不出来，也算"不是我们的"，因为我们自己起的那个从不拒答。

明确**不做**的：不建 conformance 层（缺形式化规约这个前提）、不建 `benchmarks/` 或性能 CI job（buzz 自己也没有，且我们的 runner 环境会让时延断言变噪声）、不学枚举登记制（新测试默认要能跑）。
