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

import { installPreviewFetch } from './proto-preview-transport'
import Shell from './proto-shell.vue'

import i18n from '@/i18n'
import { createDialogPlugin } from '@/plugins/dialog'
import vuetify from '@/plugins/vuetify'
import pinia from '@/stores'
import AdminStatsPage from '@/views/admin/AdminStatsPage.vue'

installPreviewFetch()

const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    // 后台的七页统计页，同一个容器按 `kind` 画一类；hash 地址和后台里的地址一致。
    { path: '/', redirect: '/admin/overview' },
    { path: '/admin/overview', component: AdminStatsPage, props: { kind: 'platform' } },
    { path: '/admin/performance', component: AdminStatsPage, props: { kind: 'performance' } },
    { path: '/admin/pipeline', component: AdminStatsPage, props: { kind: 'pipeline' } },
    { path: '/admin/usage', component: AdminStatsPage, props: { kind: 'usage' } },
    { path: '/admin/product', component: AdminStatsPage, props: { kind: 'product' } },
    { path: '/admin/feedback-trends', component: AdminStatsPage, props: { kind: 'feedback' } },
    { path: '/admin/integration-health', component: AdminStatsPage, props: { kind: 'integrations' } },
    // 页面上的下钻出口。没有它们 `router-link` 会当场抛。
    { path: '/admin/queue', component: { template: '<div class="pa-6">队列（预览里是空壳）</div>' } },
    { path: '/topics/:id', component: { template: '<div class="pa-6">话题（预览里是空壳）</div>' } },
    { path: '/feedback/:id', component: { template: '<div class="pa-6">反馈详情（预览里是空壳）</div>' } },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})

createApp(Shell).use(i18n).use(vuetify).use(router).use(pinia).use(createDialogPlugin).mount('#app')
