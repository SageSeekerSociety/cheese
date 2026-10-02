/**
 * 标注工具栏按容器宽度分三档，外加一个整体收起的档。
 *
 * 面板能窄到 240px，宽到全屏的几百上千像素，一排按钮在两头要的样貌不一样：宽的时候
 * 工具名和颜色轮都摆得下，窄的时候先把文字标签收掉、再收颜色轮，实在窄到放不下就整
 * 条淡出（`inert`，见 `DesignSketchToolbar`）。分档只看宽度，别的一概不看。
 */
export type ToolbarTier = 'full' | 'compact' | 'minimal' | 'concealed'

/** full ≥ 600：工具名 + 颜色轮；compact ≥ 224：收掉文字标签，留颜色轮；
 *  minimal ≥ 150：颜色轮也收掉，只剩图标；再窄就整条 concealed。 */
export const TOOLBAR_FULL_WIDTH = 600
export const TOOLBAR_COMPACT_WIDTH = 224
export const TOOLBAR_MINIMAL_WIDTH = 150

export function toolbarTier(width: number): ToolbarTier {
  // 量不到宽度的时候（隐藏、还没布局）当成最宽的那一档：宁可多画，也不要因为一次
  // 量不到就把工具整条收起来。
  if (!Number.isFinite(width) || width <= 0) return 'full'
  if (width >= TOOLBAR_FULL_WIDTH) return 'full'
  if (width >= TOOLBAR_COMPACT_WIDTH) return 'compact'
  if (width >= TOOLBAR_MINIMAL_WIDTH) return 'minimal'
  return 'concealed'
}
