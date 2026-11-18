import type { Component } from 'vue'

/** A width the cell keeps on its own, in px, instead of sharing the area's width by weight. */
export interface LayoutPanelWidth {
  /** Before the reader has dragged it. */
  initial: number
  min: number
  max: number
  /**
   * Where the dragged width is remembered in the browser. Every area that places this
   * panel under the same key opens it at the same width, whatever arrangement it is in.
   */
  storageKey?: string
}

/**
 * What a page tells the layout layer about one panel. The layer reads nothing else
 * about it: it does not know what the panel is for, only how it may be arranged.
 */
export interface LayoutPanel {
  id: string
  /** Tab title. A bare panel shows no tab and may leave it out. */
  title?: string
  /** What to render. Without it the layer renders the area's `panel` slot with `{ id }`. */
  component?: Component
  /** Props for `component`. */
  props?: Record<string, unknown>
  /** An mdi icon name, shown before the title and on a collapsed strip. */
  icon?: string
  /** Shows a close button on its tab; a closed panel is listed under "+". */
  closable?: boolean
  /** Cannot be closed or dragged out of its cell. Other tabs may still join that cell. */
  pinned?: boolean
  /** A cell holding this panel never gets narrower than this, in px. */
  minWidth?: number
  /**
   * The cell keeps this width when the area or its neighbours change size; only a drag
   * of its own divider changes it. It does not take part in the weights of its split.
   */
  width?: LayoutPanelWidth
  /**
   * The cell is exactly as tall as the panel's content and cannot be resized: a strip
   * such as a page head above other cells. It draws no divider of its own; the content
   * draws its edge.
   */
  fitHeight?: boolean
  /** The cell shows the panel alone, with no tab strip above it. */
  bare?: boolean
  /**
   * Out of sight for now: the cell gives its space to its neighbours and comes back at
   * the size and place it had. The panel keeps its instance meanwhile. A cell is hidden
   * when every panel in it is. Not remembered: the page decides it each time.
   */
  hidden?: boolean
  /**
   * Keep the panel's DOM laid out even while its tab is hidden (dockview's
   * `renderer: 'always'`): iframes and terminals reload when they are detached.
   */
  alwaysRender?: boolean
}

/** The part of a panel the dockview adapter and the narrow stack need. */
export type LayoutPanelInfo = Omit<LayoutPanel, 'component' | 'props'>
