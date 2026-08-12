## 状态：修复已落地，已递验收卡（含未能实测的局限说明）

## 根因（已用 Vuetify 3.9.3 源码定位，非猜测）

`InputIcon.js`（`VInput` 内部，`append-inner-icon` 带 `@click:append-inner` 时走这条路径）给显示/隐藏密码的眼睛按钮自动生成
`aria-label = "{fieldLabel} appended action"`。本项目 `createVuetify()` 未配置 `locale`，用的是默认英文模板
（`en.js`: `appendAction: '{0} appended action'`），插值后就是字面量 `"密码 appended action"`——
天然包含子串"密码"。`getByLabel('密码')` 默认子串匹配，于是同时命中：
1. `<label for="...">密码</label>` 关联的真实密码输入框
2. 眼睛按钮（`aria-label="密码 appended action"`）

触发 Playwright strict-mode violation。`用户名` 字段没有 append 图标，不受影响，helpers.ts 里没有其他同类风险点。

## 已改动

- `<&e2e/tests/helpers.ts>`：`login()` 里 `getByLabel('密码')` → `getByLabel('密码', { exact: true })`，
  精确匹配后只命中真实输入框（其 accessible name 就是精确的"密码"），不再命中按钮。
- `<&e2e/tests/auth.spec.ts>`：发现同一个 helper 之外还有一处直接内联的 `getByLabel('密码')`（"wrong credentials" 用例），
  同一个 bug，一并改成 `exact: true`。全仓 `getByLabel` 命中只有这两处，已全部覆盖。

## 验证方式和结果（如实记录，没有跑通实机浏览器测试）

尝试过的路径：登录页是纯前端渲染，不需要后端/DB 就能挂载出密码框 + 眼睛图标两个元素，所以想绕开后端，只起
`vite` 前端 dev server + 一次性 Playwright 脚本直接访问 `/account/signin` 断言选择器命中数量。

- frontend `pnpm install` 第一次因 pnpm 全局内容寻址 store 的 hardlink chmod 权限问题（`EPERM chmod .../tsc`
  等）失败，换成 `--store-dir` 指到本地全新目录后装成功，vite 也顺利起在 3000 端口。
- 到实际跑 Playwright 脚本这步，卡在 `chrome-headless-shell: error while loading shared libraries:
  libglib-2.0.so.0`——沙箱里没有 root，`playwright install --with-deps` 需要的系统依赖装不了（`sudo`
  认证失败），普通 `apt-get install` 也因为拿不到 dpkg 锁被拒绝。这是沙箱本身的限制，不是代码问题，
  也没法在这个话题里绕过去（换个沙箱/有 root 权限的环境应该就没这个问题）。

**结论：selector 修复没有跑通实机浏览器验证**，只做到了源码级的定性证明：直接读了 Vuetify 3.9.3 的
`InputIcon.js` 和 `locale/en.js` 源码，确认了 aria-label 拼接逻辑（`"{fieldLabel} appended action"`），
这是确定性的库行为，不依赖运行时环境，可信度接近实测，但不等于实测——如实告知 wangchangxin，请人工过一遍或者在有
Docker/root 权限的环境里跑一次 `task e2e:test` 做最终确认。

## 已递交

验收卡已递给 <@wangchangxin>，说明里包含：改动内容、根因证明方式、以及"未能实机跑通"这个局限。

## 范围边界（不越界）

只动 `e2e/tests/helpers.ts` 和 `e2e/tests/auth.spec.ts` 里的选择器；不碰 `build.yml` 并发策略、不碰
`deploy-dev.yml` 门禁条件——那是另一条给 wangchangxin 的决策请求，不属于这张卡。
