---
title: 系统提示词参考
kind: 参考
summary: 每一类会话（频道、任务、讨论串、私聊、文档里的芝士、个人芝士、整理记忆、写项目现状）读到什么：系统提示词由哪几篇拼成、各自的出现条件、全文、每条消息后的提醒和工具，以及平台说明库的原文——在构建时从代码生成，不跟着代码漂移。
covers:
  - backend/app/domain/prompts/
  - backend/app/domain/memory/files_store.py
  - backend/app/domain/agent/skills.py
  - backend/app/domain/skill_library/
  - backend/app/domain/task/teaching.py
---

# 系统提示词参考 {#ref-prompt}

这一页是[提示词注入与上下文管理](/dev/context#system)的原文附录：那边讲每一块为什么在这里、顺序的道理和防注入的几道边界，这里给每一类会话的原文、出现条件和工具。

提示词的文字在 `backend/app/domain/prompts/pieces/` 下，一篇一个 Markdown 文件，只有原文和变量空位；每一类会话读哪几篇、在什么条件下读，写在 `profiles.py` 那一类的清单里。下面的内容由 `docs/site/gen/prompt.py` 在构建文档站时生成：它对每一份清单真跑一遍渲染（每个开关都打开），所以加了一篇、换了顺序、改了工具，这一页跟着变。

> 讲：每一类会话的系统提示词、开场样本、每条消息后的提醒、工具，平台说明库的原文。不讲：逐轮消息的格式、防注入的边界，那些在[提示词注入与上下文管理](/dev/context)。
