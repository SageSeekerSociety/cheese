// Roving tabindex, the part every keyboard group shares: of a group of items only
// one is in the Tab sequence (tabindex=0) and the arrow keys move between them.
// A tab strip uses it (`rovingTabs.ts`), and so does the chat timeline, whose
// items are whole messages with buttons of their own inside
// (components/chat/composables/useTimelineKeys.ts).
//
// What the timeline needs on top of the tab strip is a way to take an item's own
// controls out of the Tab sequence and put them back. Those controls are drawn by
// many components and by HTML strings (reference chips write tabindex="0" into
// the markup, refChip.ts), so it is done on the DOM: `parkTabStop` sets
// tabindex=-1 and remembers what was there, `restoreTabStop` puts that back.

/** Everything the browser may stop on with Tab, by element or by an explicit tabindex. */
export const TAB_STOP =
  'a[href], area[href], button, input, select, textarea, summary, iframe, audio[controls], video[controls], [contenteditable]:not([contenteditable="false"]), [tabindex]'

/** The attribute holding a parked element's own tabindex ('' when it had none). */
const PARKED = 'data-roving-parked'

/** Only the current item is a Tab stop; the rest stay focusable by script and pointer. */
export function setRoving(items: HTMLElement[], current: HTMLElement | undefined): void {
  for (const item of items) setTabIndex(item, item === current ? '0' : '-1')
}

/** Writes only on a change: the timeline watches these attributes and would wake for nothing. */
function setTabIndex(el: HTMLElement, value: string): void {
  if (el.getAttribute('tabindex') !== value) el.setAttribute('tabindex', value)
}

/**
 * Which item a key moves to, or null when the key is not a move. `wrap` goes round
 * from the last item to the first (a tab strip); without it the ends stop there
 * (a list where the top is where older history loads).
 */
export function rovingStep(
  key: string,
  from: number,
  n: number,
  axis: 'horizontal' | 'vertical',
  wrap: boolean
): number | null {
  const [back, forward] = axis === 'horizontal' ? ['ArrowLeft', 'ArrowRight'] : ['ArrowUp', 'ArrowDown']
  switch (key) {
    case forward:
      return wrap ? (from + 1) % n : Math.min(from + 1, n - 1)
    case back:
      return wrap ? (from - 1 + n) % n : Math.max(from - 1, 0)
    case 'Home':
      return 0
    case 'End':
      return n - 1
    default:
      return null
  }
}

/** Takes `el` out of the Tab sequence, remembering its own tabindex. */
export function parkTabStop(el: HTMLElement): void {
  const own = el.getAttribute('tabindex')
  if (own === '-1') return
  // Already parked, and the component drawing it has since rewritten its tabindex:
  // that new value is the one to give back.
  el.setAttribute(PARKED, own ?? '')
  el.setAttribute('tabindex', '-1')
}

/** Puts a parked element back into the Tab sequence as it was. */
export function restoreTabStop(el: HTMLElement): void {
  const own = el.getAttribute(PARKED)
  if (own === null) return
  el.removeAttribute(PARKED)
  if (own === '') el.removeAttribute('tabindex')
  else setTabIndex(el, own)
}

/** The Tab stops under `root`, parked ones included; an element deliberately at -1 is not one. */
export function tabStopsIn(root: ParentNode): HTMLElement[] {
  return [...root.querySelectorAll<HTMLElement>(TAB_STOP)].filter(
    (el) => el.hasAttribute(PARKED) || el.getAttribute('tabindex') !== '-1'
  )
}

/** Can Tab land here right now: in the sequence, enabled, and drawn. */
export function isTabbable(el: HTMLElement): boolean {
  if (el.getAttribute('tabindex') === '-1' || (el as HTMLButtonElement).disabled) return false
  if (el.closest('[inert], [hidden]')) return false
  // No layout at all (a test DOM) answers nothing; a browser answers for display:none.
  return typeof el.checkVisibility === 'function' ? el.checkVisibility() : true
}
