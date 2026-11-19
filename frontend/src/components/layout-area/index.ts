/**
 * The layout layer's public entry. Importing it costs a page nothing of dockview: the
 * wide layout is loaded on demand inside `LayoutArea`.
 */
export type { LayoutArrangement, LayoutCell, LayoutNode, LayoutSplit, SplitDirection } from './arrangement'
export { default as LayoutArea } from './LayoutArea.vue'
export type { LayoutPanel, LayoutPanelAction, LayoutPanelSignal, LayoutPanelWidth } from './types'
export { rememberedFold, rememberedWidth, rememberWidth } from './widths'
