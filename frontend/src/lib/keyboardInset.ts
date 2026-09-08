// 软键盘弹起来之后，输入框在哪。
//
// 手机浏览器弹出键盘时**不一定**会改变页面的高度：有的把布局视口一起缩掉（那时
// CSS 里的 `100dvh` 自己就变小了，什么都不用做），有的只缩「看得见的那块」而让
// 页面毫不知情（那时页面底部的输入框就被键盘盖住）。两种行为在同一台手机上还会
// 因为浏览器、版本、是否装成 App 而不同。
//
// 所以这里不去猜浏览器是哪一种，而是**直接量**：页面上放一根 `100dvh` 高的尺子
// （CSS 减的就是这个数），拿它减掉 visualViewport 报的可见高度，差值就是被盖住
// 的那一截。浏览器自己缩了布局视口时，尺子跟着变短，差值自然是 0——同一段代码
// 在两种行为下都得出正确答案，不需要分支，也不会减两次。
//
// 结果写成 <html> 上的 `--keyboard-inset`，布局那边减掉它（见 style.css）。
const VAR = '--keyboard-inset'

/**
 * 被键盘盖住多少像素。
 *
 * @param cssViewportHeight CSS 认为的视口高度（`100dvh` 此刻等于多少）
 * @param visualHeight      visualViewport 报的、真正看得见的高度
 * @param offsetTop         页面被顶上去的那一截（iOS 会滚动布局视口），也算被挡住
 */
export function hiddenByKeyboard(cssViewportHeight: number, visualHeight: number, offsetTop: number): number {
  return Math.max(0, Math.round(cssViewportHeight - visualHeight - offsetTop))
}

/** 那根尺子：不可见、不占位、不挡点击，只用来读 `100dvh` 现在等于多少。 */
function createProbe(): HTMLElement {
  const el = document.createElement('div')
  el.setAttribute('aria-hidden', 'true')
  // 有个名字，在 devtools 里看见它时能认出这是什么，测试也照这个找它。
  el.setAttribute('data-keyboard-probe', '')
  el.style.cssText =
    'position:fixed;top:0;left:0;width:0;height:100dvh;visibility:hidden;pointer-events:none;z-index:-1'
  document.body.appendChild(el)
  return el
}

export function trackKeyboardInset(): void {
  const vv = window.visualViewport
  if (!vv) return
  const probe = createProbe()

  let queued = false
  const apply = () => {
    queued = false
    const inset = hiddenByKeyboard(probe.getBoundingClientRect().height, vv.height, vv.offsetTop)
    document.documentElement.style.setProperty(VAR, `${inset}px`)
  }
  // 一次手势里 resize/scroll 会连着来好几发，合并到下一帧再量一次就够。
  const schedule = () => {
    if (queued) return
    queued = true
    requestAnimationFrame(apply)
  }

  vv.addEventListener('resize', schedule)
  vv.addEventListener('scroll', schedule)
  window.addEventListener('orientationchange', schedule)
  // 键盘弹出/收起时，有的浏览器先给焦点事件、过一会儿才把 visualViewport 改完，
  // 也有的收起时干脆不发 resize。所以焦点变化时也量一次，并且过一拍再量一次。
  window.addEventListener('focusin', () => {
    schedule()
    setTimeout(apply, 300)
  })
  window.addEventListener('focusout', () => {
    schedule()
    setTimeout(apply, 300)
  })
  apply()
}
