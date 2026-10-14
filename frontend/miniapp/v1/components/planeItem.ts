// The things drawn on a <c-plane> — <c-point>, <c-curve>, <c-line>, <c-area> —
// are elements that draw nothing themselves: they hold what to draw, and the
// plane they sit in draws them all into one picture with one set of axes.
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
    connectedCallback() {
      this.style.display = 'none'
      this.tell()
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
  const items: Record<string, string[]> = {
    'c-point': ['value', 'drag', 'snap', 'on', 'from', 'tone', 'label'],
    'c-curve': ['f', 'from', 'to', 'tone', 'dashed', 'label'],
    'c-line': ['x', 'y', 'from', 'to', 'tone', 'dashed', 'label'],
    'c-area': ['points', 'f', 'from', 'to', 'base', 'tone', 'label'],
  }
  for (const [tag, names] of Object.entries(items))
    if (!customElements.get(tag)) customElements.define(tag, planeItem(names))
}
