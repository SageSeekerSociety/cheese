> 对应 [#282](https://github.com/SageSeekerSociety/cheese/issues/282)。本轮只交**模型 + 三个决定的方案**，不改代码。

## 一句话结论

`compute_profile` 不该拆成四个给用户点的选择器，而该**在后端展开成四根轴**、在前端仍然收敛成几个具名档。「活多久」不是第五根轴，它是**两根已有轴的函数**：`supply` 约束动作的合法域，`billing` 决定时限的量级。issue 里「每一行都是第一行的推论」这句推错了源头——不是从 supply 推，是从 billing 推。第二节说明为什么，以及为什么这样推 MicroCloud 才真的不需要特例。

---

## 一、模型：一个 id → 四根轴

| 轴 | 取值 | 决定什么 | 今天在哪 |
|---|---|---|---|
| **规格** size | cores / mem / disk / GPU | 算力大小 | 池子 id + MicroCloud `offering_id` |
| **供给形式** supply | `cloud` \| `self_hosted` | 平台能不能销毁它 → 可弃性 → **坏了能不能换** | **没有名字**，靠 `ProjectMachine.device_id` 反查 |
| **可见性** visibility | `isolated` \| `host` | agent 能看见什么、能碰到谁 | **没有名字**，隐含在 provider 实现里 |
| **计费归属** billing | credit 池 id | 谁付钱 | **没有表达** |
| （派生）生命周期 lifecycle | idle→停机→销毁 的两个时限 | 这次房间占用的**所有可回收资源**活多久 | 见下表，**两类对象里有一类连硬编码都没有** |

**生命周期的对象不止「机器」。** 一次房间占用会占住至少两类可回收资源，它们今天的处境差得很远：

| 对象 | 今天的策略 | 话题归档后 |
|---|---|---|
| 机器 / 容器 | `sandbox_idle_hours = 8h` 硬编码（<&backend/app/core/config.py>） | cloud 可回收，self-hosted 不可动 |
| **工作树** | **无** | **无** |

工作树这一格是实测出来的，不是推演：dev 盒子 `/dev/vda1` 504G 已用 313G，其中 **231G（全盘 74%）是 `.worktrees/` 下每话题一棵的树**，不是缓存也不是 docker（镜像 13.5G 且 0 可回收）。平台侧的分母是**225 个话题、206 个已归档、只有 19 个活着**——**九成的树属于早就结束的活**，平均约 1G，主要是 `.venv` + `node_modules`。

代码侧对得上：`TopicService._archive_one`（<&backend/app/domain/topic/services.py>）归档时只改状态、关验收卡、写一条事件块，**磁盘上什么都不动**；整个 workspace 层只有两处 `shutil.rmtree`（<&backend/app/domain/workspace/service.py>），一处是迁移 pre-jj 的老 git 工作树，一处是拆临时 merge 工作树——**没有任何一条路径会回收话题工作树**。`sandbox_idle_hours` 那个 reaper 收的是容器，不是树。

**它的失败方式和 #185 那条是同一个**：231G 悄悄堆到 74%，是人去 `du` 才发现的，平台一个字都没说。**不是丢，是无声**——只不过这次无声的是「占着」而不是「没了」。

**档 = 轴的具名组合。** `compute_profile` 这个字段本身不删、不改类型，降级成「preset 的名字」，后端 resolve 成一个四元组。这样前端、`topic.compute_profile` / `project.settings` / `team.compute_profile` 三级继承（<&backend/app/domain/agent/chat.py>）、已有数据全部不用动，语义在后端展开。

> 这一点是我和 issue 的隐含分歧里最实际的一条：把四根轴直接暴露成四个下拉框，等于把设计难题转嫁给用户。**用户仍然只在几个档里选一个**，只是这几个档终于有了各自说得清的定义。

### 现有三档落在坐标系里

| 档 | supply | visibility | 今天的生命周期 | 坏了能换 |
|---|---|---|---|---|
| 知是本地算力 `local` | cloud | isolated | 8h idle → `docker rm` | 能（重建容器） |
| 自托管设备 `device` | self_hosted | host | 永不回收 | **不能**（`resolve_pinned_device` write-once 钉死） |
| 远程节点 `cheesed` | self_hosted | host | 永不回收 | 不能 |

**空着两格，而且正是大家反复想要的那两格：**

- **`(cloud, host)`** —— 平台开一台 VM 给这个项目，芝士直接在宿主上跑、看得见整台机器（能看见同机器的服务、能 exec 进容器），**但因为是平台开的，坏了平台能换、闲了平台能回收、钱平台算得清**。这就是 MicroCloud 的正确位置：**它不需要特例，它需要的是这一格存在**。今天它不得不 enroll 成 device，于是被迫连「常驻不回收 + 不可换」一起继承——正是 issue 说的那个「想要能看见宿主，就必须连不回收一起要」。
- **`(self_hosted, isolated)`** —— 用你自己的机器，但房间仍然关在容器里。**一台机器给多个房间用时，这才应该是默认**；今天 self-hosted 只有一档，选了就是全员裸奔在同一个宿主上。

一个字段拆成两根轴，立刻多出两档今天表达不了、但明显有人要的配置——这是我认为这个模型对的最强证据，比任何「更清晰」的说辞都硬。

### 今天 self-hosted 档的实况比 issue 描述的更重

<&backend/app/domain/agent/device_launch.py> 里，设备屏幕跑的是 `bash -lc` 写一份 `~/.claude` 然后 `exec claude --dangerously-skip-permissions`——**裸在宿主上，以设备属主的身份，且预先接受了权限门**。所以这一档不是「能看见宿主」，是**以属主身份对这台机器有完全读写权**。UI 上那句实话必须按这个事实写，不是按「能看见」写。

---

## 二、我不同意 issue 的三处

### 2.1 「每一行都是第一行的推论」——推对了形式，推错了源头

可弃性、可替换性确实是供给形式的推论。**回收时限不是**——但它也不是一根自由旋钮。

反例先摆着：一台包月的 cloud VM，8 小时没人说话就销毁是错的；一个按秒计费的 cloud 容器，常驻不回收也是错的。**同为 `cloud`，两个相反的生命周期都合法。**

关键是这两个例子的差别**不在 supply，在第四轴 billing**：

- 按秒计费 → 闲置一分钟就是漏钱 → 回收时限该是分钟级；
- 包月 → 提前销毁一分钟也省不下钱，只会丢工作树 → 就该**永不自动销毁，只停机**。

所以正确的关系是**三条**：

| | 由谁决定 |
|---|---|
| **动作的合法域**（能不能停机 / 销毁） | `supply` |
| **时限的量级**（分钟级 / 天级 / 永不） | `billing`（回收时限与计费粒度同量级） |
| **具体数字** | 档位配置 |

| supply | 合法的生命周期动作 |
|---|---|
| `cloud` | 停机 / 销毁 / 重建，都合法；量级由 billing 定 |
| `self_hosted` | **停机、销毁一律非法**；平台唯一合法动作是「不再用它」（解绑） |

**这样推比「独立可配置策略」强在两处**：一是不新增自由度——lifecycle 不是第五根轴，是已有两根轴的函数；二是 `8h` 这个数字从「拍脑袋」变成「可论证」——它对不对，取决于那一档怎么计费。

**MicroCloud 的特例也是在这里消掉的**：那台常驻 VM 是包月，直接落在「永不自动销毁、只停机」，不需要为它开任何口子。

> 一个直接后果：**决定 1 的时限不能在 billing 轴定下来之前写死。** 下面给的 8h / 7d 是「按秒/按小时计费」那一档的取值，包月档的正确取值是 `∞`（只停机不销毁）。这两个数字必须挂在档上，不能是全局常量——今天 `sandbox_idle_hours` 就是全局常量，这正是要改的形状。

### 2.2 第三个决定不是独立的第三件事

issue 把「共享机器的资源配额」列为要定的第三件。我认为它是**可见性轴的必要配套**，不是一个平行的独立议题——理由是它的触发条件是 `visibility=host`，与 supply 无关：平台自己开的 `(cloud, host)` VM 上跑五个房间，缺配额的后果和别人的机器上一模一样。挂错轴，以后就会漏掉 cloud+host 那一格。

更要紧的是：**配额挡不住 host 档真正的威胁。** `--memory 2g --cpus 2 --pids-limit 512`（<&backend/app/domain/agent/tmux_provider.py>）管的是资源，而 host 档下芝士能 `docker exec` 进同机器上别人的容器、能读写别人的工作树——**这是权限问题，任何配额都不解决**。所以这一条必须拆成两层：资源面（配额）+ 权限面（准入）。issue 只写了资源面。

### 2.3 「都是自己人」这个前提只在团队内成立

issue 说共用机器上的隔离理由是防串扰不是防偷看，「都是自己人」。但 <&backend/app/domain/device/models.py> 里 `DeviceTeamRow` 是**多对多**——一台设备可以同时绑给多个团队，绑给团队后该团队所有项目都能用。所以现有数据模型允许「两个不同团队的房间落在同一台 host 上」，那里就不是自己人了。

**这一条 issue 没覆盖，我认为必须补进准入规则**（见决定 3）。

---

## 三、三个决定

### 决定 1 · cloud 档的销毁时机

**核心：把一个动作拆成两个，销毁走预告。**

今天容器只有 `destroy` 一档（`docker rm -f`），这是它必须 8h 就动手的原因——留着不花钱但占盘。VM 不一样：**停机就不烧钱，盘还在**。所以：

| 阶段 | 触发 | 动作 | 房间里留什么 |
|---|---|---|---|
| 停机 | 空闲 **N**（按秒/按小时档取 8h，沿用今天 `sandbox_idle_hours` 的值） | stop，保留磁盘 | 轻提示：已停机省钱，下次说话自动开回来，工作树还在 |
| 预告 | 停机后 **M**（同档取 7d），**销毁前 24h** | 不动机器 | `change_alert` 强提醒：24 小时后销毁，未推回的改动会丢 |
| 销毁 | 预告到期 | destroy | 终态说明：这台机器已回收，及为什么 |
| **包月档** | —— | **只停机，M=∞，永不自动销毁** | 停机提示 |
| self_hosted | 任何时候 | **一律不停机、不销毁**，只解绑 | 解绑说明 |

**N / M 是档位参数，不是全局常量**（2.1 的直接后果）：量级由该档的计费粒度定，包月档的 M 就是 `∞`。今天 `sandbox_idle_hours` 是全局常量，这正是要改的形状。

三条理由：

1. **#185 那句「丢得无声」，无声的不是丢，是没有预告。** 所以关键的一条不是「销毁后留说明」，是**销毁前 24h 那条强提醒**——只有它是人还来得及做点什么的时刻。销毁后的说明是补记账，防不住任何事。
2. **误杀比漏机器贵得多。** issue 说「漏的是钱」，但为了不漏而把 timeout 收紧，代价是把人正在用的工作树销毁掉——那损失的是信任，不是钱。所以时限取宽，成本靠「停机」这个便宜动作兜，「销毁」这个贵动作永远走预告。
3. **硬前置：可重建之前不许回收。**（原为「工作树推回平台之前不许销毁机器」，按上表扩到所有可回收对象——两类资源共用同一条不变量，否则第二类必然被漏掉。）

#### 工作树的回收：只删「命令能重建的」

分寸不用另发明，`deploy/dev-box-disk-cleanup.sh` 已经把它写死了：**只删一条命令能重建的东西。「下一次构建慢一点」是可接受的代价，「别人的数据」不是。** 那个脚本据此不碰镜像/卷、不碰 e2e 要的浏览器二进制、不碰任何项目目录。

落到工作树上就是一条清清楚楚的界线：

| 归档话题的工作树 | 能不能删 | 为什么 |
|---|---|---|
| `.venv` / `node_modules` / `target` | **能** | `uv sync` / `pnpm i` 重建，代价是下一轮慢几分钟 |
| **整棵树** | **不能** | 没有命令能重建它 |

**「归档」不是终点**——今天上午就有人把 `issue 186` 从归档里捞回来接着干。代码上 `TopicService.unarchive` 是个幂等、无代价、连采纳记录都保留的操作，说明它本来就被设计成常规动作。树没了而话题被 unarchive，芝士会**在一个空目录里醒来**——这比省下的那 1G 贵得多，而且又是一次静默失败（launcher `mkdir -p` 任何路径，没人会看到报错）。

按上面 206/225 的分母粗算，只清可重建的那部分，量级就在 **150G+**，而且不承担任何不可逆风险。

#### 两条硬前置（都是实测出来的，不是假想）

**前置 A · 无人可通知就不许销毁。** 强提醒要有收件人，而这个项目 40 个活话题里 20 个没有 owner——`project.owner_handle` 为 `null`，`TopicService._resolve_owner`（<&backend/app/domain/topic/services.py>）的三级兜底（创建人 → 父房间 owner → 项目 owner）三级同时落空，函数注释里写明这种情况返回 `None`。

无主房间的销毁提醒会发给空气，**那就正好落回 #185 那句「丢得无声」，只是这次无声的原因换成了没人可通知**。所以：通知发不出去 → 不许销毁（机器继续停机挂着），并把「补 owner」列为决定 1 的前置任务。这条不留给实现时发现。

**前置 B · 「继续付钱」必须有上界。** 「推不回去就继续付钱」方向对，但「推不回去」不一定是暂时的：平台上现有卡 `24f5a2bf` 卡在**自动重推 `non-fast-forward` 无限重试**上——提示「下一轮还会重试」，而同样的推送重试多少次都不会成功。照原样写，一台机器会因为一个推送 bug 无限期计费，且没有任何人会知道。

所以：**重试 N 次 / 超过 T 小时仍推不回 → 升级成一条强提醒给人**，而不是安静地继续付钱。「无限期付费」和「无声销毁」是同一个病的两面，两边都要有出口。

> 这就是我不同意 issue 定性的地方：这个决定的第一目标是**不误杀**，省钱是第二目标，而且第二目标用「停机」就能拿到大部分收益。

### 决定 2 · 供给形式落到数据模型

**加在 `device` 表上，不是 `ProjectMachine` 上。**

```
ALTER TABLE device ADD COLUMN supply     VARCHAR(16) NOT NULL DEFAULT 'self_hosted';
ALTER TABLE device ADD COLUMN visibility VARCHAR(16) NOT NULL DEFAULT 'host';
-- 一次性回填
UPDATE device SET supply='cloud'
 WHERE device_id IN (SELECT device_id FROM project_machines WHERE device_id IS NOT NULL);
```

为什么是 `device` 而不是 `ProjectMachine`：

- **消费侧读的是 device。** `resolve_pinned_device`、`DeviceProvider`、ComputePool 都拿 device_id 做判断；语义长在 `ProjectMachine` 上，每次判断都要 join 回去——**那还是反查，只是换了个写法**。
- `ProjectMachine` 是 **cloud 供给的实现细节**（MicroCloud 那侧的记账：`machine_id` / `customer_id` / `offering_id`）。self-hosted 设备根本没有这一行。用「另一张表有没有这条记录」表达语义，正是这次要消除的东西。
- `ProjectMachine.device_id` 保留，但**只作为链接**（这台 MicroCloud 机器 enroll 成了哪个 device），不再承担语义。

**写入点只有两个，各写死一个常量，任何地方都不许推断：**

| enroll 入口 | supply |
|---|---|
| connector 设备流（人自己装 cheesehost） | `self_hosted` |
| machine 的 enroll sweep（平台 SSH 进去装） | `cloud` |

**「入口决定待遇，不是硬件决定待遇」落到代码就是这两行。** 判据是：这两处必须是常量赋值；只要出现 `is_platform_provisioned()` 这类**推断**函数，语义就又靠反查存在了。

**这条判据落成机器执行的守卫，不靠人记性**：加进 <&.claude/scripts/check-repo-rules.sh>（它现有 4 条规则，每条都带 `--self-test`，正是干「linter 表达不了的仓库规矩」的地方）。规则内容：`app/domain/device/` 与 `app/domain/machine/` 下不得出现从 `project_machines` 反查供给形式的模式，也不得定义 `is_platform_provisioned` / `is_cloud_machine` 这类推断函数；要判断就读 `device.supply`。按该脚本的惯例同时补 `--self-test` 分支，证明规则会 fire 且作用域正确。

**配一条不变量**：`supply='self_hosted'` 的 device，走到任何 stop/destroy 路径要**直接抛错**，不是 `if` 静默跳过。以后有人加第二条回收路径忘了判断时，抛错会当场炸，跳过则会安静地把别人的机器关掉。

### 决定 3 · 共享机器的资源配额（+ 准入）

配额挂在 **`visibility=host`** 这根轴上，不是挂在 supply 上（见 2.2）。

**A. 资源面**

- **磁盘优先，因为它不可抢占。** CPU / 内存挤一挤只是慢，盘满了是同机器所有房间一起挂——就是 #186 第一节 `storage_exhausted` 放大 N 倍。每房间给工作树一个配额，软线告警、硬线把该房间的写路径关掉并判这一轮失败。首选文件系统级配额（XFS project quota / loop-mount），机器不支持就退化成周期性 `du` + 两条线。**不建议为此引入 cgroup v2 之外的新组件。**
- **CPU / 内存 / 进程数**：host 档下 `claude` 直接跑在宿主上，用 systemd transient scope 把每个房间的屏幕关进一个 cgroup（`systemd-run --scope -p MemoryMax=… -p CPUQuota=… -p TasksMax=…`）。零依赖，且**不改变可见性**——正好符合「host 档要的是可见性，不是无限资源」。
- 数值上先对齐容器档（2g / 2 cpu / 512 pids）作为**每房间**基线，盘按「机器盘容量 ÷ 房间数上限」给，下界 10G。

**B. 权限面（issue 没写，我认为必须有）**

- host 档**不是随手能点的默认项**：选它要一次明确确认。
- **跨团队禁止混用 host 档**：一台 device 已被 A 团队的房间以 host 档占用时，B 团队不能在同一台机器上再选 host 档（`isolated` 可以）。理由见 2.3——「都是自己人」只在团队内成立，而 `DeviceTeamRow` 允许跨团队绑定。

---

## 四、界面上那句实话

按第一节查到的事实（`--dangerously-skip-permissions`，属主身份，裸在宿主上）写，不加软化词：

> **这一档不在容器里跑。芝士会以这台机器属主的身份，直接读写整台机器——包括同机器上其他房间的工作树，以及进入它们的容器。选它意味着你信任这台机器上的所有协作者，也意味着他们的活可能被你的房间影响。**

配一次明确确认，不做默认项。**这条不在实现里弱化**：如果实现时发现这句话太吓人所以想改软，那要改的是权限模型，不是这句话。

---

## 五、边界与相邻

- **#186**：本 issue 给它第五节补上前置——「判废之后能不能换」取决于房间的 supply/visibility。好消息是 **#186 的方案已经把判定做成 quarantine（隔离 + 冷却）而不是 destroy，天然兼容**：对 `self_hosted` 或 `visibility=host` 的房间，**隔离照做**（不再往这台机器派新房间），**但已绑定的房间不迁移，只报人**。理由用 issue 的：芝士被搬到看不见那个服务的机器上、然后不知道自己为什么找不到东西，比不换更难诊断。落到代码就是给迁移动作加一个判据，判据正是决定 2 加的那两个字段。
- **#188**：平台不代管别人的运维 → `self_hosted` 的健康问题只报人，不代修、不代清。
- **`credits设计`**：billing 轴的**单价**本轮不定，但 2.1 之后它不再是「以后再说」——**回收时限的量级由计费粒度决定，所以每一档必须先说清它是按秒/按小时/包月**，否则决定 1 的 N/M 填不出来。已在那个话题里 @ 过一条：它整篇是 LLM token → credit 的折算，**没有机器小时这一类消耗**；`(cloud, host)` 一旦落地，一台常驻 VM 的钱不走 credit 体系就没人管。
- **隔离技术选型（Docker vs Firecracker）**：同意 issue 判成可推迟，本轮不扩。

---

## 六、状态与下一步

**已对齐（2026-08-12）**：四轴模型 + 「档 = 具名组合」；2.1 改为三条关系（supply 定动作合法域 / billing 定时限量级 / 档位配数字）；2.2、2.3 无补充；第四节那句实话按事实写、实现时不许改软。

**决定 2 已实现（待验收）**：

| 改动 | 位置 |
|---|---|
| `Supply` / `Visibility` 两个枚举 | <&backend/app/domain/device/supply.py>（`Device` 的两个字段在 <&backend/app/domain/device/repository.py>） |
| `device.supply` / `device.visibility` 两列（保守 server_default）+ 一次性回填 | <&backend/app/domain/device/models.py>、`alembic/versions/c4a71e5d9b30_device_supply_and_visibility.py` |
| `approve(supply=...)` **无默认值**，两个入口各写死一个常量 | <&backend/app/domain/device/service.py>、<&backend/app/api/routes/connector.py>（`self_hosted`）、<&backend/app/domain/machine/services.py>（`cloud`） |
| 不变量：`delete_platform_provisioned` 对 self-hosted **抛错**；`delete_owned`（人自己删）不受影响 | <&backend/app/domain/device/service.py> |
| 守卫 + `--self-test` | <&.claude/scripts/check-repo-rules.sh> 规则 5 |
| 还掉一条存量债：`device_provider` → `machine.repositories` 的跨域 repository import 随反查一起消失，白名单对应行删除 | <&backend/tests/unit/test_domain_import_guard.py> |

**验证**：ruff / ruff format / pyright 全绿（pyright 剩的 2 个 `_as_utc` 报错在 <&backend/app/domain/topic/services.py>，主线既有、不在本改动的 diff 里）；`check-repo-rules.sh` 与其 `--self-test`（现 5 条规则）全绿；`tests/unit` 全量 2851 passed，受影响的 8 个设备/机器测试文件 87 passed；迁移链在一个空库上从头跑到 head 通过，**回填另用带数据的库单独验过**（平台开的 device → `cloud`，人 enroll 的 → `self_hosted`）。

**变基后的两处返工**（2026-08-12 第二轮，基线换成含 #289/#299/#301 的 main）：

1. **枚举搬家**。#299 立了 `tests/unit/test_domain_import_guard.py`：领域包不许直接 import 别的领域的 repository 模块。`Supply` 原本住在 `device/repository.py`，而 `machine`、`agent` 两个领域都要读它——照原样合并就是**新欠两条债**。改为把两个枚举放进 `device/supply.py`（值类型本来就不该住在数据访问层），`repository.py` 从那里 import。`device_provider` 的那次读取也改走 #299 新增的 `device/wiring.py` 接缝，不再自己 `SqlDeviceRepository(session)`。
2. **两个新测试文件的调用点**。#301 带来的 `test_device_health.py` / `test_host_swap.py` 各有一处 `approve(code, owner_user_id=...)`——`supply` **故意无默认值**，所以它们不是「碰巧红了」，正是这个设计要求的：新入口必须自己表态。已各补一个 `self_hosted` 常量（这两个文件的逻辑不读该字段，是表态不是断言）。

> 顺带一条给 CLAUDE.md 的更正：那份文档把 `test_machine_service.py` 的失败归给 no-procps，实测**根因是缺 `ssh-keygen`（openssh-client）**，报错也不是 `'kill'` 而是 `'ssh-keygen'`。没有改 CLAUDE.md——那是共享文件，等你点头。

**一条本地拦住的事，不是本改动引起的**：这个沙箱同步到的 `main@upstream`（`08c58dba` = 采纳 issue 186 #301）**自身就是分叉的**——`alembic heads` 在**完全移除本改动的迁移文件**之后仍然是两个头（`b7e3c19d4f80` 来自 #301，`c1f7a3b90d24` 来自 #289），DB 类测试因此在建库那一步就 `Multiple head revisions are present` 报错。也就是说 #307 的修复还没同步进来。后果有两条：(a) 本轮**跑不了任何 DB 类测试**（integration / contract / 一部分 unit），只有纯 unit 可跑；(b) `c4a71e5d9b30` 的 `down_revision` 该挂谁，**在这个盒子里看不出来**——现按指示挂在 `c1f7a3b90d24` 上，但若 #307 是一条合并迁移，真正的链尾是那条合并的 revision，需要它的 id 才能挂对。

> 顺带一条给 CLAUDE.md 的更正：那份文档把 `test_machine_service.py` 的失败归给 no-procps，实测**根因是缺 `ssh-keygen`（openssh-client）**，报错也不是 `'kill'` 而是 `'ssh-keygen'`。没有改 CLAUDE.md——那是共享文件，等你点头。

**实现时发现的一件事，比预想的更实**：反查不是「以后可能有人写」，是**已经在跑的生产代码**——`ProjectMachineRepository.is_provisioned_device()` 用「machine 表里有没有一行指向这个 device」判断 device 是否与后端共享文件系统（co-location），而共享判错是**静默失败**：launcher 会 `mkdir -p` 任何给它的路径，于是芝士在一个空目录里开轮次。已改为读 `device.supply`，该反查方法删除（它只有这一个调用者），顺带断掉了 agent 层对 machine 层的一处跨域 import。

**接下来**：
1. 决定 1 阻塞在两条前置上：**补 owner**（前置 A）与**推送重试上界**（前置 B）；N/M 还阻塞在 billing 轴的计费粒度上。
2. **工作树回收**（归档话题的可重建目录）可以独立于以上全部先做——它不依赖 supply、不依赖 billing、不需要任何新字段，只需要「归档 + 只删可重建物」这一条判据，是本 issue 里唯一现在就能落地且能立刻还出 150G+ 的一格。
3. 决定 3 的落地切分（配额两层），以及和 #186 那张卡的先后顺序。
4. `visibility` 目前只有 `host` 一个真实取值——`isolated` 要等「每房间一个容器的 device 传输」才有意义，本轮只落列不开值。
