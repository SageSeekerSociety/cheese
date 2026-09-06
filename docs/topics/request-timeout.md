## 目标

回答 @wangchangxin 的问题：平台上为什么会出现 request timeout。已定位两层根因，@wangchangxin 已拍板：代理崩溃 bug + 话题失身份，一起修。已拆支线去修。

## 结论（一句话）

计量代理在**拒绝一个带请求体的模型调用**时会把自己崩掉：拒绝的 503 永远送不到客户端，Claude 那头只能干等到超时——用户看到 request timeout，真正的错误原因被吞掉。而这些调用被拒的原因是：**支线（task）轮次的 token 带的是支线自己的 id，机器身份钉扎只记在房间上，按支线 id 查身份永远查不到**。

## 证据链（2026-09-05 现场核实，均可复跑）

1. **崩溃现场**：`docker logs cheese-metering-proxy` 近 48h 有 **350 条 `mitmproxy has crashed!`**，崩溃前一行总是 `refusing a machine turn: no identity to send it as (... path=/v1/messages ...)`，异常为 mitmproxy 12.1.2 的 `NotImplementedError: Can't set a response and enable streaming at the same time`。
2. **触发机制**：<&deploy/metering-proxy/billing_addon.py> 的 `requestheaders` 在 host 校验通过后即设 `flow.request.stream = True`（#654 防 OOM），之后的拒绝路径（`_refuse` 设 `flow.response`）与之冲突。**只在请求带 body 时炸**：GET 类拒绝正常送 503；`POST /v1/messages` 48h 内 **68 次拒绝 0 次送达**（grep `-A3 'refusing.*v1/messages'` 后数 `<< 503` 为 0、数崩溃为 68）。代码注释"A path below may still refuse via flow.response, which short-circuits regardless of this flag"是错的。已核实**线上部署与仓库源码逐字节一致**（diff 无差异），改仓库即可。
3. **用户侧表现**：连接崩掉/挂死 → Claude 报 request timeout → 重试 → 再崩。日志里同一连接整 10 分钟后再崩一次是重试脚印。
4. **为什么被拒（第二层根因，实测钉死）**：被拒轮次全来自 @wangchangxin 的 agent 项目（db50af9d）房间 29f66f0b 的三条支线（8683b1bb/c8a1c10f/40f138b6）。用后端容器 `mint_scoped_token` 分别铸 token 打 `POST http://172.17.0.1:8081/llm/admission` 实测：**房间 id → 返回 `upstream: m784:…`；支线 id → 返回里没有 upstream**。链路：admission（<&backend/app/api/routes/llm_proxy.py:159>）→ `ccproxy_upstream_for_topic`（<&backend/app/domain/machine/repositories.py:320>）只查 `device_topic` 钉扎表，而钉扎只有房间有（`bind_topic_device` 只绑房间）；支线跑在房间机器上（#702）但这条查找没跟上。
5. **次要事实**：机器释放（`delete_device`，<&backend/app/domain/device/sql_repository.py:133>）会连带删钉扎，老话题从此也查不到身份——影响已释放机器的旧话题，属同一修法覆盖面之外的边缘，先不动。
6. **顺手发现的隐患**：`AdmissionGate`（<&deploy/metering-proxy/cheese_billing_core.py:274>）判决缓存按 **project** 键控 30s，但 upstream 是 per-topic 的——同项目两话题在两台机器时，30s 窗口内会拿到对方的机器身份（账记错人、ccproxy 拒绝）。一并修。

## 修法（已拆支线执行）

1. **代理崩溃**：`flow.request.stream = True` 挪到所有拒绝判断之后（确定转发才开流式）；正常转发路径必须保持流式（不能退回整块缓冲，那是 OOM 修复 #654）。
2. **支线失身份**：`ccproxy_upstream_for_topic` 先把传入 id 当支线查 `tasks`（`tree_id`/`room_id`），是支线就换成房间 id 再走钉扎查找；房间 id 直查行为不变。
3. **缓存串身份**：AdmissionGate 缓存键加上 topic 维度。

## 进展

- 2026-09-05：根因定位完成，三条修法已写进支线简报，支线已派出。部署（拷贝到宿主 `/home/nictheboy/cheese-proxy-new/` + 重启容器、后端上线）由本房间在合并后跟进。

## 待办

- 支线：按简报修三处 + 测试，push 后 conclude（不递卡，由房间递给 @wangchangxin）。
- 房间：验收、递卡、合并后跟进部署，并回头确认 agent 项目的循环止住。
