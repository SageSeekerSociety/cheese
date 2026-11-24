// 正文里指向站内页面的链接在应用里翻页，不整页重载。
//
// 一段 Markdown 画出来的正文（消息、文档、文件预览）里可以写一条站内链接，比如一道
// 赛题的说明里那条「查看完整赛题要求」`[查看完整赛题要求](/spaces/3/tasks/9)`。它是一条
// 真的 `<a href="/spaces/3/tasks/9">`，点下去默认是浏览器整页重载：应用整个重新启动，
// 内存里的一切丢掉、chunk 重下一遍。站内地址自己换一页就够了。
//
// 只接站内的、路由认识的地址：
// - 外链（`http(s):`）和带 `target` 的照浏览器的来（新标签页、交给系统浏览器）；
// - 带组合键的点击是「新开一个」的意思，照旧交给浏览器；
// - 路由不认识的站内地址（文档站挂在 `/docs/…`，那不是应用的一条路由）不接，照原样
//   交给浏览器，由服务端把那页给出来。
//
// 同一套做法原来只在「问芝士」的回答里（AssistantPanel 的 followLink），正文别处没有，
// 于是别处点了就整页重载。抽到这里，画正文的那几处共用一份。
import type { Router } from 'vue-router'

import { onBeforeUnmount, type Ref, watch } from 'vue'

import { useNavigation } from './useNavigation'

/** 这次点击该由应用换页时，给出要去的站内地址；否则 null（交给浏览器）。 */
export function inAppHrefFor(link: HTMLAnchorElement | null, router: Router | null | undefined): string | null {
  if (!link || link.hasAttribute('target') || !router) return null
  const href = link.getAttribute('href') ?? ''
  if (!href.startsWith('/') || href.startsWith('//')) return null
  let matched
  try {
    matched = router.resolve(href).matched
  } catch {
    return null // 解析不了（主机名不合法这类）：交给浏览器。
  }
  // 路由没收下这条地址时它落到 404 那条通配（`/:pathMatch(.*)*`）：那不是应用里的一页，
  // 别把读者送进 404，让浏览器照原地址去问服务端。
  if (!matched.length || matched.some((route) => route.name === 'NotFound')) return null
  return href
}

/** 一次鼠标左键点击里的站内链接。带组合键、非左键、非链接的点击都返回 null。 */
export function clickedInAppHref(event: MouseEvent, router: Router | null | undefined): string | null {
  if (event.defaultPrevented || event.button !== 0) return null
  if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return null
  const link = (event.target as Element | null)?.closest?.('a[href]') as HTMLAnchorElement | null
  return inAppHrefFor(link, router)
}

/** 把 `host` 里的站内链接接上应用内换页。画正文的那几处挂这一个就够。 */
export function useInAppLinks(host: Ref<HTMLElement | null>) {
  const router = useNavigation()?.router ?? null

  function follow(event: MouseEvent): void {
    const href = clickedInAppHref(event, router)
    if (!href || !router) return
    event.preventDefault()
    void router.push(href)
  }

  // The host element may be the editor's own DOM, which exists only once the
  // editor does (TipTapViewer's `view.dom`): follow wherever it points.
  let bound: HTMLElement | null = null
  watch(
    host,
    (element) => {
      bound?.removeEventListener('click', follow)
      bound = element
      bound?.addEventListener('click', follow)
    },
    { immediate: true, flush: 'post' }
  )
  onBeforeUnmount(() => bound?.removeEventListener('click', follow))
}
