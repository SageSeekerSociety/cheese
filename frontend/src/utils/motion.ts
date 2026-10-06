/**
 * 滚动的行为：系统开了「减弱动效」时用 'auto'（直接到位），否则用 'smooth'。
 *
 * `scrollIntoView({ behavior: 'smooth' })` 和 `scrollTo({ behavior: 'smooth' })` 是
 * 脚本发起的滚动，CSS 那条把时长压到接近 0 的全局兜底够不着它们——浏览器会照旧演
 * 一段平滑滚动。所以每处用到的都在这里问一次，跟着系统的偏好走。
 *
 * 没有 `window.matchMedia`（SSR、老环境、测试里没 stub）时按 'auto' 算：不动比动
 * 错更安全。
 */
export function scrollBehavior(): ScrollBehavior {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return 'auto'
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth'
}

/** 系统开了「减弱动效」。脚本自己画的动画（图表）要问它：CSS 的全局兜底管不到。 */
export function reducedMotion(): boolean {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return false
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches
}
