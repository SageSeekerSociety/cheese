## 状态：三处修复 + 功能测试已完成，已递验收卡（pytest 因沙箱缺 Postgres 未能本地跑绿，需要有 DB 的环境/CI 复核）

## 根因回顾

链路：`cheese-hook` 转发 → `POST /sandbox/hooks/{topic_id}` → `HookRouter.push` →
turn 内的 `run_hooks_turn` 消费，或（找不到监听队列时）落 server 端 spool，等下一轮
`_reconcile_spool` 补写。三处缺陷都在这条链路上，且互相关联（同源）。

## 已完成的三处修复

1. **补写要广播，不能只落库**（`backend/app/domain/agent/chat.py`）
   `_reconcile_spool` 从纯 `async def -> None` 改成 `AsyncIterator[dict]`：为每条
   backfill 的消息/现场事件 `yield {"type": "assistant_block"/"event_block", ...}`，
   调用处（`_converse_impl`）用 `async for frame in self._reconcile_spool(...): yield frame`
   转发。`_persist_tool_event` 相应改为返回持久化后的 block payload。broadcast 走的是
   `ChatService.converse` 现有的 frame → `TurnRunner` → `Broker.publish` → WS 通道，
   和实时路径完全同一条链路，不是新开的旁路。

2. **迟到的 Stop 不能提前结束新 turn**（`backend/app/domain/agent/hook_events.py` +
   `backend/app/domain/agent/hooks_substrate.py`）
   根因不是"注册覆盖"本身（这已经被 per-topic 锁很好地序列化了），而是：
   `register()` 必须在 `_ensure_ready`（等 screen/CLI 就绪，可能要等上一轮那个被判超时
   但仍在跑的 `claude` 进程真正收尾）**之前**发生（不能漏收早到的 hook）——这就给了
   上一轮的尾巴事件（包括它自己迟到的 Stop）一个窗口，可以在新 prompt 还没发出去之前
   就落进新注册的队列。`send_prompt` 之前，队列里任何东西都不可能属于"即将开始"的这个
   turn。新增 `HookRouter.drain(topic_id)`，在 `_ensure_ready` 成功、`_send_prompt`
   之前把队列排空，排空到的内容按"这个 turn 拒绝信任"处理——落到同一个 spool（复用
   `/sandbox/hooks` 端点已有的 park 路径），不会被丢，也不会被误判成新 turn 的结果。

3. **兜底路径缺 eid 导致同段文字被判两次**（`backend/app/domain/agent/chat.py`）
   `assistant_count == 0` 的兜底分支容易撞上和修复 1/2 同源的场景：这段回复文本对应的
   `MessageDisplay` hook 因为跟 Stop 抢发生了竞态，恰好在这个 turn 运行期间才落进 spool
   （容器端是"先落盘再 curl"，本来就比 Stop 早发生）。修法：在兜底判断之前，**再 sweep
   一次 spool**（复用 `_reconcile_spool`，逻辑不重复写），把这次 sweep backfill 出来的
   文本记下来；兜底只在这次 sweep 没有覆盖到的情况下才补发（这样兜底真正无 hook 支撑的
   场景——例如非 hooks 的 SDK 后端——行为不变，仍然落库但不带 eid）。副作用是好的：这也
   把"迟到 hook 要等到下一轮才补写"的窗口，从"一整轮"收窄到"这一轮末尾"。

## 功能测试（backend/tests/，测行为不读实现）

- `backend/tests/integration/test_spool_reconcile.py`
  - `test_backfilled_events_are_broadcast_not_just_persisted`：断言 backfill 出的
    tool 事件 / 消息都能在 `converse()` 产出的 frame 流里找到对应的
    `event_block`/`assistant_block`，不是只进了 DB。
  - `test_fallback_reply_does_not_duplicate_a_late_spooled_message`：构造"回复文本
    恰好是迟到 MessageDisplay 内容"的场景，断言最终只有 1 条消息（不重复），且它带
    `eid` + `backfilled: true`（走的是 reconcile 路径，不是无 eid 兜底）。
- `backend/tests/unit/test_hooks_substrate.py`
  - `test_stale_stop_from_abandoned_turn_never_ends_the_new_turn`：用假
    `HooksTurnProvider` 子类模拟"`_ensure_ready` 等待期间上一轮的迟到 Stop 落进新
    队列"，断言新 turn 最终按自己真实的 Stop 结束（不是被旧 Stop 提前打断），且旧
    Stop 被 park 进 spool（没有静默丢失）。

## 验证现状（需要 @n1ctheboy 或知道本环境限制的人确认）

- `ruff check .` / `ruff format --check .`：全仓库通过。
- `pyright`（项目配置只覆盖 `app/`）：0 errors。
- `pytest`：**本沙箱没有 Postgres/Docker**（`docker`/`podman` 未安装，`127.0.0.1:5433`
  连不上，也没有权限装 Postgres server 包），`tests/conftest.py` 的 `_pg_schema` 是
  session 级 autouse，任何测试（哪怕纯单元测试）跑之前都会先建库，所以本地一条测试
  都跑不起来——包括 check-runner 分身也确认了同样的环境限制。三个新测试和三处修复都
  经过了逐行手工走查（追出了具体的执行路径和断言是否成立），但没有拿到一次真实的绿色
  pytest 运行结果。

确认：这是沙箱网络层面的真实限制（能看到 host.docker.internal:5433 开着端口，但那是
别的服务，认证不通，不是可用的测试库），不是代码问题，不再往这个方向排查。按约定处理：
验收卡里如实写清楚"测试代码已写好、ruff/pyright 过了，pytest 因沙箱缺 Postgres 没能
本地跑绿，需要有 DB 的环境/CI 复核"，正常递验收卡，不等连上库。

## 第一次递卡被质量闸门拦下（gate_failed）+ 已修复

闸门报 `fatal: not a git repository`：`.claude/scripts/check.sh` 用
`git rev-parse --show-toplevel` 定位仓库根目录，但这仓库的 VCS 是 jj，闸门执行环境里
没有 `.git`，check.sh 一开头（`set -euo pipefail` 下）就整体崩溃，ruff/pyright/pytest
都没机会跑——这是 check.sh 本身的 bug，不是这次业务改动引入的，其他话题的验收卡也会
撞上同一个问题。已改成从脚本自身路径推导 `REPO_ROOT`，不再依赖 git。

紧接着又追加了一处防御：闸门/worktree 环境可能继承别的用户建的 `.venv`（跨话题遗留），
uv 想重建 `.venv/share` 时没权限会直接炸——加了一段检测，写不了就把
`UV_PROJECT_ENVIRONMENT` 指到临时目录，让 uv 在那边建 venv。这两处都是和父话题/兄弟
话题（B/C）对齐后在本工作区同步应用的。

本地重跑 `bash .claude/scripts/check.sh` 确认：不再有 git 相关的 fatal 错误；
ruff PASS；pyright PASS（0 errors）；pytest 仍然 FAIL——原因还是本沙箱没有
Postgres（3158 个连接失败的 error，跟这次三处业务修复本身无关，是已经确认过的环境
限制，不是新问题）。

## 第二次递卡又被拦（gate_failed）：venv 防御本身引入了新的 build 失败 + 已修复

闸门第二次输出：ruff/pyright/pytest 三个全 FAIL，`hint: srp-rs was included because
cheesex-backend depends on srp-rs` + `Build failures usually indicate a problem with
the package or the build environment`。原因：父话题给的 `.venv/share` 防御是"整个换成
一个全新的临时 venv"（`UV_PROJECT_ENVIRONMENT=$(mktemp -d)/venv`）——这会让 uv 把**所有**
依赖从零装一遍，包括本地 workspace member `srp_rs`（一个 maturin/pyo3 的 Rust 扩展）。
闸门执行环境里没有 Rust 工具链（也没有一个能命中的 uv 构建缓存），从源码重建 `srp_rs`
直接失败——`.venv/share` 权限问题被绕过了，但换来一个新的、更隐蔽的失败。

改法：优先**原地删除**那个不可写的 `.venv/share`（我们对 `.venv` 这个父目录仍有写权限，
删子目录不要求拥有子目录里每个文件），让 uv 只重装 `share/` 这一小部分（通常只是 man
page 之类的数据文件），`lib/site-packages` 里已经装好的 `srp_rs` 扩展完全不受影响、不
需要重建。只有在原地删除也失败时，才退回到"整个换成临时 venv"这条更贵、也更容易在缺
Rust 工具链的环境里炸的路径。本地用一个模拟的只读 `.venv/share` 验证过这段新逻辑的两条
分支都按预期走（能删则原地删，删不掉才退到临时 venv）；真实 `bash check.sh` 重跑一遍，
ruff PASS、pyright PASS（这次是通过真实脚本走完 uv 环境准备这一步验证的，不是绕过）。

这个改法目前只在本工作区生效，值得同步回父话题/兄弟话题（B/C）：父话题那版"整个换临时
venv"的防御在没有 Rust 工具链的闸门环境里会引入新的 build 失败，建议大家都换成"原地删
share、删不掉才退化"这个更保守的版本。已用 `cheese notify` 告知 n1ctheboy。

## 第三次递卡再被拦：闸门上连 `.venv` 整体都不可写，rm -rf 也失败——已改用 UV_NO_SYNC

闸门这次连"原地删除 `.venv/share`"都失败了（日志：`couldn't remove it either — falling
back to a scratch venv`），说明闸门那边继承的不只是 `share/` 不可写，是**整个 `.venv`**
都不可写——于是又掉进"整体换临时 venv → 强制从源码重建 srp_rs → 没有 Rust 工具链 →
build failure"这条老路。

改法：这种"整个 `.venv` 都动不了"的情况下，不再尝试重建任何东西——设置
`UV_NO_SYNC=1`，让 `uv run` 完全跳过"把 venv 同步成 lockfile 状态"这一步，直接用继承
下来、已经装好全部依赖（含编译好的 `srp_rs`）的那个 `.venv` 只读运行。本地验证过：
`UV_NO_SYNC=1 uv run python -c "import srp_rs"` 能直接用现成的 `.venv` 成功导入，不
触发任何重建；也模拟过"整个 `.venv` 只读"这条分支，确认会正确落到设置
`UV_NO_SYNC=1`，而不是再去建临时 venv。真实 `bash check.sh` 重跑：ruff PASS，
pyright PASS（0 errors，输出很干净，这次没有版本提示信息混进去）。

三次踩坑的教训是一致的：**只要环境缺 Rust 工具链，就绝对不能让 uv 从头重建 venv**——
任何"换个新 venv 从零装"的防御思路都会在这条线上翻车，凡是涉及 `.venv` 不可写的兜底，
都应该优先"完全不碰、只读使用现成的"，而不是"换个能写的地方重装"。

## 第四次：连 UV_NO_SYNC 环境变量也不够——闸门宿主基础设施问题，已由发起人拍板降级检查

第三版用 `export UV_NO_SYNC=1`（环境变量）在闸门上仍然失败：继承的 `.venv` 还带着一个
指向不存在解释器的坏符号链接（`Ignoring existing virtual environment linked to
non-existent Python interpreter`），uv 判定这个 venv 无效后，不管 `UV_NO_SYNC` 环境变量
是否设置，都会先尝试删除/重建它——一样撞上 `.venv/share` 权限拒绝。

已跟发起人确认：这是闸门宿主本身的基础设施问题（git/jj 不匹配、venv 跨用户权限、
`srp_rs` 原生扩展编译，一共三层），不是任何子话题代码或子话题工作区能修好的。处理方式：
项目 `check_command` 直接改成 `bash .claude/scripts/check.sh --no-tests`。本工作区同步
了最终版 `check.sh`：
- 新增 `--no-tests`（或 `SKIP_TESTS=1`）：pytest 整段跳过，记 SKIP，不计入 PASS/FAIL，
  分母也相应减少（闸门宿主本来就没有可用 Postgres，跑不跑得过测不出真假）。
- ruff/pyright/pytest 全部改成 `uv run --no-sync`（CLI 参数，不是环境变量）——本地验证
  过这条路径连"venv 无效需要重建"这条检测都能绕开，不再有任何删除/重建 `.venv` 的尝试。

本地跑 `bash .claude/scripts/check.sh --no-tests`：`Result: 2/2 passed`，约 1 分钟内
跑完（ruff PASS、pyright PASS、pytest SKIP）。三处 turn 消息丢失/重复的业务修复、3 个
功能测试都没有变。

## 第五次：`uv run --no-sync` 在闸门上依然会删 `.venv/share`——改成直接调用 .venv/bin/*

递卡后闸门输出跟第三次几乎一样：`uv run --no-sync ruff/pyright` 还是报
`failed to remove directory .../.venv/share: Permission denied`。说明 `--no-sync`
（不管是 CLI 参数还是环境变量）只跳过"装依赖"这一步，跳不过 uv 更早的一步——校验/落地
这个 venv 对应的 Python 解释器是否可用；只要它判定这个继承来的 venv"无效"（大概率是
`pyvenv.cfg` 里记的 base 解释器路径是另一个用户的 home 目录，这个 host 上不存在），就会
不由分说地先删/建 venv 结构，`--no-sync` 完全管不到这一步。

check.sh 之前把 ruff/pyright/pytest 的真实输出统统 `tail -1/2/3`，这也是这几轮一直靠猜
的原因——真正的 `error:` 那行从来没被我看到过，只看到闸门贴出来的尾部提示。这次一并把
tail 放宽到 20 行，以后再翻车至少能看见真正的报错，不用来回猜。

真正的修复：既然依赖早就装好了（`srp_rs` 也编译好躺在 `lib/site-packages` 里），那就
完全不经过 `uv run`——直接调用 `.venv/bin/ruff`、`.venv/bin/pyright`、`.venv/bin/pytest`
这些已经装好的可执行文件，uv 完全不参与，没有环境可"校验"也没有东西可"重建"。没有
venv（比如第一次跑）时兜底回退到 `uv run --no-sync`。本地用只读 `.venv/share` 模拟过
（`chmod 555 .venv/share` 之后跑 `--no-tests`）：`Result: 2/2 passed`，确认这条路径完全
不会碰 `.venv/share`。
