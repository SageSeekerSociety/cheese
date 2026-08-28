# 没有 docker 的机器上怎么跑测试

一台既没有 Postgres 也没有 docker 的容器**照样能跑全量测试**——DB-backed 的测试要的是一个服务器，不是 docker。`.claude/scripts/dev-db.sh` 用预编译 wheel（`pgserver`、`redislite`，由 `uv` 取）把 Postgres + Redis 起起来：

```bash
eval "$(bash .claude/scripts/dev-db.sh start)"   # 导出 TEST_PG_BASE + REDIS_URL
cd backend && uv run pytest tests/ -n 4 -q
bash .claude/scripts/dev-db.sh stop --purge      # 停掉并删数据目录
```

- **Redis 是必须的,不是可选**——2FA/登录/会话状态都在里面,没有它 integration 套件直接报错。
- 这两个 wheel **故意不是 backend 依赖**:`pgserver` 没有 cp313 wheel,而本项目 `requires-python >=3.13`,声明进去会让依赖解析失败。脚本自己钉住版本,并把它们跑在一个一次性的 3.12 解释器上;测试本身仍跑在 3.13。
- `tests/unit/` 完全不需要服务器（`conftest.py` 只给主动要的测试建 schema）。`tests/unit/` 以外的一律是 DB-backed。
- `check.sh --no-tests`（质量闸门用的那条）完全跳过 pytest——要真正跑套件就用上面的方子。

## 跑之前先配一个 git identity

全新容器没有,重建一次又会被抹掉,所以失败重新出现时就再跑一遍:

```bash
git config --global user.email "you@example.com"
git config --global user.name  "Your Name"
```

没有它 `git commit` 会拒绝,于是**约 43 个测试**在任何真正建 worktree 的地方失败（`test_workspace.py`、`test_upstream.py`、`test_accept*.py`、`test_git_http.py`、`test_attachments.py` 等等）。`GIT_*` 环境变量在这里**不管用**——conftest 故意把它们剥掉了（见该文件顶部注释）——但它从不碰 global config,所以 `git config` 这条路能走通。已验证:配上之后这一整批全绿。

## 还要装 `jj`

和 git identity 同一个故事:容器重建会抹掉,`uv sync` 也不会把它装回来。症状认过一次就再不会认错——每个建真实工作区的 DB-backed 测试都死在 `FileNotFoundError: [Errno 2] No such file or directory: 'jj'`（光 `test_upstream.py` 就 10 个）。项目仓库是 jj-colocated 的,所以应用会去 shell out 到这个二进制。装 CI 用的同一版本（见 `.github/workflows/test.yml`）:

```bash
curl -sSL https://github.com/jj-vcs/jj/releases/download/v0.43.0/jj-v0.43.0-x86_64-unknown-linux-musl.tar.gz \
  | tar -xz -C /tmp/jj-dl && mv /tmp/jj-dl/jj ~/.local/bin/jj && jj --version
```

## 剩下三个缺口:是缺环境,不是代码有 bug

**别再花时间重新诊断它们**。三个都由 [#516](https://github.com/SageSeekerSociety/cheese/issues/516) 跟踪——正确的修法是把它们装进镜像,而不是让每个仓库在自己的文档里教人绕开。

- **没有 procps**（`ps`/`pgrep`/`kill` 这些二进制不存在;bash 的 `kill` 只是内建）→ `test_machine_service.py` 里报 `FileNotFoundError: 'kill'`。
- **没有 openssh-client**（`ssh-keygen` 不存在）→ `test_machine_service.py` 里 21 个失败,报 `FileNotFoundError: 'ssh-keygen'`。和 procps 那个缺口在同一个文件里,所以两者看起来像一坨 22 个失败——它们不是一回事。
- **没有 provider 凭据** → 1 个失败,`test_market_api.py::test_market_lists_ai_and_compute_pools`。一个 profile 的 `available` 是 `bool(auth_token or oauth_token)`（`app/domain/agent/profiles.py`）,所以 `settings.anthropic_auth_token` 没设时,默认 AI 池诚实地报告不可用。随便给个非空值就解决——`ANTHROPIC_AUTH_TOKEN=dummy-for-test uv run pytest tests/integration/test_market_api.py` 就绿了,过程中不会真去调 provider。
