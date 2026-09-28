// 演示页没有路由，但房间里有几个真组件（平台事件行的「去看看」链接）用 router-link。
// 给它们一个什么都不导航的内存路由：链接照样画出来，点了也不离开演示。
import { createMemoryHistory, createRouter } from 'vue-router'

export function demoRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/:any(.*)*', component: { render: () => null } }],
  })
}
