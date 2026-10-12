// happy-dom 自己的开关不在 DOM 的类型里，测试里要用它得先替它说清形状。
//
// `settings.disableIframePageLoading`：打开以后 happy-dom 不再替 `<iframe src=...>` 去
// 抓那一页。组件单测里的框指着一台开发服务器，抓不到就抛一条没人接的错、把测试判红，
// 而那一份要量的是框本身（宽度、缩放、报回来的高度），不是去连服务器。
type HappyDomWindow = Window & {
  happyDOM: { settings: { disableIframePageLoading: boolean } }
}

export function setIframePageLoading(disabled: boolean): void {
  // 这一条路是 `unknown` 转过去的：DOM 的类型里没有 `happyDOM` 这一项，直接断言会被
  // 判成「两边毫不相干」（TS2352）。
  const win = window as unknown as HappyDomWindow
  win.happyDOM.settings.disableIframePageLoading = disabled
}
