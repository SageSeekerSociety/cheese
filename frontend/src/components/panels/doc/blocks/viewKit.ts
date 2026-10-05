// Small helpers the block views share.
import type { NodeViewRendererProps } from '@tiptap/core'

import { reducedMotion } from '@/utils/motion'

export function posOf(getPos: NodeViewRendererProps['getPos']): number | null {
  const pos = typeof getPos === 'function' ? getPos() : undefined
  return typeof pos === 'number' ? pos : null
}

/** A design token's current value, for what draws its own colours (a chart, a diagram). */
export function token(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim()
}

/** Call `redraw` whenever the page switches between the light and the dark theme. */
export function onThemeChange(redraw: () => void): () => void {
  const observer = new MutationObserver(redraw)
  observer.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] })
  return () => observer.disconnect()
}

// ---- Motion for a person's own edit inside a block (design-system §9.2).
// Played from the action that made the change, never from the view being
// built: a view is rebuilt whenever the document is loaded or synced, and an
// item that fades in every time it is rebuilt is decoration.

function ms(name: string): number {
  return Number.parseFloat(token(name)) || 0
}

function still(el: Element | null): el is HTMLElement {
  return !(el instanceof HTMLElement) || reducedMotion() || typeof el.animate !== 'function'
}

/** A new item settles into place. */
export function arrive(el: Element | null): void {
  if (still(el)) return
  el.animate(
    [
      { opacity: 0, transform: 'translateY(-4px)' },
      { opacity: 1, transform: 'none' },
    ],
    {
      duration: ms('--dur-base'),
      easing: token('--ease-out'),
    }
  )
}

/** An item leaves before it is taken out; resolves when it is gone. */
export function depart(el: Element | null): Promise<void> {
  if (still(el)) return Promise.resolve()
  return el
    .animate([{ opacity: 1 }, { opacity: 0 }], {
      duration: ms('--dur-quick'),
      easing: token('--ease-in'),
      fill: 'forwards',
    })
    .finished.then(
      () => undefined,
      () => undefined
    )
}

/** An item that moved slides from where it was (`from`) to where it is now. */
export function slideFrom(el: Element | null, from: DOMRect | undefined): void {
  if (still(el) || !from) return
  const to = el.getBoundingClientRect()
  const dx = from.left - to.left
  const dy = from.top - to.top
  if (!dx && !dy) return
  el.animate([{ transform: `translate(${dx}px, ${dy}px)` }, { transform: 'none' }], {
    duration: ms('--dur-base'),
    easing: token('--ease-standard'),
  })
}
