// 软键盘弹起来之后，输入框在哪。
//
// 手机浏览器弹出键盘时不会改变布局视口——`100vh` 甚至 `100dvh` 都还是原来那么高，
// 于是页面底部那一截（底栏、输入框）被键盘盖住，用户看不见自己在往哪儿打字。
// 唯一报告这件事的是 visualViewport：它的高度是**真正看得见**的那块。
//
// 这里把被盖住的高度写成 <html> 上的 `--keyboard-inset`，布局那边减掉它就行
// （见 style.css）。没有键盘、或者浏览器不支持 visualViewport 时它是 0px，
// 所以桌面上这套东西等于不存在。
const VAR = '--keyboard-inset'

function measure(vv: VisualViewport): string {
  // offsetTop 是页面被顶上去的部分（iOS 会滚动布局视口），也算被挡住。
  const hidden = window.innerHeight - vv.height - vv.offsetTop
  return `${Math.max(0, Math.round(hidden))}px`
}

export function trackKeyboardInset(): void {
  const vv = window.visualViewport
  if (!vv) return
  const apply = () => document.documentElement.style.setProperty(VAR, measure(vv))
  vv.addEventListener('resize', apply)
  vv.addEventListener('scroll', apply)
  apply()
}
