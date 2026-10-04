// 话题切换和工作面板切页的过渡：View Transitions API，约 160ms 的淡入淡出。
//
// - 只在宽屏（≥960，和 Vuetify 的 mdAndUp 同一条线）做：窄屏上换页已经有 App.vue 的
//   page motion（位移 + 淡入），两套叠在一起会打架。
// - 浏览器不支持、或者用户开了「减弱动效」，直接换，不演。
// - 只演「谁在动」：根节点的快照不做动画（见 style.css），真正淡入淡出的只有挂了
//   `view-transition-name` 的那几块（话题主区、工作面板内容区）。
// - 不碍输入、不动焦点：过渡只在换内容的那一帧拍快照，焦点在哪还在哪；时长压到 160ms，
//   过渡期间的点击最多晚这么一点。
import type { Router } from 'vue-router'

import { nextTick } from 'vue'

type VTDocument = Document & {
  startViewTransition?: (update: () => Promise<void> | void) => { finished: Promise<void> }
}

const WIDE = '(min-width: 960px)'

/** 这一次能不能演：浏览器支持、没开减弱动效、宽屏。 */
export function canViewTransition(): boolean {
  if (typeof document === 'undefined' || typeof window === 'undefined') return false
  if (typeof (document as VTDocument).startViewTransition !== 'function') return false
  if (typeof window.matchMedia !== 'function') return false
  return window.matchMedia(WIDE).matches && !window.matchMedia('(prefers-reduced-motion: reduce)').matches
}

/** 把一次状态改动包进过渡里；不能演就直接改。改动之后等 Vue 把 DOM 画完再拍新快照。 */
export function withViewTransition(update: () => unknown): void {
  if (!canViewTransition()) {
    void update()
    return
  }
  ;(document as VTDocument).startViewTransition!(async () => {
    await update()
    await nextTick()
  })
}

/**
 * 话题之间切换：在路由真正换页之前拍旧快照，换完、DOM 画完再拍新的。
 *
 * 旧快照要在下一帧才拍得到，所以导航会晚一帧（约 16ms）。拍到之后立刻放行；新页面画
 * 完（afterEach 之后的 nextTick）就结束。万一这次导航被别的守卫拦下、afterEach 没来，
 * 300ms 后自己收尾，不让过渡挂着。
 */
export function installTopicTransitions(router: Router): void {
  let finish: (() => void) | null = null
  router.beforeResolve((to, from) => {
    if (to.name !== 'workspace-topic' || from.name !== 'workspace-topic') return
    if (to.params.topicId === from.params.topicId || !canViewTransition()) return
    return new Promise<void>((proceed) => {
      ;(document as VTDocument).startViewTransition!(
        () =>
          new Promise<void>((done) => {
            const timer = setTimeout(() => settle(), 300)
            const settle = () => {
              clearTimeout(timer)
              finish = null
              done()
            }
            finish = settle
            proceed()
          })
      )
    })
  })
  router.afterEach(() => {
    const settle = finish
    if (settle) void nextTick(settle)
  })
}
