// The design questions still open, each with the variants the demo can show
// side by side. The top bar's 「方案」 menu picks one per question; the choice
// is read from the link first (#v=question:A, so a variant can be sent),
// then from this browser's storage, else the question's default. Switching
// only changes how the screen draws the same state, so it never resets the
// demo. With no question open the 「方案」 button is not shown.
//
// To ask one: add it to QUESTIONS, then read V[key] where the screen differs.
// 提交入口 was the last one asked; it is settled on 平时收起 (OriginCard.vue).
import { reactive } from 'vue'

import { storageGetJson, storageSetJson } from '@/lib/browserStorage'

export interface Question {
  key: string
  title: string
  /** The variant shown when neither the link nor storage names one. */
  default: string
  options: { key: string; label: string; note: string }[]
}

export const QUESTIONS: Question[] = []

const KEY = 'oneThingDemo.variants'

const valid = (q: string, v: unknown) =>
  typeof v === 'string' && !!QUESTIONS.find((x) => x.key === q)?.options.some((o) => o.key === v)

/** #v=q:A → { q: 'A' }; anything it does not name, or no longer asks, is left out. */
function fromHash(): Record<string, string> {
  const m = /(?:^#|&)v=([^&]*)/.exec(location.hash)
  const out: Record<string, string> = {}
  for (const part of decodeURIComponent(m?.[1] ?? '').split(',')) {
    const [q, v] = part.split(':')
    if (q && v && valid(q, v.toUpperCase())) out[q] = v.toUpperCase()
  }
  return out
}

function fromStorage(): Record<string, string> {
  const saved = storageGetJson<Record<string, unknown>>(KEY) ?? {}
  const out: Record<string, string> = {}
  for (const q of QUESTIONS) if (valid(q.key, saved[q.key])) out[q.key] = saved[q.key] as string
  return out
}

const DEFAULTS = Object.fromEntries(QUESTIONS.map((q) => [q.key, q.default]))

export const V = reactive<Record<string, string>>({ ...DEFAULTS, ...fromStorage(), ...fromHash() })

/** The link that shows the variants on screen now. */
export const variantHash = () => `#v=${QUESTIONS.map((q) => `${q.key}:${V[q.key]}`).join(',')}`

export function setVariant(q: string, v: string): void {
  if (!valid(q, v)) return
  V[q] = v
  // Storage can be missing (a private window, blocked site data): the choice then lasts until reload.
  storageSetJson(KEY, Object.fromEntries(QUESTIONS.map((x) => [x.key, V[x.key]])))
  history.replaceState(null, '', variantHash())
}

// A link pasted into the same tab changes only the hash: follow it.
window.addEventListener('hashchange', () => Object.assign(V, fromHash()))
