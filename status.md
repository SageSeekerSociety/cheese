## 目标

一次探针：验证两阶段采纳（PR迭代式）在 wangchangxin 重新连接 GitHub 账号后，现在能不能真的开出 PR。不修任何东西，只观察。

## 背景

此前两次采纳都降级成了直推 main：
- 18:21 那次**有**「两阶段采纳前快照」commit（进了 PR 路径，push 时因 token 未解密被 GitHub 401）——已定位并修复（密文 token bug）。
- 19:45 那次**没有**快照 commit（前提解析在更早的地方就返回 None）。

密文 bug 已于当天修复上线。wangchangxin 已重新连接 GitHub 账号，理论上两种失败成因都已消除，这次探针用于验证。

## 已完成

- 建了本话题的最小无害文档改动：`docs/topics/两阶段采纳-PR探针-0809.md`（探针记录本身，不承载信息价值）。
- 已递验收卡给 wangchangxin，请求直接采纳。

## 下一步 / 观察项

采纳后守着看，向父话题报告：
1. 有没有开出真实 GitHub PR？PR 号、URL。
2. 验收卡 `status` 是 `pr_open` 还是 `accepted`；`pr_number`/`pr_url`/`pr_head_sha` 是否写上（`cheese api GET /topics/<topic_id>/accept-card`）。
3. 共享工作区里有没有出现「两阶段采纳前快照」commit（判断有没有进入 PR 路径的关键指纹）。
4. 若又降级：区分「有快照=进了PR路径但push/开PR失败」vs「无快照=前提解析就返回None」。

结果会立刻用跨话题评论唤醒父话题（`e593d59c-ca17-4dcc-b0ff-57980823fd22`），不等它自己来问。

## 约束

- 不碰 `backend/`、`frontend/`、`e2e/`、`.github/`、`deploy/`。
- 不修 bug、不跑全量测试、不重构。速度优先。
- 若因 `claude-review.yml` 在所有 PR 上都红被唤醒："去修CI"——这是已知现象（该 action 本身配置坏了，跟改动无关），除了给它加路径过滤（已拍板可接受的修法）外什么都不改。
