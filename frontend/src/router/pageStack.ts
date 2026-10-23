// 冷开落在页面栈中间的一页时，把上面那几层垫进浏览器历史。
//
// 手机上的「返回」是系统给的：安卓的导航键、返回手势，走的是浏览器历史，不是顶栏那颗
// ←（那颗读的是路由声明的 `meta.backTo`，见 `ParentBackButton.vue`）。平时两者走得一
// 样，冷打开时分岔：手机把后台的页面收走之后再打开，是从入口地址重新落一次
// （`router/home.ts` 的 `landingForMember`），落点是一次 replace，历史里只有这一条。
// 于是站在任务页上按返回，浏览器身后没有上一层可退，系统就把整个应用关掉——而同一屏
// 上左上角那颗箭头明明能退回频道列表。
//
// 这里把页面栈按声明的层级垫回来：先换到最外面那一层，再一层层推到当前这一页。它跑在
// 首屏定下来之后、应用挂载之前（`main.ts`），那几屏中间页面谁都看不见（启动骨架一直
// 盖着）；用的又全是 router 自己的导航，历史里的位置和前后指针都由它维护，不去碰
// `history.state` 的形状。
//
// 只在两种情况下动手：
// - 手机外壳（窄于 960，和 Vuetify 的 `mdAndUp` 同一条线）。桌面上浏览器的返回本来就
//   该离开这个站，那是浏览器的规矩，不是要修的毛病；页面的「上一层」在桌面上也有
//   顶栏那颗 ← 兜着。
// - 身后没有应用内的来路。应用里跳过来的那一页身后已经有真正的来路，垫上去就把它盖
//   掉了。
import type { RouteLocationNormalizedLoaded, RouteLocationRaw, RouteLocationResolved, Router } from 'vue-router'

/** 桌面的宽度线：Vuetify 的 mdAndUp，和 `lib/viewTransition.ts` 的 WIDE 同一条。 */
const DESKTOP = '(min-width: 960px)'

/** 一条地址声明的上一层。跟着 `meta.backTo` 走到没有声明的那一层为止。 */
function declaredAncestors(router: Router, here: RouteLocationNormalizedLoaded): RouteLocationRaw[] {
  const chain: RouteLocationRaw[] = []
  const seen = new Set<string>()
  let at: RouteLocationNormalizedLoaded = here
  for (;;) {
    const backTo = at.meta.backTo
    if (typeof backTo !== 'string' || seen.has(backTo) || !router.hasRoute(backTo)) break
    seen.add(backTo)
    let parent: RouteLocationResolved
    try {
      // 第二参是 vue-router 的 currentLocation：按名字解析时它用来补目标缺的必填参数
      // （项目编号、频道编号都在这一页的参数里）。
      parent = router.resolve({ name: backTo }, at)
    } catch {
      break
    }
    // 声明成环（或指向自己）时在这里停手，别把无限下一步垫进去。
    if (parent.path === here.path) break
    chain.unshift(parent.fullPath)
    at = parent as unknown as RouteLocationNormalizedLoaded
  }
  return chain
}

/**
 * 冷开落在页面栈的一层里时，把上面那几层垫进浏览器历史，返回键于是和顶栏的 ← 走同
 * 一条路。没有上层可垫（落在底栏三格那一级的根上）、或者身后已经有来路时什么都不做。
 */
export async function ensureStackBehind(router: Router): Promise<void> {
  if (!phoneShell()) return
  const here = router.currentRoute.value
  // 第一屏还没落定（弱网下 `firstPageReady` 到点放行，路由可能仍停在起点）：没有栈可垫。
  if (!here.name) return
  // 身后已经有应用内的来路：那是真的走过来的，不动它。
  if (router.options.history.state?.back) return
  const ancestors = declaredAncestors(router, here)
  if (!ancestors.length) return
  // 哪一步没走成（守卫拦下、网络断了）都不能把人留在半路：能回到原来那一页就回去。
  try {
    await router.replace(ancestors[0])
    for (const step of ancestors.slice(1)) await router.push(step)
    await router.push(here.fullPath)
  } catch {
    if (router.currentRoute.value.fullPath !== here.fullPath) await router.replace(here.fullPath).catch(() => {})
  }
}

/**
 * 现在是不是手机外壳。用媒体查询而不是 `useDisplay()`：这里是路由这一层，没有组件
 * 实例，而这句判断发生在挂载之前。
 */
function phoneShell(): boolean {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return false
  return !window.matchMedia(DESKTOP).matches
}
