// 演示页没有路由，但房间里有几个真组件（平台事件行的「去看看」链接）用 router-link。
// 给它们一个什么都不导航的内存路由：链接照样画出来，点了也不离开演示。
//
// 光有通配路由不够：router-link 写的是 { name: 'workspace-running' }（预览页签上
// 那个「发布网站」），按名字解析的路由在表里找不到就报错。名字补全，落点还是那个
// 空的通配页 —— 链接画得出来，点不动。
import { createMemoryHistory, createRouter } from 'vue-router'

const blank = { render: () => null }

export function demoRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/projects/:projectId/running', name: 'workspace-running', component: blank },
      { path: '/:any(.*)*', component: blank },
    ],
  })
}
