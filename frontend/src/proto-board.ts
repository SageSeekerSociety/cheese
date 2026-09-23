/**
 * 题目板重设计原型的入口（临时，供话题预览用）。
 *
 * **界面是真的，数据是假的**（同 `proto-dashboard.ts` / `proto-feedback.ts` 的规矩）。
 * 但这一份比前两份更进一步：前两份是把**真页面**挂到假数据上，这一份的页面是新的
 * —— 因为它讲的是流程变更（谁都能发题、自己出的自己审、邀请码进下拉），而真页面
 * 现在长得不是这样。所以这里的假数据不接 fetch，直接由 `proto-board/store.ts`
 * 在内存里管；按钮点下去真的改状态，能看见列表跟着变。
 *
 * 只有路由、主题、Vuetify 是真的 —— 那份主题（plugins/vuetify.ts）就是应用本体在用的，
 * 所以配色、圆角、边框、深浅色和真界面一致。
 */
import '@/styles/content.scss'
import '@/styles/fonts.css'
import './style.css'

import { createApp } from 'vue'
import { createRouter, createWebHashHistory } from 'vue-router'

import BoardShell from './proto-board/BoardShell.vue'
import Analytics from './proto-board/pages/Analytics.vue'
import BoardHome from './proto-board/pages/BoardHome.vue'
import Members from './proto-board/pages/Members.vue'
import Mine from './proto-board/pages/Mine.vue'
import Publish from './proto-board/pages/Publish.vue'
import Review from './proto-board/pages/Review.vue'
import TaskDetail from './proto-board/pages/TaskDetail.vue'
import TaskInsights from './proto-board/pages/TaskInsights.vue'
import { isManager } from './proto-board/store'

import { createDialogPlugin } from '@/plugins/dialog'
import vuetify from '@/plugins/vuetify'

const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/', component: BoardHome },
    { path: '/task/:id', component: TaskDetail },
    { path: '/publish', component: Publish },
    { path: '/mine', component: Mine },
    { path: '/insights/:id', component: TaskInsights },
    // 下面三条只对所有者与管理员开放。**守卫是可变的**：切身份之后原来的页面
    // 可能已经不该看，所以每次导航都重新判一次，而不是在启动时算一次就定下来。
    { path: '/review', component: Review, meta: { managerOnly: true } },
    { path: '/members', component: Members, meta: { managerOnly: true } },
    { path: '/analytics', component: Analytics, meta: { managerOnly: true } },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})

router.beforeEach((to) => {
  // 普通用户手打 /analytics 会被送回首页 —— 这正是重设计要说的那句「普通用户
  // 看不到整板看板」。切回管理员身份后同一个地址又能打开。
  if (to.meta.managerOnly && !isManager.value) return { path: '/' }
  return true
})

createApp(BoardShell).use(vuetify).use(router).use(createDialogPlugin).mount('#app')
