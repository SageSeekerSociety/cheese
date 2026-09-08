## 目标

给外部系统一个往房间（topic）发消息的正门：`POST /webhooks/{topic_id}`，token 鉴权 + 来源标注落库，供部署结果、CI 结果等后续场景回房间用。

## 已完成

- **鉴权设计**：`<&backend/app/core/webhook_auth.py>`。参考 `sandbox_auth.py` 的 HMAC 签名思路，但语义改为长期存在、可撤销/轮换：token 内嵌 `version`，不带 `exp`。校验时把 token 里的 version 和 DB 里该话题的当前 version 比对——轮换（重新铸造）即让所有旧 token 失效，撤销同理（`WebhookTokenRepository.revoke` 单独提供）。真正的密钥本身从不落库。
- **落库表**：新增 `webhook_tokens`（`<&backend/app/domain/webhook/models.py>`，迁移 `<&backend/alembic/versions/e1f2a3b4c5d6_webhook_tokens_table.py>`），只存 `topic_id/project_id/version` 三元组。
- **铸造入口**（内部）：`POST /api/topics/{topic_id}/webhook-token`，走 `cheese` 已有的 scoped-token 鉴权闸门（加进了 `main.py` 的 `_CHEESE_WRITE_PATHS`），供 `cheese` CLI/AI 侧调用铸造或轮换。
- **接收入口**（外部）：`POST /webhooks/{topic_id}`（根挂载，不在 `/api` 下），body `{content, source}`，鉴权失败 401、缺字段 400；来源标注塞进 `Block.meta = {"source": ...}`（没新开 `AuthorType` 枚举），落库失败按 (0,5,30) 秒重试三次再放弃。
- **两层拆分**（review #190 追加要求 b）：`<&backend/app/domain/webhook/service.py>` 明确分两层——`mint()`/`verify()` 是 token 鉴权层，只被 HTTP 门面（`app.api.routes.webhooks`）调用；`post_with_retries()` 是内部共享的落地函数，不做任何鉴权，任何可信的进程内调用方（包括未来"merge 后结果回房间"那张卡）可以直接调用它、完全跳过 HTTP 和 token 校验——已在函数 docstring 里写清楚这个边界。
- **WS 匿名 author 风险**（review #190 追加要求 a）：`<&backend/app/api/routes/chat.py>` 里未带 token 的 WS 连接仍可在 payload 里自定义 `author`，属已知 Phase-0 兼容路径。这次只做了最小卫生处理（trim + cap 64 字符），没有做真实性收紧——收紧需要梳理全部现存未带 token 的调用方，超出本卡范围，留作后续独立卡。风险接受已通过 `cheese decision` 显式记录，不是悄悄放过。
- 单测 `<&backend/tests/unit/test_webhook.py>`（14 个）：签名/版本校验（含篡改签名、跨话题、轮换失效）、`post_with_retries` 的来源落库 + 重试到放弃、路由层鉴权失败/校验失败/成功落地。

## 验证状态（已在真实 Postgres 上验证，不是纯静态检查）

沙箱本身没有 Docker，但装了一个用户态 Postgres（`pgserver`，Python 包内置的 PG 16 二进制，走 `--user` pip 装，不需要 root/docker），临时监听在 `127.0.0.1:5433`，配出 `cheesex/cheesex` 角色，跑通了 `TEST_PG_BASE=postgresql+asyncpg://cheesex:cheesex@127.0.0.1:5433`：

- `ruff check` / `ruff format`：全仓库绿。
- `pyright`（走项目 `pyproject.toml` 的 `include=["app"]`，不是我手动挑文件）：**0 errors, 0 warnings**。此前误报是因为我第一次手动跑 pyright 时把 `tests/` 也传进了命令行参数，覆盖了配置的 include 范围——项目本身规定 pyright 不检查 tests/，按项目标准跑是干净的。
- `pytest tests/unit`：**2416 passed, 20 failed, 1 skipped**。`test_webhook.py` 的 14 个全过；20 个失败全部在 `test_machine_service.py`（19 个）和 `test_tmux_control.py`（1 个），都是 `FileNotFoundError: 'kill'`——这个沙箱镜像里没有 `kill`/`tmux` 二进制，是环境问题，跟本次改动无关（改动前这些测试大概率也是这个环境下失败的）。

## 下一步 / 待确认

- 未做前端 token 管理页面（简报里明确不需要这一版）。
- 与 #192/#193 GitHub token 那条线无关联，未阻塞。
- chat.py WS 匿名 author 的真正收紧留作后续独立卡（决策记录已建）。

## 父话题当时的活文档（快照，供参考）

（父话题当时还没有活文档）
