## 目标

个人中心 → 设置 → 个人资料里改昵称，原来要求**至少 4 个字符**，「李」「Z」这类正常昵称存不下去。改成：

- 不能为空（首尾空格自动去掉）
- 至少包含一个汉字或一个英文字母（挡住纯符号、纯空白的昵称）

需求来自 @蔡松洋。

## 改了什么

- <&frontend/src/views/user/settings/Profile.vue>：表单校验从 `min(4).max(16)` 换成「去空格 → 非空 → 最多 50 字 → 至少含一个汉字或字母」，报错文案也跟着改成中文提示。
- <&backend/app/domain/user/services.py>：新增 `normalize_nickname()`，在 `UserProfileService.update_profile` 里对昵称做同一套校验并去首尾空格；不合规返回 422。之前后端对昵称**完全没有校验**，直接发接口能把昵称改成空字符串。
- 测试：<&backend/tests/unit/test_user_services.py>（单元）+ <&backend/tests/integration/test_user_profile.py>（接口层 200/422）。

## 顺带定下的两个口径

- **上限 50**：原来前端是 16，而 OAuth 注册那条路（<&frontend/src/views/account/OAuthComplete.vue>）允许 50——同一个人注册时能起 20 字昵称，回设置页却存不回去。统一成 50。
- **纯数字昵称不再允许**：「123456」不含汉字/字母，按新规则被拒。这是需求的直接推论，如果不想要可以放宽。

## 待办

- 等前后端检查跑完（后端集成测试 + 前端 lint/vitest），绿了递验收卡。
