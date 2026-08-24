## 目标

个人中心 → 设置 → 个人资料里改昵称，原来要求**至少 4 个字符**，「李」「Z」这类正常昵称存不下去。改成：

- 不能为空（首尾空格自动去掉）
- 至少包含一个汉字、英文字母**或数字**（只挡纯符号、纯空白、纯表情）

需求来自 @蔡松洋；「纯数字昵称可以」是他在实现过程中补的口径。

## 改了什么

- <&frontend/src/views/user/settings/Profile.vue>：表单校验从 `min(4).max(16)` 换成「去空格 → 非空 → 最多 50 字 → 至少含一个汉字/字母/数字」，提示语改成中文。
- <&backend/app/domain/user/services.py>：新增 `normalize_nickname()`，在 `UserProfileService.update_profile` 里做同一套校验并去首尾空格，不合规返回 422。之前后端对昵称**完全没有校验**，直接调接口能把昵称改成空字符串。
- 测试：<&backend/tests/unit/test_user_services.py>、<&backend/tests/integration/test_user_profile.py>。

## 定下的口径

- **上限 50**：原来前端写死 16，而 OAuth 注册那条路（<&frontend/src/views/account/OAuthComplete.vue>）允许 50——注册时能起 20 字昵称、回设置页却存不回去。统一成 50。
- **纯数字放行**（`2026` 可用）；纯符号 `!!!???`、纯短横 `---`、纯 emoji 仍被拒。
- 未放宽的一点：日文假名 / 韩文 / 西里尔字母**单独出现**会被拒（规则按「汉字/英文字母/数字」写死）。需要的话改一个正则即可。

## 验证结果

- 后端单元 + 接口测试：`tests/unit`（3321 通过）、`tests/integration/test_user_profile.py` 20 项全过（含新增的 1 字昵称、纯数字、空/纯符号 422）。
- 前端：eslint 0 error、vitest 51 文件 452 项全过；另用真实 zod 跑了一遍 schema，确认「  小明  」会被存成「小明」。

## 待办

- 质量闸门（check.sh --no-tests）跑完即可递验收卡。
