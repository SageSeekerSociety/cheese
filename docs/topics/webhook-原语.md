## 目标

给外部系统一个往房间（topic）发消息的正门：`POST /webhooks/{topic_id}`，token 鉴权 + 来源标注落库，供部署结果、CI 结果等后续场景回房间用。

## 已完成

- **鉴权设计**：`<&backend/app/core/webhook_auth.py>`。参考 `sandbox_auth.py` 的 HMAC 签名思路，但语义改为长期存在、可撤销/轮换：token 内嵌 `version`，不带 `exp`。校验时把 token 里的 version 和 DB 里该话题的当前 version 比对——轮换（重新铸造）即让所有旧 token 失效，撤销同理（`WebhookTokenRepository.revoke` 单独提供）。真正的密钥本身从不落库。
- **落库表**：新增 `webhook_tokens`（`<&backend/app/domain/webhook/models.py>`，迁移 `<&backend/alembic/versions/e1f2a3b4c5d6_webhook_tokens_table.py>`），只存 `topic_id/project_id/version` 三元组。
- **铸造入口**（内部）：`POST /api/topics/{topic_id}/webhook-token`，走 `cheese` 已有的 scoped-token 鉴权闸门（加进了 `main.py` 的 `_CHEESE_WRITE_PATHS`），供 `cheese` CLI/AI 侧调用铸造或轮换。
- **接收入口**（外部）：`POST /webhooks/{topic_id}`（根挂载，不在 `/api` 下），body `{content, source}`，鉴权失败 401、缺字段 400；来源标注塞进 `Block.meta = {"source": ...}`（没新开 `AuthorType` 枚举），落库失败按 (0,5,30) 秒重试三次再放弃，参考 `dogfood_notices.py` 的模式。
- 单测 `<&backend/tests/unit/test_webhook.py>`：签名/版本校验（含篡改签名、跨话题、轮换失效）、`post_with_retries` 的来源落库 + 重试到放弃、路由层鉴权失败/校验失败/成功落地。

## 验证状态

- `ruff check` / `ruff format`：绿。
- `pyright`：0 errors（新增文件范围）。
- `pytest`：**未跑通** —— 本沙箱没有 Postgres/Docker（`docker` 命令不存在，5433 端口无监听），而 `tests/conftest.py` 的 `_pg_schema` 是 session 级 autouse fixture，任何测试（含纯单测）都会先尝试连接真实 Postgres 做 schema 迁移，因此本地无法验证。测试代码已按 `test_dogfood_notices.py` 的桩测试风格写好并经过静态检查（无 DB 依赖，纯 monkeypatch），需要在有 PG 的环境（如 `task check`）里跑一遍确认。

## 下一步 / 待确认

- 请在有 `task check` 完整基础设施的环境里跑一遍 `pytest tests/unit/test_webhook.py`，确认真正通过。
- 未做前端 token 管理页面（简报里明确不需要这一版）。
- 与 #192/#193 GitHub token 那条线无关联，未阻塞。

## 父话题当时的活文档（快照，供参考）

（父话题当时还没有活文档）
