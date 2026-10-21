---
paths:
  - "**/tests/**"
  - "**/test_*.py"
  - "**/*_test.go"
  - "**/*.spec.ts"
  - "**/*.test.ts"
  - "**/*.test.mjs"
---

# 写测试之前

原则在 AGENTS.md「Test behaviour, never the implementation」。这里是落笔前的三步。

1. **先写那一句规则。** 测试名或 docstring 用一句话说出它守的规则，没读过代码的人也能判断对错：「排队的人按来的顺序进」「取消对话框，操作不执行」。如果这句话说的是代码长什么样、文件里写了什么，这个测试就不写。一个测试要靠一大段 docstring 论证自己为什么该存在，通常是过不了第二问。
2. **问两个问题。** 实现坏了，它会红吗？换一种写法实现同样的行为，它还绿吗？两个都是「是」才留。
3. **在能观测到这条规则的最低一层断言结果。** 结果是返回值、HTTP 响应、再读回来的数据库状态、屏幕上按角色或标签找到的东西、发出的事件；不是调用了谁、调用了几次、拼出了什么命令行。外部系统用 `backend/tests/support/` 的共享替身，断言替身最后的状态，不断言它的调用记录。数据库用真的（`db_session`），不 mock repository。

改动让一个老测试红了：行为是有意改的，就改断言；行为没变，它就是 change detector——改成断言结果，或者问派活的人能不能删，不要把期望值改成新的样子了事。

可以照着写的：`backend/tests/unit/test_admission.py`（真 Redis、两个客户端，测排队与租约）、`backend/tests/unit/test_a_dead_turn_frees_its_seat.py`（docstring 写清它守的 bug）、`frontend/src/composables/useQuestionInvitations.spec.ts`（切换题目时迟到的响应不落到新题目上）。
