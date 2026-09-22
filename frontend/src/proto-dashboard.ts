/**
 * 看板预览的精简入口（同 `proto-feedback.ts` 的理由：整套 app 4.7MB，预览通道
 * 扛不住；这里只装看板真正用到的东西）。
 *
 * **界面是真的，数据是假的**：预览通道上没有后端，`installPreviewFetch` 在
 * `fetch` 那一层接上假数据（含新加的 pipeline / product / integrations 三块）。
 */
import '@/styles/content.scss'
import '@/styles/fonts.css'
import './style.css'

import { createApp } from 'vue'
import { createRouter, createWebHashHistory } from 'vue-router'

import { installPreviewFetch } from './proto-feedback-fixtures'
import Shell from './proto-shell.vue'

import i18n from '@/i18n'
import { createDialogPlugin } from '@/plugins/dialog'
import vuetify from '@/plugins/vuetify'
import pinia from '@/stores'
import AdminDashboardPage from '@/views/admin/AdminDashboardPage.vue'

installPreviewFetch()

const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/', component: AdminDashboardPage },
    // 页面上的下钻出口。没有它们 `router-link` 会当场抛。
    { path: '/admin/dashboard', component: AdminDashboardPage },
    { path: '/admin/queue', component: { template: '<div class="pa-6">队列（预览里是空壳）</div>' } },
    { path: '/admin', component: AdminDashboardPage },
    { path: '/topics/:id', component: { template: '<div class="pa-6">话题（预览里是空壳）</div>' } },
    { path: '/feedback/:id', component: { template: '<div class="pa-6">反馈详情（预览里是空壳）</div>' } },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})

createApp(Shell).use(i18n).use(vuetify).use(router).use(pinia).use(createDialogPlugin).mount('#app')
