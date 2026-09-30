/**
 * 反馈预览的精简入口（临时，供话题预览用）。
 *
 * 为什么不是 main.ts：main.ts 的静态导入图里带着 katex、editorjs-latex、
 * wc-waterfall、zod、prism 等等，整套 app 的入口有 4.7MB。预览要经话题的预览
 * 通道送到人面前，那条通道实测只有 ~140KB/s 且每个响应都是 no-store，4.7MB
 * 的模块图冷加载要 40 秒以上，加载期间页面全白——人看到的就是「预览是白的」。
 *
 * 这里只装反馈真正用到的东西：同样的外壳、同样的页面组件、同样的主题与设计令牌，
 * 只是把路由砍到反馈那五条、把与之无关的静态导入去掉。产物是一个自包含的 HTML，
 * 挂在话题预览上直接点。
 *
 * 与上一轮的差别只有一处：那时候页面本身读内存里的 mock，这一轮页面读真接口，
 * 而预览通道上没有后端，所以这里在 `fetch` 那一层接上假数据（见
 * `proto-feedback-fixtures.ts`）。**界面是真的，数据是假的**；要看真实数据请在本机
 * 起 backend + frontend。
 */
import '@/styles/content.scss'
import '@/styles/fonts.css'
import './style.css'
import './proto-admin-variants.css'

import { createApp } from 'vue'
import { createRouter, createWebHashHistory } from 'vue-router'

import { installPreviewFetch } from './proto-feedback-fixtures'
import Shell from './proto-shell.vue'

import i18n from '@/i18n'
import { createDialogPlugin } from '@/plugins/dialog'
import vuetify from '@/plugins/vuetify'
import feedbackRoutes from '@/router/feedback'
import pinia from '@/stores'

// 挂在 createApp 之前：页面在 `onMounted` 里就发请求，拦截器晚一步装，第一批请求
// 就跑到真网络上去了（在预览域上那是一页 404）。
installPreviewFetch()

// 布局方案预览：`?v=a|b|c` 在 <html> 上挂一个属性，`proto-admin-variants.css` 按它覆盖
// 后台各页的外壳样式。不带参数就是现状。右下角那枚标签说明这是预览、数据是样例。
const variant = new URLSearchParams(location.search).get('v') ?? ''
if (variant) document.documentElement.dataset.adminVariant = variant
const VARIANT_NAME: Record<string, string> = { '': '现状', a: '方案 A · 轻量对齐', b: '方案 B · 统一骨架', c: '方案 C · 卡片工作台' }
const tag = document.createElement('div')
tag.className = 'proto-variant-tag'
tag.textContent = `预览 · ${VARIANT_NAME[variant] ?? variant} · 样例数据`
document.body.appendChild(tag)

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

createApp(Shell).use(i18n).use(vuetify).use(router).use(pinia).use(createDialogPlugin).mount('#app')
