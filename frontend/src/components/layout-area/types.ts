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

/** A mark on a panel's tab: something to look at (`dot`, `count`) or something happening (`pulse`). */
export type LayoutPanelSignal = { kind: 'dot' } | { kind: 'pulse' } | { kind: 'count'; count: number; fresh?: boolean }

/** A button the page puts in the header of the cell whose visible tab is this panel. */
export interface LayoutPanelAction {
  icon: string
  label: string
  run: () => void
}

/**
 * What a page tells the layout layer about one panel. The layer reads nothing else
 * about it: it does not know what the panel is for, only how it may be arranged.
 */
export interface LayoutPanel {
  id: string
  /** Tab title. A bare panel shows no tab and may leave it out. */
  title?: string
  /** Said on hover and to a screen reader instead of the title, when it says more. */
  hint?: string
  /** The panel has nothing to show right now: its title is drawn fainter. It still opens. */
  faint?: boolean
  /** A mark on the tab. While a cell is maximized, a mark on a panel out of sight shows on the restore button. */
  signal?: LayoutPanelSignal
  /** Opened in passing: its title is slanted, and double-clicking the tab keeps it (`keep`). */
  temporary?: boolean
  /** Buttons for the header of the cell while this panel is its visible tab. */
  actions?: LayoutPanelAction[]
  /**
   * Panels of one kind. A panel that appears while the area is open goes to the cell where
   * the other panels of its group are.
   */
  group?: string
  /**
   * Comes and goes with the page (an opened file, an opened task): placed in its group's
   * cell when it appears, never listed under "+", never remembered. Closing its tab asks
   * the page to drop it (`close`).
   */
  transient?: boolean
  /** A transient panel that appears in a cell of its own, beside the cell of this panel. */
  beside?: string
  /** The cell's folded state is the same wherever the panel is placed, remembered under this key. */
  foldKey?: string
  /** What to render. Without it the layer renders the area's `panel` slot with `{ id }`. */
  component?: Component
  /** Props for `component`. */
  props?: Record<string, unknown>
  /** An mdi icon name, shown before the title and on a collapsed strip. */
  icon?: string
  /**
   * Shows a close button on its tab; a closed panel is listed under "+". A panel that
   * cannot be closed is always placed: a saved arrangement without it is not used.
   */
  closable?: boolean
  /**
   * A kind of panel the reader may open any number of: always listed under "+", and never
   * placed itself. Picking it asks the page for a new one (`spawn`), which the page then
   * declares as a transient panel.
   */
  spawns?: boolean
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
  /** The cell shows the panel alone, with no tab strip above it, and takes no dropped tab. */
  bare?: boolean
  /**
   * While it is the only panel in its cell, the cell shows no tab strip: the page around
   * it already says what it is. A tab dropped there brings the strip back, and the panel
   * can be dragged by its tab again.
   */
  stripWhenShared?: boolean
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
