/**
 * Scroll positions inside one panel, kept across moves.
 *
 * The layer moves a panel's DOM between hosts (another cell, the narrow stack, the
 * hidden parking spot while its cell is collapsed), and dockview detaches a hidden tab's
 * content. A detached element forgets its scroll offset; the component instance and its
 * inputs survive, the offset does not. So every scroll inside the panel is recorded as it
 * happens, and written back once the panel is attached again.
 */
export interface ScrollKeeper {
  /** Puts recorded offsets back on the elements that are still inside the panel. */
  restore(): void
  dispose(): void
}

export function keepScroll(root: HTMLElement): ScrollKeeper {
  const offsets = new Map<Element, { top: number; left: number }>()
  const onScroll = (event: Event) => {
    const target = event.target
    if (!(target instanceof Element) || !root.contains(target)) return
    // A detached element reads 0; that is the loss being undone, not a scroll.
    if (!target.isConnected || !target.getClientRects().length) return
    offsets.set(target, { top: target.scrollTop, left: target.scrollLeft })
  }
  root.addEventListener('scroll', onScroll, { capture: true, passive: true })
  return {
    restore() {
      for (const [element, offset] of offsets) {
        if (!root.contains(element)) {
          offsets.delete(element)
          continue
        }
        if (element.scrollTop !== offset.top) element.scrollTop = offset.top
        if (element.scrollLeft !== offset.left) element.scrollLeft = offset.left
      }
    },
    dispose() {
      root.removeEventListener('scroll', onScroll, { capture: true })
      offsets.clear()
    },
  }
}
