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

import i18n from '@/i18n'
import vuetify from '@/plugins/vuetify'
import DemoCatalog from '@/views/demo/DemoCatalog.vue'
import { demoRouter } from '@/views/demo/demoRouter'
import DemoView from '@/views/demo/DemoView.vue'

// 这一条入口上现在有两页，地址说了算：组件预览站（/demo/catalog…）和动态演示
// （/demo/<名字>）。两页都是「地址当 props 传进去」，只是预览站不看查询串。
const catalog = /^\/demo\/catalog(\/|$)/.test(location.pathname)
// 预览里根路径就是这一页：话题预览把根路径直接喂给演示页，地址栏留的是 `/`。
// 那时按 `/` 去选页会落到 DemoView 上、它不认识这个地址，结果一片空白——所以
// 把 `/` 也算作预览站，并指到默认那条（提问接管输入框）。只在演示入口里有效。
const root = location.pathname === '/' || location.pathname === '/index.html'

createApp(catalog || root ? (DemoCatalog as Component) : DemoView, {
  path: root ? '/demo/catalog/ask-takeover' : location.pathname,
  ...(catalog || root ? {} : { search: location.search }),
})
  .use(vuetify)
  .use(i18n)
  .use(createPinia())
  .use(demoRouter())
  .mount('#app')
