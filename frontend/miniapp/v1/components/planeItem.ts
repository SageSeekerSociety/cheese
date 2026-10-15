// The things drawn on a <c-plane> — <c-point>, <c-curve>, <c-line>, <c-area> —
// and in a <c-space> — those four and the solids, surfaces and groups below —
// are elements that draw nothing themselves: they hold what to draw, and the
// plane or space they sit in draws them all into one picture with one set of
// axes. Two are seen in the page: <c-text>, whose content the space places at a
// point, and <c-group>, which only holds others.
//
// They are plain elements, not Vue ones: an app's template hands them a new
// function on every render (`:f="(x) => …"`), and an element that answered each
// of those with a reactive update of its own could keep its app re-rendering.
// Here a property is only stored, and the plane is told once, after the app
// has finished setting them.
export const PLANE_ITEM_CHANGED = 'c-plane-item'

function planeItem(names: string[]): CustomElementConstructor {
  class Item extends HTMLElement {
    static observedAttributes = names
    private held: Record<string, unknown> = {}
    private told = false
    /** Whether the app listens for a click on it, which a space shows by
     *  answering the pointer. */
    clickable = false
    connectedCallback() {
      const tag = this.tagName.toLowerCase()
      if (tag === 'c-group') this.style.display = 'contents'
      else if (tag !== 'c-text') this.style.display = 'none'
      this.tell()
    }
    addEventListener(
      type: string,
      listener: EventListenerOrEventListenerObject | null,
      options?: boolean | AddEventListenerOptions
    ) {
      if (type === 'click' && !this.clickable) {
        this.clickable = true
        this.tell()
      }
      if (listener) super.addEventListener(type, listener, options)
    }
    attributeChangedCallback() {
      this.tell()
    }
    tell() {
      if (this.told) return
      this.told = true
      queueMicrotask(() => {
        this.told = false
        this.dispatchEvent(new Event(PLANE_ITEM_CHANGED, { bubbles: true }))
      })
    }
    read(name: string) {
      return name in this.held ? this.held[name] : this.getAttribute(name) ?? undefined
    }
    write(name: string, value: unknown) {
      if (this.held[name] === value) return
      this.held[name] = value
      this.tell()
    }
  }
  for (const name of names)
    Object.defineProperty(Item.prototype, name, {
      configurable: true,
      get(this: Item) {
        return this.read(name)
      },
      set(this: Item, value: unknown) {
        this.write(name, value)
      },
    })
  return Item
}

export function registerPlaneItems() {
  const solid = ['tone', 'opacity', 'wire', 'label']
  const items: Record<string, string[]> = {
    'c-point': ['value', 'drag', 'snap', 'on', 'from', 'tone', 'label', 'drop'],
    'c-curve': ['f', 'from', 'to', 'points', 'tone', 'dashed', 'label', 'opacity'],
    'c-line': ['x', 'y', 'from', 'to', 'tone', 'dashed', 'label', 'opacity'],
    'c-area': ['points', 'f', 'from', 'to', 'base', 'tone', 'label', 'opacity'],
    'c-surface': ['f', 'x', 'y', 'z', 'u', 'v', 'level', 'color', 'contours', ...solid],
    'c-box': ['from', 'to', ...solid],
    'c-sphere': ['center', 'radius', ...solid],
    'c-cylinder': ['from', 'to', 'radius', ...solid],
    'c-cone': ['from', 'to', 'radius', ...solid],
    'c-mesh': ['vertices', 'faces', 'color', ...solid],
    'c-points': ['points', 'color', 'size', 'tone', 'opacity'],
    'c-field': ['f', 'x', 'y', 'z', 'n', 'tone', 'label'],
    'c-text': ['at', 'tone'],
    'c-group': ['position', 'rotate', 'scale'],
  }
  for (const [tag, names] of Object.entries(items))
    if (!customElements.get(tag)) customElements.define(tag, planeItem(names))
}
