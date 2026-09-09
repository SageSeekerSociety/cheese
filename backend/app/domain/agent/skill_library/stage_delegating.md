---
name: stage-delegating
title: 阶段·任务执行
scenarios: [stage:delegating]
description: 房间内创建独立任务，选择执行者和验收人，并从各自工作目录交付
---

# 当前阶段：任务执行

房间保留对话与会话。要交付仓库改动，即使主 agent 自己只改一行，也先创建任务。每条任务拥有自己的工作目录、分支与 PR。

`cheese split "标题" --brief "目标、约束、验收标准" --reviewer <handle>` 返回任务 id 和工作目录。只有项目已配置默认验收人时才可省略 `--reviewer`，派活时检查返回的人选。任务依赖尚未合并的另一条任务时，加 `--base-task <父任务 id>`。

任务可以由人、主 agent 或原生后台分身执行；split 不会启动执行者。自己执行就进入返回目录。派给分身时，把简报、已核实的证据和任务目录一起传入其 prompt，然后用 `cheese bind <task_id> <agent_id>` 关联任务时间线。同一任务目录只交给一个执行者写。分身停止只记录它的说明，不关闭任务。

中途补充要求，直接给正在运行的分身发消息；已停止且没有后台进程继续写任务目录时在房间会话中重新派遣并绑定。需要留在任务记录里的说明用 `cheese tell <task_id> "内容"` 保存，该命令不唤醒执行者。

执行者在自己的任务目录按仓库约定完成检查并提交。已连接 GitHub 时，首个提交同步后平台创建该任务的 draft PR。执行者用 `cheese ready` 标记可评审；需要请人验收时用 `cheese accept-request --subject "fix(scope): describe the change" --body "原因、验收证据和未完成检查"`。每条任务独立请求验收，不等待房间凑齐一批。

调研和活动安排等不交付仓库文件的工作，直接使用来源资料和平台文档，不必为了更新进度制造代码提交。
