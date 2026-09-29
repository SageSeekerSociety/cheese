// 一个人（人或 AI 队友）在一句话里被提到时的样子和去处，只在这里定义一次。
//
// 对话里的 `<@handle>` 由 renderMessage 拼成 HTML（v-html，点击由外层容器委派），
// 对话之外的句子——「由 X 授权」「X 采纳」——用 components/common/UserRef.vue。
// 两条路径的 markup 和跳转都从这里取：各写一份的话，迟早一个样子两个去处。
import type { RouteLocationRaw } from 'vue-router'

/** 点一个人名去哪：在项目里就是项目里的成员页（← 回名册），项目外是他的个人主页。
 *  handle 是认人的唯一依据——人和 AI 队友一样，主页都按 handle 找。 */
export function userRefRoute(handle: string, projectId?: string | null): RouteLocationRaw {
  return projectId ? { name: 'member', params: { projectId, handle } } : { name: 'UserPage', params: { handle } }
}

function escapeHtml(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')
}

/** 消息正文里那颗 @chip 的 HTML。样式是全局的 `.mention`（style.css）。 */
export function userRefHtml(handle: string, name: string): string {
  return `<span class="mention" data-handle="${escapeHtml(handle)}">@${escapeHtml(name)}</span>`
}
