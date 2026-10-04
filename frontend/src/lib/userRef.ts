// 一个人（人或 AI 队友）在一句话里被提到时的样子和去处，只在这里定义一次。
//
// 正文里的 `<@handle>` 画成 chip（lib/refChip.ts，点击由外层容器委派），正文之外
// 的句子——「由 X 授权」「X 采纳」——用 components/common/UserRef.vue。两条路径的
// 跳转都从这里取：各写一份的话，迟早一个样子两个去处。
import type { RouteLocationRaw } from 'vue-router'

/** 一颗 @chip 的去处。展示组件（components/common/UserRef.vue）只认这个类型，
 *  不 import vue-router：它得能在没装路由的树里单独渲染，类型也从这里取。 */
export type UserRefTarget = RouteLocationRaw

/** 点一个人名去哪：在项目里就是项目里的成员页（← 回名册），项目外是他的个人主页。
 *  handle 是认人的唯一依据——人和 AI 队友一样，主页都按 handle 找。 */
export function userRefRoute(handle: string, projectId?: string | null): RouteLocationRaw {
  return projectId ? { name: 'member', params: { projectId, handle } } : { name: 'UserPage', params: { handle } }
}
