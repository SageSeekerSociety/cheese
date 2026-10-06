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
import type {
  HistoryState,
  LocationQuery,
  NavigationFailure,
  RouteLocationMatched,
  RouteLocationRaw,
  RouteParams,
  Router,
} from 'vue-router'

import { computed, getCurrentInstance } from 'vue'

/** 此刻在哪儿的一小片，够组件画「我在哪」用。形状就是 vue-router 自己那两个
 *  类型，不含 Router 的任何实例方法 —— 拿不到路由的树也不会因此少画什么。 */
export interface RouteSnapshot {
  path: string
  name: string | symbol | null | undefined
  params: RouteParams
  query: LocationQuery
  /** 锚点那一小段（`#xxx`），设置页把授权结果从地址里拿掉时要原样留着。 */
  hash: string
  /** 匹配到的记录链，从最外面那层到当前这层。页签条要知道自己「在管理的第几层
   *  下面」（`matched.some(r => r.name === …)`）。 */
  matched: RouteLocationMatched[]
  meta: Record<string, unknown>
}

/** 组件能对宿主的路由做的几件事。宿主没装路由时整个是 `null`。 */
export interface Navigation {
  /** 去某处。宿主没有路由就什么都不做。`replace` 为真时换掉当前这一格，不在身后
   *  留一条几乎一样的地址 —— 设置里换栏、从某一栏回目录都是这一种（见 lib/backOut）。
   *
   *  返回 vue-router 那一支 `push` / `replace` 的 promise：调用方要拿它接住一次被
   *  守卫拦下的跳转（`navigate(to).catch(…)`）时，就是原来 `router.push(to)` 的那一个。
   *  不关心结果的照旧直接调用。 */
  navigate(to: RouteLocationRaw, options?: { replace?: boolean }): Promise<NavigationFailure | void | undefined>
  /** 某处的地址，用来画 `<a href>`（中键新开、右键复制链接、状态栏预览都靠它）。
   *  宿主没有路由，或者这个去处这条路不认识 → `null`，那时画出来的是不可点的东西。 */
  href(to: RouteLocationRaw): string | null
  /** 此刻在哪。宿主没有路由就是 `null`。 */
  readonly route: RouteSnapshot | null
  /** 宿主路由的本体。给**确实要整台 router** 的那几处用：悬停预取要拿它解析出
   *  要下的 chunk（`prefetchOnHover({ router, to })`），话题操作要把「复制链接、
   *  新建任务、归档」整套交给 `commands/topicActions`，返回键要 `resolve` 出父级
   *  地址、`back()` 退一格。这些地方要的不是一小片快照，就别假装快照够用。
   *
   *  宿主没装路由时整个 `Navigation` 是 `null`，所以拿得到 nav 就一定拿得到
   *  router —— 组件仍然按「没有路由就少画一点」来写，一处都不少判。 */
  readonly router: Router
  /** 宿主路由的 history state —— vue-router 每次应用内跳转都会往里写一个 `back`
   *  （上一个地址），贴链接直接打开的第一页上它是 `null`。**不是响应式的**（history
   *  本来就不是）：放在 `computed` 里要随跳转重算，就先读一次 `route`，让那一步重算。 */
  readonly historyState: HistoryState | null
}

/**
 * 宿主的路由，窄成组件需要的那几件事；宿主没装路由就是 `null`。
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
    return {
      path: route.path,
      name: route.name,
      params: route.params,
      query: route.query,
      hash: route.hash,
      matched: route.matched,
      meta: route.meta,
    }
  })

  return {
    navigate(to, options) {
      const r = router as Router
      return options?.replace ? r.replace(to) : r.push(to)
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
    get router() {
      return router as Router
    },
    get historyState() {
      return (router as Router).options.history.state ?? null
    },
  }
}
