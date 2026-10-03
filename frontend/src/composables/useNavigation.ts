// 组件要「跳转」或「读当前路由」时走这里，而不是 import vue-router。
//
// 为什么：`components/**` 里一个 `useRouter()` 就把这颗组件从「给 props 就能渲染」
// 推到「必须先搭一套路由」——单测要多装一个插件，/demo 这类脱离后端的宿主也要跟着
// 装。扇入 48 的 UserRef 就是这么拖累别人的（#2118 把它拆成纯组件 + UserRefLink）。
// 这里是同一套做法的另一半：路由从**应用**上拿 —— `app.use(router)` 会把 `$router`
// 和 `$route` 挂在 `appContext.config.globalProperties` 上 —— 拿不到就当没有：组件
// 照样渲染，只是没有去处、也没有「我在哪」。
//
// 三种用法，按轻到重选：
//
//  1. **只是画一个可点的东西，点了去某处** —— 用 `components/common/NavLink.vue`。
//     它就是这份 composable 的模板那一半，使用方连函数都不用调。
//  2. **要读当前位置**（哪一格是当前页、当前的 spaceId、当前的 query）——
//     `useNavigation()?.route`。拿不到就是 `null`，按「没有当前位置」画。
//  3. **要主动跳**（点了一个菜单项）—— `useNavigation()?.navigate(to)`。
//
// 还有第四条、也是默认答案：**能收 props + emit 的就别用它**。父级知道去向的
// （列表里每行去哪由页面给），组件只该把 `to` 收进来、把点击 emit 出去（UserRef 的
// `navigate`）。这个 composable 是给「没有父级可问」的那一类：一整行的链接、页签条、
// 底部动作面板。
//
// 一处细节：`getCurrentInstance()` 只在 `setup()` 里拿得到实例，所以这个函数要在
// setup 里调（和 `useRouter()` 一样），不要在事件回调里调。
import type { LocationQuery, RouteLocationRaw, RouteParams, Router } from 'vue-router'

import { computed, getCurrentInstance } from 'vue'

/** 此刻在哪儿的一小片，够组件画「我在哪」用。形状就是 vue-router 自己那两个
 *  类型，不含 Router 的任何实例方法 —— 拿不到路由的树也不会因此少画什么。 */
export interface RouteSnapshot {
  path: string
  name: string | symbol | null | undefined
  params: RouteParams
  query: LocationQuery
  meta: Record<string, unknown>
}

/** 组件能对宿主的路由做的三件事。宿主没装路由时整个是 `null`。 */
export interface Navigation {
  /** 去某处。宿主没有路由就什么都不做。`replace` 为真时换掉当前这一格，不在身后
   *  留一条几乎一样的地址 —— 设置里换栏、从某一栏回目录都是这一种（见 lib/backOut）。 */
  navigate(to: RouteLocationRaw, options?: { replace?: boolean }): void
  /** 某处的地址，用来画 `<a href>`（中键新开、右键复制链接、状态栏预览都靠它）。
   *  宿主没有路由，或者这个去处这条路不认识 → `null`，那时画出来的是不可点的东西。 */
  href(to: RouteLocationRaw): string | null
  /** 此刻在哪。宿主没有路由就是 `null`。 */
  readonly route: RouteSnapshot | null
}

/**
 * 宿主的路由，窄成组件需要的那三件事；宿主没装路由就是 `null`。
 *
 * `route` 是实时的：它读的是 `$route` 那个 getter，所以放在 `computed` / 模板里
 * 会跟着路由变（页签的选中态要的就是这个）。
 */
export function useNavigation(): Navigation | null {
  const app = getCurrentInstance()?.appContext.config.globalProperties
  const router = app?.$router
  if (!router) return null

  const here = computed<RouteSnapshot | null>(() => {
    const route = app.$route
    if (!route) return null
    return { path: route.path, name: route.name, params: route.params, query: route.query, meta: route.meta }
  })

  return {
    navigate(to, options) {
      const r = router as Router
      void (options?.replace ? r.replace(to) : r.push(to))
    },
    href(to) {
      // 名字对不上（演示页那张表只有几条）时 `resolve` 会抛，而「画不出一条链接」
      // 不该把整页带下去：返回 null，调用方画成不可点的。
      try {
        return (router as Router).resolve(to).href
      } catch {
        return null
      }
    },
    get route() {
      return here.value
    },
  }
}
