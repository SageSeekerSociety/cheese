// 构建时渲染公开页（scripts/prerender.mjs 调它，不进浏览器）。渲染的就是浏览器上那个
// App.vue：公开页上它画的是登录恢复那一层（这时不可见）加 `<router-view>`，客户端在这份
// HTML 上直接 hydrate。App.vue 里只在浏览器里成立的那几件（主题跟随）在这里不跑。
//
// 只装公开页要的路由：首页那一组。别的地址（登录、协议页）只需要算得出链接，由最后那条
// 兜底路由接住，不渲染。路由守卫不跑——守卫看的是登录态和桌面 app，构建时两样都没有，
// 预渲染的就是一位没登录的浏览器访客看到的样子。
import type { RouteRecordRaw } from 'vue-router'

import { createSSRApp } from 'vue'
import { renderToString } from 'vue/server-renderer'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import { createPinia } from 'pinia'

import App from '@/App.vue'
import i18n, { loadLocale, t } from '@/i18n'
import { vuetifyOptions } from '@/plugins/vuetify'
import home from '@/router/home'
import { routeTitle } from '@/router/routeTitle'

const routes: RouteRecordRaw[] = [
  { ...home, children: home.children?.map((child) => ({ ...child, beforeEnter: undefined })) } as RouteRecordRaw,
  { path: '/:rest(.*)*', component: { render: () => null } },
]

/** 一套主题的 Vuetify 样式表（`:root` 上的变量和 `bg-*` 那些类），预渲染页先写进 <head>。 */
export function themeStyles(theme: 'light' | 'dark'): string {
  const vuetify = createVuetify({
    ...vuetifyOptions,
    ssr: true,
    theme: { ...vuetifyOptions.theme, defaultTheme: theme },
  })
  return (vuetify.theme as unknown as { styles: { value: string } }).styles.value
}

export async function render(url: string) {
  // 预渲染的是中文页：服务端没有浏览器语言可跟（Node 的 navigator.language 是 en-US）。
  // 英文访客由启动脚本改走骨架，等应用画出英文（index.html）。
  await loadLocale('zh-CN')
  i18n.global.locale.value = 'zh-CN'
  const router = createRouter({ history: createMemoryHistory(), routes })
  const app = createSSRApp(App)
  app
    .use(i18n)
    .use(createVuetify({ ...vuetifyOptions, ssr: true }))
    .use(router)
    .use(createPinia())
  await router.push(url)
  await router.isReady()
  const ctx: { modules?: Set<string> } = {}
  const html = await renderToString(app, ctx)
  const route = router.currentRoute.value
  // 和 usePageTitle 一致：有自己的整页标题就用它，否则「页名 - 站名」。
  const own = route.meta.documentTitleKey
  const page = routeTitle(route)
  const title = own ? t(own) : page ? `${page} - ${t('global.cheese')}` : null
  return { html, modules: [...(ctx.modules ?? [])], title }
}
