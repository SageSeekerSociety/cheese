// The small menus the blocks open from their own controls: a callout's kind, a
// timeline item's actions, a new status tag. One at a time, closed by Esc, by
// a click elsewhere, or by picking something.
//
// Plain DOM rather than a Vue component: a node view lives inside the editor,
// outside any component tree, and opens its menu from a click on its own DOM.
// Every button keeps the editor's focus (mousedown is swallowed), so the caret
// is still where it was when the menu closes.

let open: { el: HTMLElement; close: () => void } | null = null

export function closePopover(): void {
  open?.close()
}

export interface PopoverItem {
  label: string
  hint?: string
  pressed?: boolean
  run: () => void
}

/** Open `el` under `anchor`; `onClose` runs however it closes. */
export function popoverAt(anchor: DOMRect, el: HTMLElement, onClose?: () => void): void {
  closePopover()
  el.classList.add('doc-pop')
  el.addEventListener('mousedown', (e) => {
    if (!(e.target as HTMLElement).closest('input')) e.preventDefault()
  })
  document.body.append(el)
  const width = el.offsetWidth
  const height = el.offsetHeight
  const left = Math.max(8, Math.min(anchor.left, window.innerWidth - width - 8))
  const below = anchor.bottom + 4
  const top = below + height > window.innerHeight - 8 ? Math.max(8, anchor.top - height - 4) : below
  el.style.left = `${left + window.scrollX}px`
  el.style.top = `${top + window.scrollY}px`
  // Opened above its anchor: it comes in from below.
  if (top < anchor.top) el.style.setProperty('--doc-menu-from', '4px')
  const away = (e: MouseEvent) => {
    if (!el.contains(e.target as Node)) close()
  }
  const key = (e: KeyboardEvent) => {
    if (e.key === 'Escape') {
      e.preventDefault()
      close()
    }
  }
  const close = () => {
    if (open?.el !== el) return
    open = null
    leave(el)
    document.removeEventListener('mousedown', away, true)
    document.removeEventListener('keydown', key, true)
    onClose?.()
  }
  document.addEventListener('mousedown', away, true)
  document.addEventListener('keydown', key, true)
  open = { el, close }
}

/** Play the popover's way out (docBlocks.css), then take it off the page. */
function leave(el: HTMLElement): void {
  el.classList.add('is-leaving')
  const done = () => el.remove()
  el.addEventListener('transitionend', done, { once: true })
  // No transition ran (reduced motion, a hidden tab): don't leave it behind.
  window.setTimeout(done, 200)
}

/** A menu of actions under `anchor`. */
export function menuAt(anchor: HTMLElement, items: PopoverItem[]): void {
  const el = document.createElement('div')
  el.setAttribute('role', 'menu')
  for (const item of items) {
    const button = document.createElement('button')
    button.type = 'button'
    button.className = item.hint ? 'doc-menu__item doc-menu__item--described' : 'doc-menu__item'
    button.setAttribute('role', item.pressed === undefined ? 'menuitem' : 'menuitemradio')
    if (item.pressed !== undefined) button.setAttribute('aria-checked', String(item.pressed))
    const label = document.createElement('span')
    label.className = 'doc-menu__label'
    label.textContent = item.label
    button.append(label)
    if (item.hint) {
      const hint = document.createElement('span')
      hint.className = 'doc-menu__hint'
      hint.textContent = item.hint
      button.append(hint)
    }
    button.addEventListener('click', () => {
      closePopover()
      item.run()
    })
    el.append(button)
  }
  anchor.setAttribute('aria-expanded', 'true')
  popoverAt(anchor.getBoundingClientRect(), el, () => anchor.setAttribute('aria-expanded', 'false'))
}

/** A button that lives inside a node view: not editable, never takes focus. */
export function controlButton(className: string, label: string, text = ''): HTMLButtonElement {
  const button = document.createElement('button')
  button.type = 'button'
  button.className = className
  button.contentEditable = 'false'
  button.setAttribute('aria-label', label)
  button.title = label
  button.textContent = text
  button.addEventListener('mousedown', (e) => e.preventDefault())
  return button
}
