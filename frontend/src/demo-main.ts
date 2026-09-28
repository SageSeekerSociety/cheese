/**
 * 文档动态演示的入口（demo.html，线上地址 /demo/<名字>）。
 *
 * 只装演示页：不起路由、不恢复登录、不连后端。文档把这一页嵌进 iframe，所以它
 * 越轻越好，也不能因为后端没起来就挂 —— 整个应用那条入口（main.ts）两样都做不到。
 * 画面用的是产品自己的组件和主题（plugins/vuetify.ts、style.css），所以和真界面
 * 长得一样。
 */
import '@/styles/content.scss'
import '@/styles/fonts.css'
import './style.css'

import { createApp } from 'vue'

import i18n from '@/i18n'
import vuetify from '@/plugins/vuetify'
import DemoView from '@/views/demo/DemoView.vue'

createApp(DemoView, { path: location.pathname, search: location.search }).use(vuetify).use(i18n).mount('#app')
