// Small helpers the block views share.
import type { NodeViewRendererProps } from '@tiptap/core'

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
