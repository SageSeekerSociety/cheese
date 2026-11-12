// The design questions still open, each with the variants the demo can show
// side by side. The top bar's 「方案」 menu picks one per question; the choice
// is read from the link first (#v=question:A, so a variant can be sent),
// then from this browser's storage, else the question's default. Switching
// only changes how the screen draws the same state, so it never resets the
// demo. With no question open the 「方案」 button is not shown.
//
// To ask one: add it to QUESTIONS, then read V[key] where the screen differs.
// 提交入口 was asked before; it is settled on 平时收起 (OriginCard.vue).
// Open now: 工作台放哪 (`bench`). Every 一件事 has a 工作台: its own copy of
// the files, what changed, what was run and what it printed (Workbench.vue).
// W1 opens it in the center column with the 一件事's 对话 left in the right
// panel; W2 keeps the 一件事 in the right panel with a 工作台 tab, widened
// while a person is doing it; W3 opens a started 一件事 as a view of its own
// over channel and panel (工作台 · 对话 · 改动), like opening a document.
import { reactive } from 'vue'

import { storageGetJson, storageSetJson } from '@/lib/browserStorage'

export interface Question {
  key: string
  title: string
  /** The variant shown when neither the link nor storage names one. */
  default: string
  options: { key: string; label: string; note: string }[]
}

export const QUESTIONS: Question[] = [
  {
    key: 'bench',
    title: '工作台放哪',
    default: 'W1',
    options: [
      {
        key: 'W1',
        label: 'W1 中间整块',
        note: '打开一件事的工作台，它占中间一栏：左边是这件事的文件，中间看或改，底下是运行。对话留在右栏，左上角的返回回到频道。',
      },
      {
        key: 'W2',
        label: 'W2 右栏加宽',
        note: '一件事照旧在右栏打开，多一个「工作台」页；人在做的时候右栏加宽，频道留在左边看得见。',
      },
      {
        key: 'W3',
        label: 'W3 一件事全屏',
        note: '开工后的一件事单独占一整页，像打开一个文档：页头是名字、谁在做、到哪了，下面是工作台、对话、改动；返回才回到频道。',
      },
    ],
  },
]

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
