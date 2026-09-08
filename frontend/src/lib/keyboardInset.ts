// 软键盘弹起来之后，输入框在哪。
//
// 手机浏览器弹出键盘时不一定会改变布局视口——`100vh` 甚至 `100dvh` 都还是原来
// 那么高，于是页面底部那一截（底栏、输入框）被键盘盖住，用户看不见自己在往哪儿
// 打字。唯一报告这件事的是 visualViewport：它的高度是**真正看得见**的那块。
//
// 这里把被盖住的高度写成 <html> 上的 `--keyboard-inset`，布局那边减掉它就行
// （见 style.css）。没有键盘、或者浏览器不支持 visualViewport 时它是 0px，
// 所以桌面上这套东西等于不存在。
//
// Chrome 那一侧另有一条不走 JS 的路：index.html 的 viewport 里写了
// `interactive-widget=resizes-content`，布局视口自己就缩了。那时下面算出来的
// 是 0px——两条路加起来只减一次，这正是要拿布局视口而不是别的东西当基准的原因。
const VAR = '--keyboard-inset'

/**
 * 被键盘盖住多少像素。
 *
 * 基准必须是**布局视口**（`document.documentElement.clientHeight`）——CSS 里的
 * `100%` / `100dvh` 量的就是它，我们减的也是它。不能用 `window.innerHeight`：
 * 它在一部分手机浏览器上会跟着键盘一起缩，那时它和 visualViewport 一样高，算出
 * 来永远是 0，页面一动不动——而这恰恰是最需要这段代码的那些浏览器。
 *
 * `offsetTop` 是页面被顶上去的部分（iOS 会滚动布局视口），也算被挡住。
 */
export function hiddenByKeyboard(layoutHeight: number, visualHeight: number, offsetTop: number): number {
  return Math.max(0, Math.round(layoutHeight - visualHeight - offsetTop))
}

export function trackKeyboardInset(): void {
  const vv = window.visualViewport
  if (!vv) return
  const apply = () => {
    const layout = document.documentElement.clientHeight || window.innerHeight
    document.documentElement.style.setProperty(VAR, `${hiddenByKeyboard(layout, vv.height, vv.offsetTop)}px`)
  }
  vv.addEventListener('resize', apply)
  vv.addEventListener('scroll', apply)
  apply()
}
