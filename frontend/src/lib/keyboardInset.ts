// 软键盘弹起来之后，输入框在哪。
//
// 手机浏览器弹出键盘时**不一定**会改变页面的高度：有的把布局视口一起缩掉（那时
// CSS 里的 `100dvh` 自己就变小了，什么都不用做），有的只缩「看得见的那块」而让
// 页面毫不知情（那时页面底部的输入框就被键盘盖住）。两种行为在同一台手机上还会
// 因为浏览器、版本、是否装成 App 而不同。
//
// 基准取**布局视口本身**：`document.documentElement.clientHeight`。CSS 里的 `100%`
// 量的就是它，所以「视口多高」和「减掉多少」用的是同一个数。
//
//   不取 `window.innerHeight`：Android Chrome 上它历史上会跟着键盘一起缩。那时它
//   和下面 visualHeight 一样高，算出来恒为 0，页面一动不动——而这恰恰是最需要这段
//   代码的时候。（PR #753 记的就是这一次。）
//
//   也不取一根 100dvh 高的探针（#753 当时的做法）：探针量的是「CSS 认为视口多高」，
//   而装成 PWA 之后有「dvh 初次加载就算错、开一次键盘再收起才恢复」的实测报告——
//   拿一个自己可能就是错的数当基准，错了之后没有任何东西能纠正它。
//
// 于是结果写成 <html> 上的两个变量：`--app-height`（布局视口多高，像素）和
// `--keyboard-inset`（被键盘盖住多少），布局那边拿前者减后者（见 style.css）。
const HEIGHT_VAR = '--app-height'
const VAR = '--keyboard-inset'

/**
 * 被键盘盖住多少像素。
 *
 * @param layoutHeight 布局视口高度（`document.documentElement.clientHeight`）
 * @param visualHeight visualViewport 报的、真正看得见的高度
 * @param offsetTop    页面被顶上去的那一截（iOS 会滚动布局视口），也算被挡住
 */
export function hiddenByKeyboard(layoutHeight: number, visualHeight: number, offsetTop: number): number {
  return Math.max(0, Math.round(layoutHeight - visualHeight - offsetTop))
}

export function trackKeyboardInset(): void {
  const root = document.documentElement
  const vv = window.visualViewport

  let queued = false
  const apply = () => {
    queued = false
    // 两个数一次量出来：视口高度和遮挡高度必须出自同一帧，否则中间那一下 resize
    // 会让它们错配，外壳先矮一下再弹回来。
    const layoutHeight = root.clientHeight
    root.style.setProperty(HEIGHT_VAR, `${layoutHeight}px`)
    // 没有 visualViewport 就没有「看得见的那块」可问，只能按 0 算——那种浏览器
    // 键盘归它自己缩布局视口管（`interactive-widget` 那条路）。
    root.style.setProperty(VAR, `${vv ? hiddenByKeyboard(layoutHeight, vv.height, vv.offsetTop) : 0}px`)
  }
  // 一次手势里 resize/scroll 会连着来好几发，合并到下一帧再量一次就够。
  const schedule = () => {
    if (queued) return
    queued = true
    requestAnimationFrame(apply)
  }

  // `window.resize` 必须听：有的浏览器键盘弹出只发这一个，visualViewport 一个事件
  // 都不发——只挂在 vv 上就永远不醒。
  window.addEventListener('resize', schedule)
  window.addEventListener('orientationchange', schedule)
  vv?.addEventListener('resize', schedule)
  vv?.addEventListener('scroll', schedule)
  // 键盘弹出/收起时，有的浏览器先给焦点事件、过一会儿才把 visualViewport 改完，
  // 也有的收起时干脆不发 resize。所以焦点变化时也量，并且过一拍、再过一拍各量一次
  // ——600ms 那一拍是留给「等键盘动画结束才把最终高度报出来」的那种浏览器。
  const onFocusChange = () => {
    schedule()
    setTimeout(apply, 300)
    setTimeout(apply, 600)
  }
  window.addEventListener('focusin', onFocusChange)
  window.addEventListener('focusout', onFocusChange)
  apply()
}
