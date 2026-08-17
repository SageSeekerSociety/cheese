## 目标

让「递验收卡不写提交标题」在后端被挡住，而不是静默兜底成 `chore: <话题标题>`（PR #500 就是这么来的）。

## 状态：做完了，已递卡给 @彭文博

代码、测试、文档全部改完；全量 pytest 4899 passed / 0 failed，闸门那条检查 7/7 通过。

## 做了什么

**后端**（<&backend/app/domain/review/services.py>）：`AcceptService.create_card` 里，`change_subject` 缺失或全是空白直接拒绝。错误信息不是"缺参数"，而是一段能照着改的话——写法 `type(scope): description`、type 清单、英文祈使句 / ≤72 字符 / 不加句号，外加一条完整可复制的 `cheese accept-request` 命令。读这句话的是一个下一轮就要重递的 agent，只说"缺参数"等于让它再猜一轮。原有的「格式非法」校验（`commit_message.check_subject`）一行没动。

**没用 Pydantic 必填**：那只会返回 pydantic 自己的 `Field required`，文案不可控。所以 <&backend/app/domain/review/schemas.py> 的类型仍是 `str | None`，只改注释说明为什么门设在服务层。

**CLI**（<&backend/sandbox/cheese>）：`--subject` 改成 `required=True`，argparse 在发请求前就退出；删掉了原来那段递完卡才打的警告——那时候卡已经出去了，警告等于没有。

**兜底保留**：<&backend/app/domain/review/pr_text.py> 的 `fallback_subject()` 一行没动。历史卡片的 `change_subject` 是 NULL，删掉它们的 PR 标题和合并标题会炸。docstring 补了一句说明它现在只服务历史行。

**文档四处**已同步：`stage_working.md`、`stage_gate.md`、根 `CLAUDE.md`、`pr_text.py` docstring。

## 验证

| 项 | 结果 |
|---|---|
| 缺 subject 建卡 | 拒绝，`message` 里含 `type(scope): description` + 完整 `cheese accept-request` 例子 |
| subject 是 `"   "` | 同样拒绝 |
| 纯中文标题 | 仍旧拒绝（原行为没回归） |
| 合法 subject | 正常建卡，原样落库 |
| 历史 NULL 行 | 仍走兜底 `chore: <话题标题>`（新增测试守着） |
| `cheese accept-request alice "x"` 不带 `--subject` | 非零退出，**没有**发出 POST |
| 全量 pytest | 4899 passed, 30 skipped, **0 failed**（247s） |
| `check.sh --no-tests`（闸门那条） | 7/7 passed，1 skipped = pytest 本身（脚本自己关的） |

测试改动：新增后端 4 条 + CLI 2 条；约 20 个集成测试文件里的递卡调用补了合法 subject。其中 4 条原本断言 422「已有卡不能再递」的测试，加必填后会因为缺 subject 而"通过"——那是过错原因通过，都补上了真 subject。

## 和简报不一致的一处（已抛问题，未答）

简报写「返回 400」，但仓库里 `ValidationError.code = 422`，而且**现有的「格式非法」拒绝本来就是 422**。我按 422 实现——和现有行为一致、不改已有测试的语义。要改 400 是把 `ValidationError` 换成 `BadRequestError` 一行的事，但那样「缺 subject」和「格式非法」会分成两个码。

## 查出来但故意没做

`docs/topics/提交与PR规范.md`（#497 那个话题自己的实况文档）里还写着「不给 `--subject` 就回落成 `chore: <话题标题>`」，现在和实际行为相反。**没动它**——那是别人话题的历史记录，不在这张卡的范围里，但谁去翻会被带偏，值得单独处理。
