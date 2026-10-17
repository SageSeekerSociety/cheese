// Drag and drop: <c-board> holds <c-zone>s, places that take pieces, and
// <c-piece>s, the things the reader moves. The board's value says which pieces
// are in which zone, in order — `{ zone: [piece, …] }` — and the app draws each
// zone's pieces from it, so a piece is wherever the value says. The reader
// moves one by dragging it (mouse or finger), or by picking it (click, tap, or
// Enter on it) and then the zone (the same again). The board hands the app the
// new value the way every control does (emit.ts), and slides each piece from
// where it was to where it lands.
//
// A zone with `max` full takes a piece by swapping: the one already there goes
// back where the newcomer came from, which is what one-per-slot exercises want.
//
// Pieces and zones are the app's own content and are drawn by it; page.css
// gives them a plain default look, which the app's own classes override.
import { announce } from './emit'
import { duration, still } from './motion'

type Places = Record<string, string[]>

const nameOf = (el: Element | null) => el?.getAttribute('name') ?? ''

class Board extends HTMLElement {
  value: Places = {}
  private picked: HTMLElement | null = null
  private drag: { piece: HTMLElement; ghost: HTMLElement | null; x: number; y: number; dx: number; dy: number } | null =
    null
  private justDragged = false
  private tabs = new MutationObserver(() => this.reachable())

  connectedCallback() {
    this.addEventListener('pointerdown', this.down)
    this.addEventListener('click', this.click)
    this.addEventListener('keydown', this.key)
    this.tabs.observe(this, { childList: true, subtree: true })
    this.reachable()
  }
  disconnectedCallback() {
    this.removeEventListener('pointerdown', this.down)
    this.removeEventListener('click', this.click)
    this.removeEventListener('keydown', this.key)
    this.tabs.disconnect()
    this.drop()
  }

  /** Pieces and zones can be reached and worked from the keyboard. */
  private reachable() {
    for (const el of this.querySelectorAll<HTMLElement>('c-piece, c-zone')) {
      if (!el.hasAttribute('tabindex')) el.tabIndex = 0
      if (el.tagName === 'C-PIECE' && !el.hasAttribute('role')) el.setAttribute('role', 'button')
    }
  }
  private zoneAt(x: number, y: number) {
    const zone = document.elementFromPoint(x, y)?.closest('c-zone') ?? null
    return zone && this.contains(zone) ? (zone as HTMLElement) : null
  }
  private zoneOf(piece: string) {
    return Object.keys(this.value ?? {}).find((z) => this.value[z]?.includes(piece)) ?? null
  }
  /** Where among a zone's pieces a drop at (x, y) goes, in reading order. */
  private indexAt(zone: HTMLElement, x: number, y: number, moving: string) {
    const pieces = [...zone.querySelectorAll<HTMLElement>('c-piece')].filter(
      (p) => p.closest('c-zone') === zone && nameOf(p) !== moving
    )
    const at = pieces.findIndex((p) => {
      const r = p.getBoundingClientRect()
      return y < r.top || (y <= r.bottom && x < r.left + r.width / 2)
    })
    return at < 0 ? pieces.length : at
  }

  /** Move a piece into a zone at a position, and tell the app. */
  private place(piece: string, zone: string, index?: number) {
    const now: Places = Object.fromEntries(Object.entries(this.value ?? {}).map(([k, v]) => [k, [...(v ?? [])]]))
    const from = this.zoneOf(piece)
    const was = from ? now[from].indexOf(piece) : -1
    if (from) now[from].splice(was, 1)
    const into = (now[zone] ??= [])
    const max = Number(this.querySelector(`c-zone[name="${CSS.escape(zone)}"]`)?.getAttribute('max'))
    if (max > 0 && into.length >= max) {
      if (!from || from === zone) return
      const out = into.splice(Math.min(index ?? into.length, into.length - 1), 1)[0]
      now[from].splice(was, 0, out)
    }
    into.splice(Math.min(index ?? into.length, into.length), 0, piece)
    if (JSON.stringify(now) === JSON.stringify(this.value)) return
    const before = this.where()
    announce(this, now)
    this.slide(before)
  }

  private where() {
    return new Map(
      [...this.querySelectorAll<HTMLElement>('c-piece')].map((p) => [nameOf(p), p.getBoundingClientRect()])
    )
  }
  /** Once the app has redrawn, each piece slides in from where it was. */
  private slide(before: Map<string, DOMRect>) {
    if (still()) return
    requestAnimationFrame(() => {
      for (const p of this.querySelectorAll<HTMLElement>('c-piece')) {
        const was = before.get(nameOf(p))
        if (!was) continue
        const r = p.getBoundingClientRect()
        const [dx, dy] = [was.left - r.left, was.top - r.top]
        if (Math.abs(dx) < 1 && Math.abs(dy) < 1) continue
        p.animate([{ transform: `translate(${dx}px, ${dy}px)` }, { transform: 'none' }], {
          duration: duration(),
          easing: 'cubic-bezier(0.4, 0, 0.2, 1)',
        })
      }
    })
  }

  private pick(piece: HTMLElement | null) {
    this.picked?.removeAttribute('data-picked')
    this.picked?.setAttribute('aria-pressed', 'false')
    this.picked = piece
    piece?.setAttribute('data-picked', '')
    piece?.setAttribute('aria-pressed', 'true')
  }
  /** A pick, or the drop of the piece already picked onto what was chosen. */
  private choose(target: Element) {
    const piece = target.closest('c-piece') as HTMLElement | null
    const zone = target.closest('c-zone') as HTMLElement | null
    if (piece && !piece.hasAttribute('fixed') && (!this.picked || piece === this.picked)) {
      this.pick(piece === this.picked ? null : piece)
      return
    }
    if (!this.picked || !zone) {
      if (piece && !piece.hasAttribute('fixed')) this.pick(piece)
      return
    }
    const moving = nameOf(this.picked)
    const index = piece
      ? [...zone.querySelectorAll('c-piece')].filter((p) => nameOf(p) !== moving).indexOf(piece)
      : undefined
    this.pick(null)
    this.place(moving, nameOf(zone), index != null && index >= 0 ? index : undefined)
  }

  private down = (e: PointerEvent) => {
    const piece = (e.target as Element).closest('c-piece') as HTMLElement | null
    if (!piece || !this.contains(piece) || piece.hasAttribute('fixed') || e.button !== 0) return
    const r = piece.getBoundingClientRect()
    this.drag = { piece, ghost: null, x: e.clientX, y: e.clientY, dx: e.clientX - r.left, dy: e.clientY - r.top }
    addEventListener('pointermove', this.move)
    addEventListener('pointerup', this.up)
    addEventListener('pointercancel', this.cancel)
  }
  private move = (e: PointerEvent) => {
    const d = this.drag
    if (!d) return
    if (!d.ghost) {
      if (Math.hypot(e.clientX - d.x, e.clientY - d.y) < 5) return
      const r = d.piece.getBoundingClientRect()
      const ghost = d.piece.cloneNode(true) as HTMLElement
      ghost.removeAttribute('data-picked')
      Object.assign(ghost.style, {
        position: 'fixed',
        left: '0',
        top: '0',
        width: `${r.width}px`,
        height: `${r.height}px`,
        margin: '0',
        pointerEvents: 'none',
        zIndex: '10',
        opacity: '0.92',
      })
      ghost.setAttribute('data-ghost', '')
      document.body.append(ghost)
      d.ghost = ghost
      d.piece.setAttribute('data-dragging', '')
      this.pick(null)
    }
    e.preventDefault()
    d.ghost.style.transform = `translate(${e.clientX - d.dx}px, ${e.clientY - d.dy}px)`
    const over = this.zoneAt(e.clientX, e.clientY)
    for (const z of this.querySelectorAll('c-zone[data-over]')) if (z !== over) z.removeAttribute('data-over')
    over?.setAttribute('data-over', '')
  }
  private up = (e: PointerEvent) => {
    const d = this.drag
    if (d?.ghost) {
      const zone = this.zoneAt(e.clientX, e.clientY)
      const moving = nameOf(d.piece)
      this.justDragged = true
      setTimeout(() => (this.justDragged = false))
      this.drop()
      if (zone) this.place(moving, nameOf(zone), this.indexAt(zone, e.clientX, e.clientY, moving))
      return
    }
    this.drop()
  }
  private cancel = () => this.drop()
  private drop() {
    const d = this.drag
    this.drag = null
    removeEventListener('pointermove', this.move)
    removeEventListener('pointerup', this.up)
    removeEventListener('pointercancel', this.cancel)
    d?.ghost?.remove()
    d?.piece.removeAttribute('data-dragging')
    for (const z of this.querySelectorAll('c-zone[data-over]')) z.removeAttribute('data-over')
  }
  private click = (e: MouseEvent) => {
    if (this.justDragged) return
    this.choose(e.target as Element)
  }
  private key = (e: KeyboardEvent) => {
    if (e.key === 'Escape') return this.pick(null)
    if (e.key !== 'Enter' && e.key !== ' ') return
    const target = e.target as Element
    if (!target.closest('c-piece, c-zone')) return
    e.preventDefault()
    this.choose(target)
  }
}

export function registerBoard() {
  if (!customElements.get('c-board')) customElements.define('c-board', Board)
  if (!customElements.get('c-zone')) customElements.define('c-zone', class extends HTMLElement {})
  if (!customElements.get('c-piece')) customElements.define('c-piece', class extends HTMLElement {})
}
