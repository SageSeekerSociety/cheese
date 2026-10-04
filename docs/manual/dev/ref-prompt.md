---
title: 系统提示词参考
kind: 参考
summary: 芝士的新会话开场时读到什么：系统提示词和开场快照各由哪些块组成，每块的出现条件、预算和原文，以及平台说明库的原文——在构建时从代码生成，不跟着代码漂移。
covers:
  - backend/app/domain/agent/harness/prompt.py
  - backend/app/domain/memory/instructions.py
  - backend/app/domain/memory/files_store.py
  - backend/app/domain/agent/skills.py
  - backend/app/domain/agent/skill_library/
  - backend/app/domain/task/teaching.py
---

# 系统提示词参考 {#ref-prompt}

这一页是[提示词注入与上下文管理](/dev/context#system)的原文附录：那边讲每一块为什么在这里、顺序的道理和防注入的几道边界，这里给每一块的原文、出现条件和预算。想改提示词又不想先通读一遍 `prompt.py` 时，从这一页开始。

下面的内容由 `docs/site/gen/prompt.py` 在构建文档站时生成：它真的用一组覆盖了每个开关的样本参数调用一次 `build_system_prompt` 和 `build_session_opening`，把两者拼起来按块切开，再从 `skill_library/` 取平台说明的原文。所以块被改名、加进来、换了顺序，这一页跟着变——改了提示词却忘了改文档这件事不会发生。

> 讲：系统提示词和开场快照的每一块、平台说明库的原文。不讲：逐轮消息的格式、防注入的边界，那些在[提示词注入与上下文管理](/dev/context)。
