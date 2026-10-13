// 「有一次跳转正在路上」这件事，做成界面上读得到的一个标记。
//
// 一次首访某个页面的等待有前后两段：先下这一页的懒加载 chunk，chunk 下完才换内容
// （见 routePrefetch.ts 开头）。第一段里屏幕上一个像素都不动——点一下和没点到长得
// 一模一样，人只能再点一次。这里把第二段的开头（跳转已经开始）说出来，让内容区当场
// 给出反馈。
//
// 包的是 `router.push` / `router.replace` 本身，不是拿 `beforeEach` / `afterEach` 数：
//
// - 要**按下去那一帧**就记上。库里那几道全局守卫里有异步的，而懒加载 chunk 是在守卫
//   跑完之后才开始下的；包在 `push` 外面则一出这个函数就记，不排在任何人的后面。
// - 配对也是准的。一次 `push` 的 promise 落地（成功、被守卫拦下、被下一次跳转顶掉，
//   都算）就减回去。`afterEach` 不行：原地重复跳转（点当前已经在的那一页）根本不会
//   走 `beforeEach`，却照样触发一次 `afterEach`，拿它配对迟早把计数数歪。
//
// `go` / `back` / `forward` 不管：那条路上的目标是这一会话里已经渲染过的一页，它的
// chunk 早就在模块缓存里，剩下要等的是数据——那是页面自己的加载态该管的事。
import type { Router } from 'vue-router'

import { computed, type Ref, ref } from 'vue'

/** 露给界面看的那一面：一个布尔量，和把它从 router 上摘下来的手。 */
export interface NavigationProgress {
  /** 有没有一次跳转还没落地。 */
  navigating: Readonly<Ref<boolean>>
  /** 停止计数并把 router 还原——测试里每个用例各用各的 router，用完各自收摊。 */
  dispose: () => void
}

/** 一台 router 只装一次：装两遍计数会翻倍，而 `push` 被包过几层没人看得出。 */
const installed = new WeakMap<Router, NavigationProgress>()

/**
 * 开始把 `router` 上的每次跳转记进 `navigating`。同一台 router 重复调用拿到的是同一
 * 份进度（不重复包装）；`dispose()` 之后可以重新装。
 */
export function trackNavigations(router: Router): NavigationProgress {
  const existing = installed.get(router)
  if (existing) return existing

  const inFlight = ref(0)

  /**
   * 一次跳转进出配平。`push` 按约定一定返回 promise，但真出了别的返回值（测试替身、
   * 以后的实现变了）也得当场放掉计数——它要是卡在那儿，界面上会一直转下去。
   */
  function counted(original: Router['push']): Router['push'] {
    return ((to: Parameters<Router['push']>[0]) => {
      inFlight.value++
      const done = () => {
        if (inFlight.value > 0) inFlight.value--
      }
      let result: ReturnType<Router['push']>
      try {
        result = original(to)
      } catch (error) {
        done()
        throw error
      }
      if (result && typeof (result as Promise<unknown>).then === 'function') {
        // 派生出来的这一支只管配平；原件照样返回给调用方，它的 then/catch 原样管用。
        void (result as Promise<unknown>).then(done, done)
      } else {
        done()
      }
      return result
    }) as Router['push']
  }

  // 摘下来的手要还原成**原来那个函数**（不是包过的），这样 dispose 之后重新装也不会
  // 套出两层。
  const push = router.push
  const replace = router.replace
  router.push = counted(push)
  router.replace = counted(replace)

  // 界面上要的只是「有没有」，所以这里由计数派生，不另存一份会跟它分叉的布尔量。
  const progress: NavigationProgress = {
    navigating: computed(() => inFlight.value > 0),
    dispose: () => {
      router.push = push
      router.replace = replace
      inFlight.value = 0
      installed.delete(router)
    },
  }
  installed.set(router, progress)
  return progress
}
