## 目标

回答 @wangchangxin 的问题：平台上为什么会出现 request timeout。根因是计量代理的崩溃 bug，已拆支线去修。

## 结论（一句话）

计量代理在**拒绝一个带请求体的模型调用**时会把自己崩掉：拒绝的 503 永远送不到客户端，Claude 那头只能干等到超时——用户看到 request timeout，真正的错误原因被吞掉。

## 证据链（2026-09-05 现场核实，均可复跑）

1. **崩溃现场**：`docker logs cheese-metering-proxy` 近 48h 有 **350 条 `mitmproxy has crashed!`**，崩溃前一行总是 `refusing a machine turn: no identity to send it as (... path=/v1/messages ...)`，异常为 mitmproxy 12.1.2 的 `NotImplementedError: Can't set a response and enable streaming at the same time`。
2. **触发机制**：<&deploy/metering-proxy/billing_addon.py> 的 `requestheaders` 在 host 校验通过后即设 `flow.request.stream = True`（#654 防 OOM），之后的拒绝路径（`_refuse` 设 `flow.response`）与之冲突。**只在请求带 body 时炸**：GET 类拒绝正常送 503；`POST /v1/messages` 48h 内 **68 次拒绝 0 次送达**（grep `-A3 'refusing.*v1/messages'` 后数 `<< 503` 为 0、数崩溃为 68）。代码注释"A path below may still refuse via flow.response, which short-circuits regardless of this flag"是错的。已核实**线上部署与仓库源码逐字节一致**（diff 无差异），改仓库即可。
3. **用户侧表现**：连接崩掉/挂死 → Claude 报 request timeout → 重试 → 再崩。日志里同一连接整 10 分钟后再崩一次是重试脚印。

## 修法（已拆支线执行）

1. **代理崩溃**：`flow.request.stream = True` 挪到所有拒绝判断之后（确定转发才开流式）；正常转发路径必须保持流式（不能退回整块缓冲，那是 OOM 修复 #654）。

## 进展

- 2026-09-05：根因定位完成，修法已写进支线简报，支线已派出。部署（拷贝到宿主 `/home/nictheboy/cheese-proxy-new/` + 重启容器、后端上线）由本房间在合并后跟进。

## 待办

- 支线：按简报修 + 测试，push 后 conclude（不递卡，由房间递给 @wangchangxin）。
- 房间：验收、递卡、合并后跟进部署，并回头确认 agent 项目的循环止住。
