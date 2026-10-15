// The platform's elements an app is written with (references/apps.md teaches
// them): native custom elements, built from Vue files, used the same way from
// any framework or none.
//
// Their tags, attributes and events are shell 1's contract with every app ever
// kept on it. Within shell 1 they only grow: a new element, a new attribute.
// Renaming, removing or changing what one means breaks apps already written, so
// it goes into a shell 2, which old apps never run on.
//
// The conventions every control keeps (emit.ts): the value is on `value`, and
// `input` and `change` both fire whenever it changes, so Vue's v-model works.
import { defineCustomElement } from 'vue'

import { registerBoard } from './components/board'
import Chart from './components/Chart.ce.vue'
import Chip from './components/Chip.ce.vue'
import MathEl from './components/Math.ce.vue'
import NumberEl from './components/Number.ce.vue'
import Plane from './components/Plane.ce.vue'
import { registerPlaneItems } from './components/planeItem'
import Player from './components/Player.ce.vue'
import Row from './components/Row.ce.vue'
import Segmented from './components/Segmented.ce.vue'
import Select from './components/Select.ce.vue'
import Slider from './components/Slider.ce.vue'
import Space from './components/Space.ce.vue'
import Stat from './components/Stat.ce.vue'
import Switch from './components/Switch.ce.vue'

const ALL = {
  'c-slider': Slider,
  'c-select': Select,
  'c-segmented': Segmented,
  'c-number': NumberEl,
  'c-switch': Switch,
  'c-player': Player,
  'c-stat': Stat,
  'c-chip': Chip,
  'c-row': Row,
  'c-chart': Chart,
  'c-math': MathEl,
  'c-plane': Plane,
  'c-space': Space,
}
/** Vue gives an element its properties only once it is in the page. A
 *  framework sets them earlier — React sets every prop on an element it has
 *  just created, before inserting it — and seeing no such property, writes an
 *  attribute instead: an array or an object arrives as a string. So the
 *  properties are there from the start: one set before the element is in the
 *  page is kept on the element, where Vue takes it over when it mounts (after
 *  the attributes, so the property wins, as it should). */
function earlyProps(base: CustomElementConstructor, keys: string[]): CustomElementConstructor {
  const element = class extends base {}
  for (const key of keys) {
    Object.defineProperty(element.prototype, key, {
      configurable: true,
      get() {
        return undefined
      },
      set(value: unknown) {
        Object.defineProperty(this, key, { value, writable: true, enumerable: true, configurable: true })
      },
    })
  }
  return element
}

export function registerComponents() {
  registerBoard()
  registerPlaneItems()
  for (const [name, comp] of Object.entries(ALL)) {
    if (customElements.get(name)) continue
    const keys = Object.keys((comp as { props?: object }).props ?? {})
    customElements.define(
      name,
      earlyProps(defineCustomElement(comp, name === 'c-math' ? { shadowRoot: false } : {}), keys)
    )
  }
}
