import type { Component } from 'vue'

/**
 * What a page tells the layout layer about one panel. The layer reads nothing else
 * about it: it does not know what the panel is for, only how it may be arranged.
 */
export interface LayoutPanel {
  id: string
  /** Tab title. */
  title: string
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
   * Keep the panel's DOM laid out even while its tab is hidden (dockview's
   * `renderer: 'always'`): iframes and terminals reload when they are detached.
   */
  alwaysRender?: boolean
}

/** The part of a panel the dockview adapter and the narrow stack need. */
export type LayoutPanelInfo = Omit<LayoutPanel, 'component' | 'props'>
