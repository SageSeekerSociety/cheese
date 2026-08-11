## 状态摘要

操作卡落地顺序的**第 3 步**。第 1 步（人类授权动作前移）、第 2 步（`accept_via_pr`）已在 main。

**本卡只做「只登记不执行」**：建 operation registry + 清单（manifest）格式，跑通
「写一个操作请求 → 它变成一个只碰 `ops/requests/` 的 PR → 卡面把七问显示出来」。

## 目标（验收标准）

1. **operation registry** —— 有哪些 operation、每个的 `blast_radius`、参数 schema、可逆性。
   registry 是**权威**：卡面七问尽量从 registry + args 推出来，不让芝士自由发挥填文本。
2. **清单格式 + schema 校验** —— `ops/requests/<topic8>-<operation_id>.yaml`，字段即卡面七问
   （`operation_id` / `args` / 执行什么 / 打到哪 / 可逆吗 / 炸了会怎样 / `interruptible`）。
   **提交时就校验**，格式不对当场失败，不等到执行。
3. **硬约束：一个 ops PR 只能碰 `ops/requests/` 下的文件** —— 由必需检查强制。
   即使还不执行也必须先立，否则格式一旦被用起来就晚了。
4. **禁止隐含的「当前」** —— `commit_id` 只锁得住「PR 的 head 没变」，锁不住「main 变了」。
   一切目标写成具体 sha / 具体版本，**schema 层面拒绝**「当前 main」这类隐含参数。
5. **卡面** —— 把七问显示出来即可，**不接执行结果回写**。

## 范围红线（绝对不要越）

- **不接执行链**。`pull_request_review` 触发执行是第 4 步，且必须从 device-smoke（只读）起步；
  prod 是第 5 步且永远两次人工授权。
- **拍板 6（2026-08-11）：每次执行都要一次新的 approve。** 长期信封 PR 只允许
  `blast_radius: none`；有副作用的一律一次性 PR、执行完即关。信封省的是解释成本，不是人点那一下。
  → `blast_radius` 就是这条拍板的落点，**不设计成「授权一次可反复执行」**。
- **开 PR 只能用人的授权**，绝不用平台 App 的 `write_token()`。
  **不需要给 App 加任何新权限**——若方案需要加权限，说明走错了，停下报告。

## 约束

- **并发**：`backend/app/domain/review/` 有四条线在跑，`backend/app/domain/topic/` 有一条。
  本卡优势是**几乎全是新建文件**（新 `ops` 域、`ops/` 目录、新 workflow）——
  diff 尽量限制在新文件里，改既有文件要说明为什么。基于最新 main 开工。
- 分层 Route → Service → Repository → Model；Pydantic v2；全类型标注，不用 `Any`。
- **本步不落库 → 不写 alembic 迁移**（顺带躲开 main 上曾出现的 2 heads）。
- 测试至少覆盖：合法清单通过、隐含「当前 main」被拒、ops PR 夹带非 `ops/requests/` 文件被拒。

## 进展

- [x] 接手、范围确认、活文档改写
- [x] `backend/app/domain/ops/` registry + 清单 schema + 校验器 + 七问推导
      （`registry.py` / `manifest.py` / `guard.py` / `schemas.py` / `services.py`）
- [x] `ops/requests/` 目录约定 + 格式说明（`ops/README.md`）
- [x] CI 必需检查 `ops-guard.yml`：ops PR 路径白名单 + 全量清单校验
- [x] 卡面七问展示：`GET /api/projects/{id}/operation-requests`（只读，不接执行回写）
- [x] 单测 472 行（合法清单、隐含「当前」被拒、夹带非 ops 文件被拒）
- [x] 检查跑绿：ruff 通过、alembic 单 head、ops 单测 40 passed、
      pyright 对 `app/domain/ops` + `routes/ops.py` + `scripts/ops_manifest.py` 0 errors
      （沙箱没有 Postgres，check.sh 跳过全量 pytest；CI 上会跑）

## 落地形态（与最初设想的两处差异）

- **不提交示例清单文件。** 规矩 3 要求「ops PR 只能碰 `ops/requests/`」，而本 PR 同时带着
  backend 代码——真放一份 `.yaml` 进去，`ops-guard` 会当场把自己这个 PR 判红。
  所以示例以完整 YAML 的形式写在 <&ops/README.md> 里，`ops/requests/` 本体只留 `.gitkeep`。
  第一份真清单会是它自己那一个 PR，这正是格式想要的形状。
- **卡面停在 API 层。** 后端把七问按 registry 推导好后整份吐出来，前端渲染留给接执行链的那一步——
  本卡范围内没有必要动 `frontend/`，也就不去和并发的几条线抢文件。

## 改动范围

15 个文件、全部新建（另加 `pyproject.toml` / `uv.lock` 各一行：pyyaml）。
没有 alembic 迁移，没有触碰 `backend/app/domain/review/` 与 `topic/`。

## 下一步

第 4 步（`pull_request_review` 触发执行，从 device-smoke 只读起步）不在本卡范围内。
