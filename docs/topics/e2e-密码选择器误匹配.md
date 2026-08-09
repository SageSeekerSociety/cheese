## 状态：修复已落地，正在做实测验证

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

## 验证方式（因沙箱无 Docker/Postgres，绕开真实登录流程验证选择器本身）

思路：登录页是纯前端渲染，不需要后端/DB 就能挂载出密码框 + 眼睛图标两个元素。所以不起后端，只起
`vite` 前端 dev server + Playwright chromium，直接在 `/account/signin` 页面上验证：
- 改动前的写法 `getByLabel('密码')`（不带 exact）应该命中 2 个元素（复现 strict-mode violation）
- 改动后的写法（`exact: true`）应该只命中 1 个，且能正常 fill

当前在后台跑 `pnpm install`（frontend + e2e）和 `playwright install chromium`，验证脚本还没执行。

## 下一步

1. 装完依赖后，起 `vite --port 3000`，写一个一次性 Playwright 脚本（不经过 webServer/backend）直接访问
   `/account/signin`，断言上面两条选择器行为
2. 如果沙箱资源/时间不够跑完整 `pnpm exec playwright test`（需要真后端+PG+demo 种子数据，本沙箱没有 Docker），
   就如实说明：选择器修复已通过读 Vuetify 源码 + DOM 结构定性验证，但没有跑通完整 e2e 用例，把这个局限写进验收说明，不谎报"测试全绿"
3. 递验收卡给 wangchangxin，附上根因说明和验证方式（含未能完整跑 e2e 的局限，如果最终没跑成）

## 范围边界（不越界）

只动 `e2e/tests/helpers.ts` 和 `e2e/tests/auth.spec.ts` 里的选择器；不碰 `build.yml` 并发策略、不碰
`deploy-dev.yml` 门禁条件——那是另一条给 wangchangxin 的决策请求，不属于这张卡。
