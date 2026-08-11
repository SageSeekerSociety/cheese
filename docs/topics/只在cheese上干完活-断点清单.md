**理想链路**：话题里提需求 → 芝士开子话题干 → 递验收卡 → 人点采纳 → 改动自动进 GitHub main → 部署到盒子 → 话题归档。
本话题是这条链路的**断点台账**，也是工作台：一条要办就 `split` 一个子话题，办完递卡回流。

## 总览

| # | 断点 | 状态 | 归谁 |
|---|---|---|---|
| 1 | GitHub App 缺 `workflows` 权限 | **卡在人身上** | 需 App owner 网页操作 |
| 2 | 平台 main 与 GitHub main 同步要手动按 | ✅ 已做 | #275 改成定时拉 |
| 3 | `pending_gate` 没有出边 | 在做 | 「pending_gate孤儿卡死锁」 |
| 4 | 闸门绿灯不可信（全 SKIP 判绿 / 守卫一响无结论） | 在做 | 「门禁SKIP不该判绿」 |
| 5 | 采纳推不到 GitHub 时只留一条卡备注 | 未接 | — |
| 6 | 「我的改动到底上盒子了没」在平台上看不见 | 未接 | — |
| 7 | token 过期，发言人静默变 `anonymous` | 未接 | — |
| 8 | 采纳会不会真走 PR，事前不可见 | 未接 | 根同 1/2 |
| 9a | 镜像推不上去 → 后面全堵死（ghcr 403） | ✅ 已修 | #276 |
| 9b | 慢工作追不上快 main | 先想清楚，别急着做 | — |
| 10 | 平台重启 → 自报 `scope` check 永久 IN_PROGRESS | 未接 | — |
| 11 | 部署销账问错了问题 → 卡永久 `pr_open` | 在做，卡 held | `83d67c9c` |

（编号沿用话题里的原编号。9 被用过两次，这里拆成 9a/9b。）

## 卡在人身上

### 1. GitHub App 缺 `workflows` 权限

`cheesex-app` 实测权限：`actions=read, checks=read, contents=write, metadata=read, pull_requests=write` —— **没有 `workflows`**。

GitHub 的规则不是「这张卡改了 workflow 才拒」，而是「推送分支里的 `.github/workflows/` 只要跟目标默认分支不一致就拒」。所以**平台 main 一落后就撞墙**，跟卡改没改 workflow 无关。

下午实测把影响面收窄了：平台 main 追平 GitHub main 之后，不碰 workflow 的卡走 PR 完全正常（#270 #271 #273 #274 #275 #279 全是平台自动合的）。**现在真正还撞墙的只剩「卡本身改了 `.github/workflows/`」**——活样本 `b2bcbe11`（操作卡·registry，改了 5 个 workflow），至今出不去。

修法一步，且只能人做（GitHub 没开放改 App 权限的 API）：
`https://github.com/settings/apps/cheesex-app/permissions` 加 `Workflows: Read and write`，然后 org 接受一次权限请求。

## 在做（别重复开工）

### 3. `pending_gate` 是个没有出边的状态

卡 `b020d5d3`（话题 `fbea1a5e`）锁死：`gate_passed_at` 为 null、`gate_output` 为空，闸门从没跑完，也不会再有东西来写它。三个终结操作全试过：

| 操作 | 结果 |
|---|---|
| `accept` | 「平台检查还在进行中」（`review/services.py:454`） |
| `reject` | 只接受 `pending`（`:1609`） |
| `revoke` | 「只有已验收的卡才能撤销」（`:1627`） |

**任何非终态都必须有一条人能走的出边**——至少 `reject` 该放宽到所有非终态。
→ 「pending_gate孤儿卡死锁」在做：扫底判死 + `gate_started_at` + 人工作废出口。

### 4. 闸门那张绿灯不可信

两头都坏：

- **全跳过也算通过**：`Result: 0/0 passed, 4 skipped (exit: 0)` 照样放行进 pending。根因是 `check.sh` 只在 `FAIL>0` 时退非零，盒子 DNS 一挂，pyright/alembic/pytest 全降级成 SKIP。
- **守卫一响连结论都没有**：三个仓库守卫在 `set -euo pipefail` 下，`else` 分支那句重跑是管道，返回非零直接打死脚本，`FAIL:` 和 `Result:` 两行都印不出来——而闸门就靠那行判读。（#264/#266 引入的 bug。）

→ 「门禁SKIP不该判绿」在做。

### 11. 部署销账问错了问题（最后一环）

采纳→PR→CI→自动合并→main 已反复跑通，**卡住的是话题归档**。

根因：平台按 `head_sha` 精确匹配去找部署 run。而 `deploy-dev.yml` 的并发组是固定字符串 `deploy-dev`（不含 sha），合并一密集，后来的 `workflow_run` 触发被并发组吞掉——**run 根本不会被创建**。

对照实验（都在 main、都是 push、build 都 success，唯一差别是时间疏密）：

| sha | Build | Deploy run |
|---|---|---|
| `482ca022e` (#270) | success 14:58 | **不存在** |
| `611e43f02` (#274) | success 14:59 | **不存在** |
| `955707a31` (#273) | success 15:05 | ✅ 15:09 success |

这三个提交其实**早就上线了**——部署拉的是 main 的 tip，它们全是 `45b6169a4` 的祖先（`merge-base --is-ancestor` 逐个验过）。代码到了 dev，只是账没销。

**性质**：不是偶发故障，是那套并发配置的固有行为。**平台自动合并跑得越顺、合并越密集，卡永久卡死的概率越高——越顺越坏。**

修法：判据从「我这个 sha 的部署 run 在哪」换成「**我这个 commit 到底上线了没**」。前者取决于 GitHub 的调度偶然，后者是事实。

两个已经钉住、不要再走回头路的判据教训：

- **「镜像在不在」是坏代理**，不能拿来再挡一道。`#267/#270/#274` 自己的镜像确实不存在（ghcr 403），但一个包含它们、真的部署过的更晚提交已经把源码送上盒子了。按「镜像在不在」它们**永远卡死**。
- **`success ≠ 真的部署过`**（更危险，是假阳性）。`Skip docs-only commits` 会把登录和部署两步关掉，job 照样绿；这种运行顶替掉一次 cancelled，就会把「什么都没做」判成「已上线」。

**当前状态：卡 held，入口没接上。** 实现质量没问题，但新逻辑全挂在 `if state == "failure":` 下，而 `workflow_run_state` 的契约是「没有匹配的 run → **pending**，不是 failure」。0 runs 的那两张卡永远走 `pending → return`，**新代码一行都执行不到**。

那三张 `pr_open` 的卡**不手工归档**——它们是这个修复天然的端到端验收样本。

## 未接

### 5. 采纳没能推到 GitHub 时，只留一条 note

卡备注里写着「⚠️ 未走 PR 采纳（GitHub 侧调用失败：…）；上游同步失败，未回推」，话题该归档归档，看起来一切正常，但**东西根本没出平台**。人要点进卡读 note 才知道。
同步上游冲突已经会自动派活了，回推失败该有同等待遇——要么派活，要么在话题里给一条明显提醒，而不是藏在备注字段里。

### 6. 「我的改动到底上盒子了没」在平台上看不见

deploy 是 `workflow_run` 触发的，`gh run list` 对这类 run 显示的是**当前 main 的 sha**，不是被部署的那个 sha——盯着 sha 都会判断错。用户在平台上更是完全看不到。
要的是：话题/卡上直接看到「镜像建完没、部署到盒子没、部署的是哪个 sha」。

**这一条的本质**（9a 那次事故给出的最好注脚）：平台全程没有撒谎，每一环的信号都准确——卡备注写着「等部署也成功才归档」，deploy 守卫写着「什么都没部署」，build 日志写着 403。问题在于**没有任何地方把这些信号连起来**，于是三张卡在 `pr_open` 上躺了几小时，看起来跟「CI 还在跑」一模一样。

### 7. token 一过期，人的发言会静默变成「匿名者」

`api/routes/chat.py` 连接时解析一次 token，`conn_actor.authenticated` 为假时**不拒绝、不提示**，直接回落到消息体的 `author`，没带就写 `anonymous`：

```python
author = (
    conn_actor.handle
    if conn_actor.authenticated
    else (payload.get("author") or "anonymous").strip()[:64] or "anonymous"
)
```

消息照发、芝士照做，只有署名悄悄丢了。发的人在自己屏幕上看不出任何异常。
同一个降级路径还让「**未认证的连接可以声称任何 author**」成立（`#190` 已知的 Phase-0 缺口，代码注释里承认了）——所以这不只是显示问题。

要的效果：token 失效时，要么拒绝连接并提示「登录已过期，请重新登录」，要么至少在消息上标出「未认证」。悄悄改掉发言人身份是最坏的一种。

### 8. 采纳能不能走 PR，事前不可见

同一个操作，14:00 前三张卡全部降级（只合平台 main、不推 GitHub），14:15 平台 main 追平之后 14:35 起连续正常开 PR。决定结果的是一个**人完全看不见的状态**：平台 main 落后 GitHub main 多少。人拿到的反馈只有卡备注里一句「GitHub 侧调用失败」。
补齐 App 权限（第 1 条）后耦合会松很多，但「**我这次采纳到底会不会推出去**」本身应该在点采纳之前就能看见。

### 10. 平台一重启，它自己上报的 `scope` 检查就永久挂起

```
scope      | IN_PROGRESS |      | 2026-08-11T14:28:59Z
guard/guards/e2e | COMPLETED | SUCCESS
```

`scope` 不是 GitHub Actions 的 workflow，是**平台自己创建并上报的 check run**（判断改动有没有超出授权范围）。检查跑在平台进程里，进程一没，check run 就永远停在 `IN_PROGRESS`——GitHub 那边不会超时，也没人来补结论。

死锁链：PR 永远 UNSTABLE → 自动合并条件永远不满足 → 卡永远 `pr_open` → 话题永远不归档。人看到的是「采纳成功了」，且**没有任何地方显示它为什么停着**。

**这条特别阴，因为它由成功本身触发**：采纳越顺、合并进 main 越频繁，部署越频繁，正在跑的 scope 检查被打断的概率就越高。今天四张卡连续采纳，第二张就撞上了。

要办：

1. **check run 必须能自愈**——平台起来后要知道自己有哪些 check run 还开着（**持久化，别只放内存**），要么继续判，要么给一个明确的失败结论让人能重试。悬在 `IN_PROGRESS` 是最坏的一种。
2. **判 scope 本身要能扛重启**——短判断就别跨越一次部署；可能长就要断点续传或超时兜底。
3. **平台侧要显示 PR 卡在哪一项检查上**，以及它已经多久没动静。

（#270 是人手动合并绕过去的——而这正是本清单要消灭的动作。）

### 9b. 慢工作追不上快 main

`b2bcbe11` 两次解完冲突去采纳，两次都在采纳的瞬间发现 main 又前进了、又冲突了。解冲突要几分钟到几十分钟，而 main 现在几分钟就长一截（今天下午合了 10 个 PR）。这不是谁做错了，是**解冲突耗时 × main 前进速度**决定的结构性问题，并行话题越多越糟。

方向（**别急着做，先想清楚**）：采纳时不要求分支追平 main（让平台做三方合并而非要求先 rebase），或者解冲突任务在采纳前自动重新对齐一次基线。

## 已做

### 2. 同步上游要人手动按 → #275 改成定时拉

平台 main 一落后，workflow 文件就跟 GitHub 默认分支不一致，就撞第 1 条的权限墙——所以这条是 1 的放大器，优先级反而更高。冲突自动派给芝士解（#268）这部分本来就是好的，缺的只是「触发同步」本身。#275 补上了。

### 9a. 镜像推不上去，把后面所有环节全堵死

完整因果链：

```
docker login -u ${{ github.actor }}
  → push 事件里 github.actor 是「提交作者」，而平台代人采纳生成的提交，作者是被采纳的那个人
  → ghcr 返回 403 permission_denied，build 不产出镜像
  → deploy 守卫如实报「no images were pushed, dev is still running the previous commit」
  → pr_open 的卡等不到绿部署（2026-08-09 拍板：merge alone doesn't count）
  → 话题永不归档
```

对照实验坐实：同一个 sha `2e72632e3`，走 `push`（actor=andylizf）→ 403 失败；走 `workflow_dispatch`（actor=WangChangxin0809）→ 成功。树、Dockerfile、`GITHUB_TOKEN` 权限全一样，唯一变量是 actor。

已改成 `github.repository_owner`（#276，六处：`build.yml` ×4、`build-tmux.yml`、`deploy-dev.yml`）。

顺带表扬 `deploy-dev.yml` 的 guard job：它没假装成功，而是明确报了「Build failure for 2e72632e3 — no images were pushed, so dev is still running the previous commit. Re-run the build; a PyPI or registry reset is the usual cause.」——把「什么都没部署」「盒子还在跑上一个提交」「怎么办」一次说清楚。缺的只是**这句话只出现在 Actions 日志里**（→ 第 6 条）。

## 本工作区能核验到的（2026-08-11）

平台无法观测远端（`jj git fetch` 不可用、token 读不了 PR/ref），所以凡涉及远端状态的都是**声明，不是观测**。以下是本工作区里真的跑过命令看到的：

- 本工作区 `main@upstream` = `2e72632e`（即 9a 那次 403 失败的 sha）。`build.yml`/`build-tmux.yml`/`deploy-dev.yml` 六处 `docker login` 仍是 `${{ github.actor }}`——**#276 还没到达本工作区看到的平台 main**。（只能说明本工作区的基线旧，不能断言全站平台 main 就停在这里。）
- `main@upstream` 的 `deploy-dev.yml` 并发组仍是固定 `group: deploy-dev` + `cancel-in-progress: false`——第 11 条的根因在这份基线里原样成立。
