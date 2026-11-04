// 右栏小浏览器的三档视口：桌面、平板、手机。
//
// 一档不是一个标签，是一份**内容视口宽度**：应用里的 `@media` 按它排版，圈选量出来的
// 坐标也按它算。所以「平板视口」必须真的以 768px 排版，哪怕右栏只有 600px；面板比这一
// 档窄时整块等比缩小到放得下（`fitScale`），而不是把 iframe 压成 600px 再管它叫 768px
// —— 那会让读者以为看的是平板排版，实际看到的是右栏排版。
//
// 桌面这一档没有固定宽度：它就是这块面板自己的宽度（原型里的「桌面」也是这样）。
// 窄到手机上（`PHONE_ONLY_PX` 以下）默认停在手机档：手机上打开一个网页，先看它按手机
// 排版的样子，再看要不要切宽；桌面档在那种宽度下本来就等于手机档，默认它等于什么都没切。

export type PreviewViewport = 'desktop' | 'tablet' | 'mobile'

/** 每一档的内容视口宽度；`null` 表示「跟面板一样宽」。 */
export const VIEWPORT_WIDTH: Record<PreviewViewport, number | null> = {
  desktop: null,
  tablet: 768,
  mobile: 375,
}

/** 面板窄过这一条，默认按手机档打开。 */
export const PHONE_ONLY_PX = 480

/** 这一档在这么宽的面板里按什么比例画：放得下就是 1，放不下就等比缩小。 */
export function fitScale(paneWidth: number, tier: PreviewViewport): number {
  const target = VIEWPORT_WIDTH[tier]
  if (!target || paneWidth <= 0) return 1
  return Math.min(1, paneWidth / target)
}

/** 刚打开（或换了一份文件）时停在哪一档：窄面板按手机，其余按桌面。 */
export function defaultViewport(paneWidth: number): PreviewViewport {
  return paneWidth > 0 && paneWidth < PHONE_ONLY_PX ? 'mobile' : 'desktop'
}
