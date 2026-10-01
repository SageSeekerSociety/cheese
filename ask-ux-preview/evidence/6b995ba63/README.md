## 固定提交定点结果

源码：`6b995ba6375f7588c89bf32a4d860a5cd7f7e681`。
日期：2026-10-01。PR #2213 保持草稿，未合并或部署。

| 检查 | 结果 | 原始输出 |
| --- | --- | --- |
| 后端 C1/C2/C3 依赖边界 | 3 kept，exit 0；未增加豁免 | ask-v6-boundaries.txt |
| 跨域 repository guard | 22 passed，6.45s | ask-v6-repository-guard.txt |
| consecutive / gateway 四个消费者 | 4 passed；随后 busy 因漏设固定 Claude 路径失败 | ask-6b-targeted.txt |
| 固定 Claude 2.1.282 原生 busy | HTTP 422：questions 必须含 1-8 题；首败停止 | ask-6b-native-targeted.txt |
| 组回滚、人类创建拒绝、回执事务重试 | 3 passed | ask-6b-extraction-targeted.txt |
| 原生 echo 提交故障恢复 | 新进程报 Completion consumer is not bound | ask-6b-extraction-targeted.txt |

原生 busy 首败后 idle、idle-race、accepted-start 未执行。echo 单独执行后仍失败，不记原生接续通过。未重跑已通过的四个完成消费者。

### 测试环境

有效 heavy 窗口内串行执行。PostgreSQL 使用本机 5433 测试服务，槽名 `ask_s_concurrency`，Redis 12。

迁移重排后，调用仓库现有 fresh-migration fallback 重建该槽的两个数据库：`cheesex_test_ask_s_concurrency` 和 `cheesex_test_ask_s_concurrency_c`。进程内令 `_ensure_template` 返回 False，避免建立或清理其他使用者的共享模板；未改仓库 fixture 或断言。

原生固定可执行文件：`/var/tmp/ask-s-native-2.1.282/claude`。第一次遗漏环境变量的输出完整保留。补设后不再执行四个已绿消费者。

### 证据范围

组 HTTP 测试使用真实授权与 PostgreSQL，但原生来源和提交后派发由 fixture 提供。完成消费者使用脚本通道，保留真实登记、回显和完成校验；不等于真实模型进程验证。

O 的前端证据固定在 `9d2c8ecf0`，采用真实组件和固定本地数据。五项通过不证明真实后端提交或原生接续。中文 PDF 已发布到房间 `ask-ui-browser-9d2/提问体验前端证据.pdf`，下载 SHA256 为 `8ff359b73628555d5916bb054d9dfb1d83ad5f4896f00f4a8fc9d91012abe395`。Root 随后完成全部五页 110DPI 视觉复核，未见裁切、重叠或缺字；报告见同目录 `root-visual-review.md`。此结论只关闭 PDF 视觉检查，不扩大固定数据证据范围。

此检查点的自然 Required 尚无运行记录；最近可见运行属于旧提交 `2a00245e9`，不能作为当前提交门禁结论。
