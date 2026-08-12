## 目标

把 `backend/srp_rs`（Rust/pyo3 扩展）的 Cargo 构建缓存持久化到 checkout 之外，消除 `test.yml`（`migration-heads`、`test`）与 `e2e.yml`（`e2e`）三个 job 里每次 `actions/checkout` 清空工作区后全量重编译 Rust 的开销（研究卡实测 41~44 秒/次）。

## 改动

- `test.yml`：`migration-heads`、`test` 两个 job 各自加 `env: CARGO_TARGET_DIR: ${{ runner.temp }}/../cargo-target/<job名>`。
- `e2e.yml`：`e2e` job 同样加 `CARGO_TARGET_DIR: ${{ runner.temp }}/../cargo-target/e2e`。
- 三个 job 在 `uv sync`（触发 srp_rs 编译的那一步）之后各加一步 `du -sh "$CARGO_TARGET_DIR"`，把实际缓存大小打进 CI 日志，供以后持续观察，不用只靠这次的一次性估算。
- 只动了这两个 workflow 文件，未碰 `claude-review.yml` / `deploy-dev.yml` 门禁 / `build.yml` concurrency / 任何迁移文件 / `_summarize_runs`，也没有动 `pnpm install` 那条问题，也没有把 `pytest -n auto` 改成 `-n 4`。

## 设计决策

1. **路径选择**：`${{ runner.temp }}/..`——沿用仓库里 `microcloud-smoke.yml` 已经验证过的同款模式（"survives the checkout clean"），即 self-hosted runner `_work` 根目录下的一个兄弟目录，不在任何 job 的 checkout 目录之内，不会被 `actions/checkout` 清空。本地已实测确认这套机制本身工作正常（见"已验证"）。

2. **并发风险处理：按 job 名分目录，不共享**。`migration-heads`/`test`/`e2e` 三个 job 各自读写独立路径，物理上不存在同一目录的并发写入，Cargo 目录锁的排队/损坏风险完全不存在，不是"接受风险"而是"消除了风险发生的前提"。
   代价：三个 job 各自独立预热（首次各自冷编译一次），不能"一个 job 编译完，另一个直接复用"。但这两个 job 在改动前本来就完全不共享缓存（都是从零编译），所以这不是损失了既有收益，只是没有拿到一个从未存在过的额外收益。
   **加并行度之后会怎样**（P3，加机器）：路径按 job 名分、落在每台机器自己的本地磁盘上，新机器对每个 job 类型只需冷启动一次、之后本机保温，机器之间不存在共享文件系统，也就没有跨机器竞争。唯一没覆盖、也不假装它不存在的场景：同一物理 runner 上跑两个**同名** job 的并发实例——这需要一台机器注册多个 runner 进程，不是当前的架构，如果以后真变成这样，需要在路径里再加一层 run id 之类的区分。

3. **不会被 `deploy-docker.sh` 的 prune 波及**：`docker image prune -af` / `docker builder prune` 的作用域是 Docker 自己的 image/build-cache 存储，机制上就够不到 runner `_work` 目录下的任意路径——这不是"选对了地方侥幸躲开"，是这类 prune 命令的作用范围本来就在 Docker storage 内部，跟选哪个宿主机路径无关。

4. **磁盘量级**：本地实测（手动触发一次 srp_rs 重新编译）单个 job 的 `CARGO_TARGET_DIR` 约 132MB；三个 job 独立目录合计约 400MB 量级，相对 box 目前约 55% 的磁盘占用是很小的增量。已加的 `du -sh` 步骤会在每次真实 CI 运行的日志里给出当时的实际数字，往后不用靠这次的一次性估算。

## 已验证

- **机制本身**：本地（同一套 uv/maturin/cargo 工具链，非 CI 环境）设置 `CARGO_TARGET_DIR` 后跑 `uv sync --reinstall-package srp-rs`，编译产物精确落在外部目录（`release/libsrp_rs.so` 等，mtime 是编译时刻），原地 `backend/srp_rs/target` 里的文件 mtime 完全没变——证明确实是"重定向"而不是"两边都编译了一份"。
- **ruff**：0 错误。**pyright**：0 错误。
- **pytest**：本沙箱没有可用的 Postgres（`localhost:5433` connection refused），`check.sh` 按设计直接判该项 FAIL（不是 SKIP，也不硬跑 rerun-across-3000-tests）——这是沙箱环境限制，不是这次改动引入的问题；没有为了让检查变绿而删减断言。本次改动完全不涉及 Python 代码，跟 pytest 能否连库无关。
- **真实提速收益，这次验证不了，要等合并部署之后**：判断方式是查 GitHub Actions API（`/repos/.../actions/runs` → 对应 run 的 `jobs/{id}`）里 `test.yml` 的 `Install dependencies`（即 `uv sync`）这一步，在这次改动合并前后的耗时对比——预期 Rust 编译那部分（原本 41~44 秒）在缓存热了之后大幅下降，具体数字要等真实 CI 运行才能看到。

## 下一步

- 等人工验收，重点确认"按 job 名分目录"这个并发风险处理方案是否认可。
- 提交后平台会在下一轮轮询（约 60 秒）自动推到 PR 分支，PR 触发 Backend Test / E2E。
- 合并部署后，建议实际查一次 `test` job 的 `Install dependencies` 步骤耗时，确认收益是否如预期落地。
