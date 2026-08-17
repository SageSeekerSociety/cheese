## 目标

让「递验收卡不写提交标题」在后端被挡住，而不是静默兜底成 `chore: <话题标题>`（PR #500 就是这么来的）。

## 状态：代码改完了，等全量测试

改动已全部落盘，ruff / pyright 已绿，相关测试子集（accept 系列 12 个文件 + CLI）已绿。全量 pytest 正在跑（用 `cheese await`，几十分钟），出结果后递卡给 @彭文博。

## 做了什么

**后端**（<&backend/app/domain/review/services.py>）：`AcceptService.create_card` 里，`change_subject` 缺失或全是空白直接拒绝，错误信息里给了写法（`type(scope): description`、type 清单、英文祈使句、≤72 字符、不加句号）和一条完整的 `cheese accept-request` 例子——读这句话的是一个下一轮就要重递的 agent，只说"缺参数"等于让它再猜一轮。原有的「格式非法」校验（`commit_message.check_subject`）保持不动。

**没用 Pydantic 必填**：那会返回 pydantic 自己的 `Field required`，而要送到 agent 手上的是那段能教会它怎么写的话。所以 <&backend/app/domain/review/schemas.py> 的类型仍是 `str | None`，只改了注释说明为什么。

**CLI**（<&backend/sandbox/cheese>）：`--subject` 改成 `required=True`，argparse 在发请求前就退出；删掉了原来那段「事后打一行警告」——卡都递出去了，警告没有意义。

**兜底保留**：<&backend/app/domain/review/pr_text.py> 的 `fallback_subject()` 一行没动。历史卡片的 `change_subject` 是 NULL，删掉它们的 PR 标题和合并标题会炸。docstring 补了一句说明它现在只服务历史行。

**文档四处**已同步：`stage_working.md`、`stage_gate.md`、根 `CLAUDE.md`、`pr_text.py` docstring。

**测试**：新增 4 条（缺 subject → 拒绝且文案里有写法和例子 / 空白串 → 同样拒绝 / 合法 subject 原样落库 / **历史 NULL 行仍走兜底**）+ CLI 2 条（不带 `--subject` 时确实没发请求 / 带了正常发）。约 20 个集成测试文件里的递卡调用补上了合法 subject。

## 一处和简报不一致，已抛回确认

简报写「返回 400」，但仓库里 `ValidationError.code = 422`——**现有的「格式非法」拒绝本来就是 422**（`test_pr_publish.py` 里那条测试断言的就是 422）。我按 422 实现，理由是跟现有行为一致、不改已有测试的语义。要改成 400 是把 `ValidationError` 换成 `BadRequestError`，一行的事，但那样「缺 subject」和「格式非法」会分成两个状态码。已用选项问题抛回去。

## 下一步

1. 全量 pytest 出结果（宿主缺 procps / openssh-client 那批照旧会挂，如实标注）。
2. `check.sh` 跑一遍，数清有几项 SKIP（它会假绿，退出码 0 不代表检查跑了）。
3. 递卡。
