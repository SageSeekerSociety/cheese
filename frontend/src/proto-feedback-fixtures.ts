/**
 * proto-feedback-fixtures.ts — 预览构建的假数据，以及一个只认反馈接口的 fetch 拦截器。
 *
 * **这一份只被 `src/proto-feedback.ts` 引用**（预览的精简入口），应用本体一行都不碰：
 * 页面照旧通过 store → `api.ts` → `fetch` 取数，这里在 `fetch` 那一层把反馈的几条路由
 * 接过去。上一轮的做法是让 store 直接读一个内存 mock（`lib/feedbackMock.ts`），好处是
 * 简单，代价是**应用代码里长着一个只有预览才走的岔路**；这一轮页面接真接口，岔路挪到了
 * 构建产物里 —— 真实构建（`vite.config.ts`）不含这个文件，预览构建（
 * `vite.feedback-proto.config.mts`）才 import 它。
 *
 * 为什么预览需要它：预览通道给的是一个**单文件 HTML 和一个静态服务**，没有后端、也没有
 * 登录态（`/api/feedback/*` 全要鉴权），所以真接口在那边一律不通。不接这一层，人点开
 * 看到的就是一个空列表 —— 而「预览是白的」正是当初做这个入口要解决的事。
 *
 * 因此：**界面是真的，数据是假的**。要看真实数据请在本机起 backend + frontend。
 * 假数据照着 `backend/app/domain/feedback/models.py` 的字段造，覆盖几种要看的东西：
 * 私密条目（没有支持按钮、只挂中性标签）、agent 发现人代提交的那条（两个 handle 不同）、
 * 已修复的 bug（默认沉底）、走完四级停在「已上线」的（沉底规则必须和「已修复」一致）、
 * 支持数过门槛的（进「热门」）、带安全标记的（管理端第四栏）。
 *
 * **这一份现在只是个薄入口**：夹具按职责分在 `./proto-feedback-fixtures/` 下面 ——
 * `support`（原语：常量、两条工厂、线程分页）、`handwritten` 与 `filler`（两批数据）、
 * `rows`（那份数据本身与增删）、`list`（列表的读侧）、`stats`（看板里的反馈、用量、
 * 平台）、`statsRoutes`（看板里的耗时、管线、产品、集成）、`members`（成员名单）、
 * `routes`（路由表）。从这里出去的名字和原来一模一样，调用方一行都不用改。
 */
export type { MockReply } from './proto-feedback-fixtures/routes'
export { routes } from './proto-feedback-fixtures/routes'
