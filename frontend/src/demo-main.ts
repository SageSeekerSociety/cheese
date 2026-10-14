/**
 * 文档动态演示的入口（demo.html，线上地址 /demo/<名字>）。
 *
 * 只装演示页：不起路由、不恢复登录、不连后端。文档把这一页嵌进 iframe，所以它
 * 越轻越好，也不能因为后端没起来就挂 —— 整个应用那条入口（main.ts）两样都做不到。
 * 画面用的是产品自己的组件和主题（plugins/vuetify.ts、style.css），所以和真界面
 * 长得一样。验收卡这类组件读工作区 store，所以也装一个空的 pinia。
 */
import '@/styles/content.scss'
import '@/styles/fonts.css'
import './style.css'

import type { Component } from 'vue'

import { createApp } from 'vue'
import { createPinia } from 'pinia'

import i18n, { localeReady } from '@/i18n'
import vuetify from '@/plugins/vuetify'
import { applyResolvedTheme } from '@/theme'

// 这一条入口上现在有两页，地址说了算：组件预览站（/demo/catalog…）和动态演示
// （/demo/<名字>）。两页都是「地址当 props 传进去」；查询串两页都收：动态演示按它
// 选演示，预览站按它给 iframe 里的那一格定主题、动效和底色。
const catalog = /^\/demo\/catalog(\/|$)/.test(location.pathname)
// 预览里根路径就是这一页：话题预览把根路径直接喂给演示页，地址栏留的是 `/`。
// 那时按 `/` 去选页会落到 DemoView 上、它不认识这个地址，结果一片空白——所以
// 把 `/` 也算作预览站，并指到默认那条（提问接管输入框）。只在演示入口里有效。
const root = location.pathname === '/' || location.pathname === '/index.html'

// The docs page that embeds a demo says which theme it shows (?theme=). From the
// docs' own host it keeps its preference in its own storage, which this origin
// cannot read; the boot script in demo.html took it already, this tells Vuetify.
const asked = new URLSearchParams(location.search).get('theme')
if (asked === 'dark' || asked === 'light') {
  applyResolvedTheme(asked)
  vuetify.theme.change(asked)
}

// 这一页和真界面共用一份 i18n：浏览器记的是英文时，那份语言包不在入口里，要单独
// 取（见 src/i18n/index.ts 的 `catalogs`）。当场挂载就会先用 zh-CN 兜底画一屏中文、
// 取回来再翻成英文——所以和 main.ts 一样等它落地。取不到也照常挂载，i18n 那边退回
// 入口自带的 zh-CN：这一页是文档里的演示，宁可中文也不能空着。
//
// 这几页自己也得等：其中有些字是模块加载时就取好的（`views/demo/catalogViewAccountFixtures.ts`
// 那一类把 `t(...)` 的结果直接存进了 `export const`），静态引进来就赶在语言包前面
// 了——那时取到的是 zh-CN 的兜底，画面挂上去也不会再变。所以按需引进来，等这一步。
void localeReady.then(async () => {
  const [{ default: DemoCatalog }, { default: DemoView }, { demoRouter }] = await Promise.all([
    import('@/views/demo/DemoCatalog.vue'),
    import('@/views/demo/DemoView.vue'),
    import('@/views/demo/demoRouter'),
  ])
  createApp(catalog || root ? (DemoCatalog as Component) : DemoView, {
    path: root ? '/demo/catalog/ask-takeover' : location.pathname,
    search: location.search,
  })
    .use(vuetify)
    .use(i18n)
    .use(createPinia())
    .use(demoRouter())
    .mount('#app')
})
