/**
 * 预览精简构建里 `@/router` 的替身（临时，供话题预览用）。
 *
 * **为什么需要它**：`AdminSpacesPage.vue` 从 `@/network/api/spaces` 进来，那条链一路
 * 走到 `network/Interceptors/hooks/refreshToken.ts`，而它 `import router from '@/router'`
 * —— 只为了 token 过期时 `router.replace('/account/signin')` 这一句。`@/router` 是
 * **整棵应用路由树**，于是预览这份产物把工作区、WorkPanel、CodeEditor 和 monaco
 * （含 7MB 的 ts.worker）一起装了进去：量出来 proto.js 从 ~2MB 涨到 12.5MB，而预览
 * 通道实测 ~140KB/s，这正是这个入口存在的理由（见 `proto-feedback.ts` 顶部注释）。
 *
 * 预览里没有登录态可过期，也没有 `/account/signin` 这条路由（`installPreviewFetch`
 * 在 `fetch` 那一层就回答了所有请求），所以这一句是个死分支。替身只保证**调用不炸**。
 *
 * 真正的路由树仍然由 `proto-feedback.ts` 自己那份（五条反馈路由 + hash 历史）提供，
 * 那个 import 是 `@/router/feedback`，与这里的 `@/router` 不是同一个模块 —— 别名在
 * 配置里写成 `/^@\/router$/` 正是为了不把子路径一起吃掉。
 */
const stub: {
  replace: (to: string) => Promise<void>
  push: (to: string) => Promise<void>
} = {
  replace: () => Promise.resolve(),
  push: () => Promise.resolve(),
}

export default stub
