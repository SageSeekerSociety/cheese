// A control tells the page around it what changed the way a native input
// does, with one difference: `input` and `change` both fire whenever the value
// changes (a native `change` waits until the control lets go), because an app
// exists to show the result as the reader moves. The new value is on
// `event.target.value` and on `event.detail`; both events bubble out of the
// element, so Vue's v-model and @input/@change, React's onInput/onChange and
// plain listeners all work.
export function announce(host: HTMLElement & { value?: unknown }, value: unknown) {
  host.value = value
  for (const kind of ['input', 'change'])
    host.dispatchEvent(new CustomEvent(kind, { detail: value, bubbles: true, composed: true }))
}

export type Option = string | number | { value: string | number; label?: string } | [string | number, string]

export function optionsOf(list: unknown): { value: string | number; label: string }[] {
  if (!Array.isArray(list)) return []
  return list.map((o: Option) => {
    if (Array.isArray(o)) return { value: o[0], label: String(o[1] ?? o[0]) }
    if (o && typeof o === 'object') return { value: o.value, label: String(o.label ?? o.value) }
    return { value: o as string | number, label: String(o) }
  })
}
