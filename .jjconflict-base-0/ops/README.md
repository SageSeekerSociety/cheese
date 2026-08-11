# 操作请求（operation requests）

**现在这里只登记，不执行。** 没有任何东西会因为这个目录里多了一个文件而跑起来。
执行链（`pull_request_review` → 真跑）是后面一步，且必须从只读操作起步。

## 为什么是文件

GitHub 拒绝创建没有 commit 的 PR（`422 No commits between`）。所以"用 PR 当授权信封"
不能理解成"开一个空 PR 装授权"——那在机制层面不成立。

补法：**把操作请求写成清单文件，清单本身就是那个 diff。**

```
清单是 diff  →  PR 装着它当授权与留痕容器  →  （后续步骤）人批准 = 授权这一次执行
```

## 格式

```
ops/requests/<topic8>-<operation_id>.yaml
```

`<topic8>` 是话题 uuid 的前 8 位，`<operation_id>` 取自 registry。文件名和文件里的
`topic_id` / `operation_id` 不一致会当场校验失败。

一份完整的清单（`resolved:` 段由工具生成）：

```yaml
# 操作请求（operation request）。这份文件本身就是授权 diff：
#   人在 PR 上批准 = 授权这一次执行。改了 args 就要重新批。
# `resolved:` 一段由 registry 生成，请勿手改 —— CI 会重算并比对。
#   重新生成：uv run python scripts/ops_manifest.py render <本文件>
schema_version: 1
operation_id: deploy.dev
topic_id: b2bcbe11
requested_by: andy
reason: dev box 落后 main 三个提交，联调前先对齐
authorization: once
args:
  commit_sha: 9f4c1d0b7a3e5628cf10b4d92a7e6531c08fa2b4
resolved:
  what: 以 9f4c1d0b7a3e 的 per-commit 镜像跑 deploy/deploy-docker.sh（pull → migrate → up → health-check）
  where: dev/test box cheese-dev-env1-app（192.168.16.5）
  blast_radius: dev
  reversibility: partial
  reversal: 重新部署上一个 commit 的镜像即可回退代码；但这一次跑过的 alembic 迁移不会自动回滚
  worst_case: dev 环境在迁移或健康检查失败后停在半升级状态，所有人的 dev 联调中断，直到有人手工修迁移
  interruptible: false
  human_approvals_required: 1
```

人在卡面上看到的**七问**就是这份文件：`operation_id` / `args` 是请求方写的，
其余五问（`what` / `where` / `reversal` / `worst_case` / `interruptible`）
**从 registry + args 推出来**，请求方改不动。

## 四条规矩

1. **registry 是权威。** `resolved:` 由 `registry.py` 生成，CI 会重算并逐字段比对。
   手改 = 校验红。这条挡的是"作者把风险描述写得好看一点"。
2. **不允许隐含的「当前」。** `main`、`latest`、`当前`、`HEAD` 这类值一律拒绝，
   目标必须写成具体 sha / 具体版本号。PR 的 `commit_id` 只锁得住"PR 的 head 没变"，
   锁不住"main 变了"。
3. **一个操作请求 PR 只能碰 `ops/requests/`。** 由必需检查（`ops-guard.yml`）强制。
   否则能把代码改动夹带进一个"看起来只是部署请求"的授权里——批准的人会用自己的身份
   连带批准那段代码。
4. **每次执行都要一次新的 approve（拍板 6）。** `authorization: envelope`
   （长期信封 PR）只开放给 `blast_radius: none` 的操作；有副作用的一律
   `once`——一次性 PR、执行完即关。**信封省的是解释成本，不是人点那一下。**

## 工具

都在 `backend/` 下跑（只依赖 pydantic + pyyaml，不连数据库）：

```bash
cd backend
uv run python scripts/ops_manifest.py describe                       # registry 里有什么
uv run python scripts/ops_manifest.py render ../ops/requests/x.yaml  # 生成/重算 resolved:
uv run python scripts/ops_manifest.py check  ../ops/requests/x.yaml  # 校验
uv run python scripts/ops_manifest.py guard  <PR 改动的文件列表...>   # PR 文件范围
```

registry 本身在 `backend/app/domain/ops/registry.py`——新增一个 operation
就是在那里加一个类（args 模型 + 七问推导），不需要动清单格式。
