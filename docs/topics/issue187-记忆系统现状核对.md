# issue #187（记忆系统）· 2026-08-17

<@caisongyang> 要求：把 OpenViking 通上电，「假装你有了 embedding key」。

## 一句话

**第 2 步（把 OpenViking 通上电）已经做完了，除了那把真钥匙。** 部署侧缺的三样里，`.viking` 持久化挂载和 backend 切换的整条链路都补齐了；embedding key 用一个本地假端点顶上，把 remember → 抽取 → 落盘 → 建索引 → 语义检索 → 重启后还在 → 存量记忆迁移这一整条路**真跑了一遍**，绿的。现在只差有人塞一把智谱 key 进 dev 的 `.env` 再重部一次。

## 这一轮改了什么（都在工作区，未递卡）

| 文件 | 改动 | 为什么 |
|---|---|---|
| `deploy/compose/docker-compose.base.yml` | backend 加 `OPENVIKING_DATA_DIR=/data/viking` + 绑定挂载 `${VIKING_HOST_PATH:-/home/nictheboy/cheese-viking}:/data/viking` | issue 点名的「部署一次记忆清零」。无条件挂载：db 后端下它只是个空目录，但这样切后端就只是改一行 env，不用再动部署文件 |
| `deploy/deploy-docker.sh` | 部署前 `mkdir -p` 该目录，并把它加进属主移交清单 | 目录不存在时 docker 会用 root 建，后端（uid 1000）写不进去；移交脚本本身会跳过不存在的路径，所以必须先建 |
| `deploy/tests/test-app-tier-health.sh` | 把 viking 路径纳入这套部署自测 | 否则测试会去真的 `/home/nictheboy/cheese-viking` 上乱建目录 |
| `backend/app/main.py` | lifespan 退出时关闭 OpenViking 运行时（仅 openviking 后端） | 之前没人管它的生命周期，重部会把进程按在写一半的状态上——挂载是持久的，但里面的东西得是一致的 |
| `backend/Dockerfile` | 单独 COPY `scripts/migrate_memory_to_openviking.py` 进生产镜像 | 生产镜像原本不带 `scripts/`，等于存量记忆迁移**在机器上根本跑不了**。只点名这一个文件，其余是开发探针和 demo 种子，不该进生产镜像 |
| `backend/.env.example` | 补全 `MEMORY_BACKEND` / `OPENVIKING_*` 一节 | 之前这批配置在 example 里完全没有，运维得读源码才知道要填什么 |
| `docs/infrastructure.md` | 新增《Turning on the openviking memory backend》运维步骤 + 属主段落补 `VIKING_HOST_PATH` | 切换步骤、迁移命令、两个必须先知道的事（见下） |
| `backend/tests/support/fake_model_endpoint.py`（新） | 本地 OpenAI 协议假端点：embedding + chat | 就是那把「假装有了的 key」 |
| `backend/tests/integration/test_openviking_fake_endpoint.py`（新） | 无 key 无网络的全链路回归 | 让这条路在钥匙到位之前不会悄悄烂掉 |

## 「假装有 key」具体是什么

不是把校验绕过去，是**真起了一个本地 OpenAI 协议服务**顶替智谱：

- **embedding**：字符 3-gram 哈希成向量再归一化。是**真向量**，只是没学过——余弦相似度反映的是字面重合度。检索是通的，只是比训练过的模型弱。
- **chat**（OpenViking 的抽取器）：读 `ExtractLoop` 塞在 system prompt 里的那份 JSON Schema，按 schema 造一条合法的记忆条目，正文放对话原文。所以**写盘/建索引/检索这条路是真的**，只有「抽取得好不好」是假的。

## 实测到什么（都是本地真跑，不是推演）

- `backend/tests/integration/test_openviking_fake_endpoint.py` **2 个用例、10.6 秒、全绿**：remember → 后台抽取 → recall 拿到卡片、`count` 对得上、另一个 project 的空间是空的（作用域隔离）、向量检索命中且 URI 都在本作用域内、L2 读取、`forget` 后消失，并且断言两个端点收到的 model 名就是配置里那两个（证明 ov.conf 真的驱动了 provider，没有静默走默认）。
- **重启后还在**：把嵌入式实例整个关掉再开（等价于容器重建），同一个数据目录 recall 出来的东西一字不差。这就是 `.viking` 必须挂载的直接证据。
- **存量迁移脚本真跑通了**：往 PG 塞 3 条 `memory_entries`，跑 `scripts/migrate_memory_to_openviking.py` → 3 migrated / 0 failed；换个进程 recall 出 3 张卡；`search "git identity"` 排序正确（目标卡 0.543，无关卡 0.112）；**再跑一次是 `nothing to do`**（幂等）。
- 全量 `tests/unit` + `test_health.py`：**3311 passed, 1 skipped**。质量闸门 `check.sh --no-tests`：**7/7 passed**（这台机器没 PG，pytest 那项按惯例 SKIP）。
- `deploy/tests/test-app-tier-health.sh` 全部 PASS，包括属主移交顺序和回滚那几条——部署脚本的改动没有破坏「移交是不可逆的最后一步」这条不变量。

## 顺手发现的两个真问题（已在上面修掉）

1. **生产镜像里没有 `scripts/`。** 也就是说，就算钥匙到位、后端切过去了，把存量记忆搬进 OpenViking 的那个迁移脚本，在机器上**是不存在的**。不写进镜像的话，切换当天才会发现。
2. **没有人关闭 OpenViking 运行时。** 整棵记忆树（AGFS + 向量索引）活在一个嵌入式实例里，`lifespan` 里没有对应的关闭动作，重部就是拔电。

## 还差什么（只剩这一件）

**一把真的智谱 key。** 拿到之后在 dev 的 `backend/.env` 里加三行，然后按 <&docs/infrastructure.md> 里那节重部一次当前 sha（`docker restart` 不生效，env_file 是建容器时读的）：

```
MEMORY_BACKEND=openviking
OPENVIKING_LLM_API_KEY=<key>
OPENVIKING_EMBEDDING_API_KEY=<key>
```

再 `docker exec -w /app cheese-backend-1 python scripts/migrate_memory_to_openviking.py --dry-run`，看着没问题去掉 `--dry-run`。

翻牌前有两件事得先认下来：

- **那个目录就是数据库。** 不在 Postgres 里、不在镜像里。现有备份只覆盖 PG 和 uploads，**`.viking` 不在任何备份里**——要让这些记忆活过一次机器重建，得单独给它加一条备份。
- **key 买的不只是向量，还有抽取。** 每记一条事实都要花一次 chat 调用（OpenViking 的抽取器）加若干次 embedding。只在 embedding 端点有效的 key，换来的是一个什么都存不下的后端。

## 一个我不建议做的事

有人可能会想：既然假端点能跑，那就先拿它在 dev 上把 openviking 开起来。**别。** 假抽取器不会判断什么值得记，它只会把整段对话原样塞成一张卡——真钥匙到位时，你要先清一遍垃圾。这套假端点的位置是回归测试，不是临时后端。

## 没动的部分

- 第 3 步（观察抽取质量）依赖真 key，仍然开不了工。
- 第 4 步 dreaming 未开始（`backend/app` 全域 grep `dream` 仍是零命中）。
- #271 抛出来、至今没人拍的两个决定，仍然没人拍：**记忆作用域**（代码里还是 C：只读 per-agent 池 + 遗留共享池，`chat.py:1469`）和**显著性分层**（`MemoryEntry` 里仍无 `kind` 列）。这两件**不依赖任何外部 key**，现在是这个 issue 上唯一能立刻推进的东西，问题递给了 <@wangchangxin>。
- issue #187 本身还开着，8/9 之后没人在上面留过字。
