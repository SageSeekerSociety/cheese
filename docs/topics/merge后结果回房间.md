## 状态：开工中

一句话：AcceptService.accept() 决议 merge 的成功/冲突/失败三种收尾，各调一次卡1 webhook 原语的内部函数 `post_with_retries`，把结果发回触发采纳的话题时间线。

## 依赖确认

- 卡1（webhook 原语）已合并进 main：`backend/app/domain/webhook/service.py` 的 `post_with_retries(session_factory, *, project_id, topic_id, content, source)` 就是要调的内部函数——不鉴权、不走 HTTP、调用方直接传 `async_session_factory`。
- 检查过 `ACCEPT_VIA_PR`（PR #195 的开关）：这个沙箱的 main 上还搜不到，说明 #195 还没合并。按简报指示，本卡只覆盖现在活着的 `backend/app/domain/review/services.py::AcceptService.accept()` → `ws.merge_topic()` 路径；#195 合并后再补一处调用，不算这张卡的返工。

## 落点与设计

`AcceptService.accept()`（`backend/app/domain/review/services.py:225`）里 merge 结果有三个收尾分支，都要接一次 `post_with_retries(async_session_factory, project_id=topic.project_id, topic_id=topic.id, content=..., source="accept")`：

1. `ws.merge_topic()` 抛异常（第 271-278 行）→ 失败通知，紧接着仍然 `raise ValidationError`。
2. 合并冲突 `conflicts` 分支（第 280-291 行）→ 失败通知（冲突原因），card 状态转 `conflict`，正常 return。
3. 非冲突的其他合并失败（`noop` 不为真，第 293-302 行）→ 失败通知，紧接着仍然 `raise ValidationError`。
4. 合并成功（含 noop 直接可验收的话题）走到最后 `topic.status = archived`（第 304 行往后）→ 成功通知，复用已经算好的 `card.note`（push 结果）。

`post_with_retries` 走自己的 session（`async_session_factory`），跟 `AcceptService` 自身的 `self._session` 无关——即便后续 `raise` 导致 `self._session` 被上层 `get_db` 回滚，通知已经用独立事务提交了，不会跟着回滚，这正是我们想要的（合并失败也要让房间知道）。

来源标注：`source="accept"`。

## 下一步

1. 改 `accept()`，接上四个收尾点的 `post_with_retries` 调用。
2. 补两类单测（成功 / 冲突失败），断言 `post_with_retries` 收到正确的 project_id/topic_id/source/成功失败语义。
3. 用 pgserver 起真实 Postgres，`task check` 全绿。
4. 递验收卡给 wangchangxin。
