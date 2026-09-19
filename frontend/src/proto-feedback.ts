/**
 * 反馈原型的精简入口（临时，供预览用）。
 *
 * 为什么不是 main.ts：main.ts 的静态导入图里带着 katex、editorjs-latex、
 * wc-waterfall、zod、prism 等等，整套 app 的入口有 4.7MB。预览要经话题的预览
 * 通道送到人面前，那条通道实测只有 ~140KB/s 且每个响应都是 no-store，4.7MB
 * 的模块图冷加载要 40 秒以上，加载期间页面全白——人看到的就是「预览是白的」。
 *
 * 这里只装反馈原型真正用到的东西：同样的 App.vue 外壳、同样的页面组件、同样的
 * 主题与设计令牌，只是把路由砍到反馈那三条、把与之无关的静态导入去掉。产物是
 * 一个自包含的 HTML，挂在话题预览上直接点。
 */
import '@/styles/content.scss'
import '@/styles/fonts.css'
import './style.css'

import { createApp } from 'vue'
import { createRouter, createWebHashHistory } from 'vue-router'

import Shell from './proto-shell.vue'
import i18n from '@/i18n'
import { createDialogPlugin } from '@/plugins/dialog'
import vuetify from '@/plugins/vuetify'
import pinia from '@/stores'
import feedbackRoutes from '@/router/feedback'

// hash 路由：预览域只按路径找文件，没有 SPA 回退，history 模式下刷新 /feedback
// 会 404。hash 模式下所有页面都在 / 上，深链（#/feedback/<id>）也能直接打开。
const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    ...feedbackRoutes,
    { path: '/', redirect: '/feedback' },
    { path: '/:pathMatch(.*)*', redirect: '/feedback' },
  ],
})

createApp(Shell)
  .use(i18n)
  .use(vuetify)
  .use(router)
  .use(pinia)
  .use(createDialogPlugin)
  .mount('#app')
