## 目标

@fulu 发的截图没送到机器上。两件事：① 把图从后端捞出来看内容（已完成）；② 修「图片送不到沙箱」这个 bug——之前修过一次但不奏效。

## 图里是什么（已捞到）

那张图是一段终端报错：

```
API Error: Unable to connect to API (ConnectionRefused)
Crunched for 3m 3s
Unable to connect to API (ConnectionRefused) · Retrying in 1s · attempt 3/10
```

捞法：`GET /topics/<话题id>/attachments/raw?path=uploads/img-3231d1ddd207.png`，200，12725 字节的 PNG。**图在后端好好的**——丢的只是「后端 → 这台机器」这一段。

## 根因（已定死，不是猜的）

一句话：**这台开发机上的连接器（cheesehost）是 8 月 16 日的旧版本，它根本不认识后端用来传图的那条指令。**

链条：

1. 图片上传落在后端自己的工作区。机器是另一台机器上的另一块盘，所以后端要额外把字节推过去，用的是 `file.put` 这条控制帧（<&backend/app/domain/agent/device_provider.py> 的 `stage_images`）。
2. `file.put` 是 PR #508 加进连接器的，合并时间 **2026-08-17 15:57**。
3. 这台机器上跑的 `cheesehost` 进程（pid 185284）启动于 **2026-08-16 20:01**，二进制文件本身也是 8-16 20:00 编译的——**比 `file.put` 早了将近一天**。实测 `strings` 里 `file.put` / `file.result` 出现 **0 次**。
4. 旧连接器的消息分发 `switch` 没有 `default` 分支，认不出的帧**一声不吭地丢掉**，不回错误。
5. 后端等 20 秒（`_FILE_STAGE_TIMEOUT_S`）超时，拿到一个**消息为空**的 `TimeoutError`，于是日志里是 `could not stage image ...: ` 冒号后面什么都没有，房间里则显示「本轮有 N 张图片没能送到这台机器上」。

所以**这台机器上任何话题、任何时候发的图，从功能上线那天起就一张也没到过**。日志里同一时段另一个话题（e3454be0）也在丢图，报错一模一样。

**为什么「修过一次不奏效」**：上次修的是 PR #613，它把 staging 从 send 里拆出来，作用是「图送不到时至少别把整条消息也弄丢」。那是止血，不是修复——它从来没让图真的送达。

**为什么没人发现**：服务端在 `hello` 握手里只收一个协议版本号 `v=1`，而 `file.put` 加进去时按约定没有升版本（只有破坏性改动才升）。于是「版本号相同、能力不同」对服务端完全不可见，机器可以无限期地旧下去。而 `device_link.update()`（服务端主动推「去更新」的那条帧）在整个后端里**没有任何调用点**，是死代码——所以平台从不推更新，机器只能靠人手动更。

## 修法

**A. 立刻止血（这台机器）——已完成，2026-08-31 07:05**。@fulu 拍板后执行。

动手前逐项核对：连接器 config 的 `base` 非空；`curl <base>/connector/latest/linux-amd64/cheesehost` 返回 200，sha256 = `d2128fb9…`，与 `docker exec cheese-backend-1 sha256sum /app/connector-dist/linux-amd64/cheesehost` **完全一致**，且含 `file.put`；`~/.local/bin` 对服务用户可写（否则自更新永远失败且无声，#501）。

`kill -USR2 185284` → 7 秒内换完。结果：

| 检查项 | 结果 |
|---|---|
| 磁盘上的二进制 | `d175d79f…`（8-16）→ `d2128fb9…`（8-29） |
| 运行中进程映射的 inode | 1472199 = 磁盘上新文件的 inode，**跑的确实是新镜像** |
| pid | 185284 不变（`syscall.Exec` 保留 pid） |
| tmux 会话 | 251 → **251，一个没少** |
| journal | `binary updated in place; handing off to the new build (tasks preserved)…` |
| 控制通道 | 14:04:59 `WebSocket /connector/agent` accepted，已重连 |


**B. 让它不再复发（代码，已完成）**：commit `9ee42b992`，分支 `topic/787d58b3`。

让服务端能看见机器上跑的到底是哪个连接器，看见不对就推更新：

- `hello` 带上连接器**自己二进制的 sha256** 和 `<os>-<arch>`。不用版本号字符串——版本号要靠人记得改，而「装更新」本身就是把服务端那份字节 rename 到可执行文件上，所以比哈希是精确的、且不会忘记升。
- 服务端拿它跟自己发布的那份比，不一致就推 `{"t":"update"}`，把那条一直没有调用点的死代码接上。
- **一个字节都不报的连接器 = 比「开始上报」那个版本还老**，照样推。这条是够到已经落在外面的机器的关键：`update` 帧从 2026-07-08 就在，每个发过的版本都认识它。
- 两个防呆：每条连接只推一次（更新失败就等它下次重连，不刷屏）；只在拿到确定答案时才推——报了平台却哈希不出自己的，不动它，否则会变成每次重连都 re-exec、永远收敛不了。
- 顺带把那条以冒号结尾的日志改成会打印异常类型（`TimeoutError`）。

改动文件：新增 <&backend/app/domain/agent/connector_build.py>；改 <&backend/app/domain/agent/device_hub.py>、<&backend/app/domain/agent/device_link.py>、<&backend/app/domain/agent/device_provider.py>、<&backend/app/api/routes/installer.py>（目标列表和 dist 目录改为共用一处）、<&cli/internal/link/link.go>、<&cli/internal/host/host.go>、<&cli/internal/update/update.go>。

## 验证

- 新增 13 条 Python 测试 + 1 条 Go 测试。其中 **3 条在把修复关掉后实测是红的**（`test_connector_that_names_no_build_is_told_to_update`、`test_connector_running_other_bytes_is_told_to_update`、`test_update_is_pushed_once_per_connection_and_again_on_reconnect`）。
- Go：`go build` / `go vet` / `gofmt -l` / `go test ./...` 全过（在 docker 的 golang:1.26 里跑，这台机器没装 Go；`internal/service` 有一条测试在容器里以 root 跑会假红，换成非 root 就绿，与本改动无关）。
- Python：ruff check、ruff format、pyright 全 0 error。
- 后端全量测试：**5314 passed / 2 failed / 30 skipped**（15 分 34 秒）。两条红的都不是本改动造成的：
  - `test_exec_in_sandbox_runs_code_and_blocks_network` —— 单独重跑就绿，是要真 docker 沙箱的那类不稳定测试。
  - `test_market_lists_ai_and_compute_pools` —— 断言的是 AI 池的 `available`，跟本改动碰的文件毫无关系。**已在 base 提交 `d8d3eeb18` 上单独复现，同样红**，是这套一次性测试环境没配 AI provider 导致的既有失败。

## 进展

- [x] 图已捞出并读出内容
- [x] 根因定位到「连接器旧了 15 天 + 平台从不推更新」
- [x] B：代码修复 + 回归测试，已推到 `topic/787d58b3`
- [x] 后端全量测试跑完（5314 passed，2 条既有失败与本改动无关）
- [x] A：连接器已更新到 8-29 构建并重连，251 个 tmux 会话零损失

## 待办

- **只差最后一步**：请 @fulu 再发一张图，那是唯一的端到端验证——二进制里有 `file.put` 这个字符串只证明它认识这条帧，真的把字节写进沙箱还得跑一次才算数。
- 分支 `topic/787d58b3` 要不要开 PR 由 @fulu 定：本话题是 device 话题，递验收卡的瞬间平台会拿它自己那份工作区做快照强推，会把已推的内容冲掉，所以我没有自行递卡。
