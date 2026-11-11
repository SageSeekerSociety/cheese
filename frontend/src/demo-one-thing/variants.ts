// The design questions still open, each with the variants the demo can show
// side by side. The top bar's 「方案」 menu picks one per question; the choice
// is read from the link first (#v=submit:A, so a variant can be sent),
// then from this browser's storage, else the default. Switching only changes
// how the screen draws the same state, so it never resets the demo.
//
// 提交入口's D family (D, D1, D2, D3) all put the 题目's card in the claimed
// project's sidebar (OriginCard.vue); the calmer ones differ in what the card
// leads with and when it is open. Under them the menu also has 「模拟：离截止
// 不到 24 小时」, which moves the clock the deadlines are read against (CLOCK
// below) and nothing else: the tour and every recorded time stay as they are.
import { reactive } from 'vue'

import { storageGetJson, storageSetJson } from '@/lib/browserStorage'

export interface Question {
  key: 'submit'
  title: string
  options: { key: string; label: string; note: string }[]
}

export const QUESTIONS: Question[] = [
  {
    key: 'submit',
    title: '提交入口',
    options: [
      { key: 'A', label: 'A 一行来源', note: '侧栏标题下一行「来自 …」，点开空间里的题目，在那里提交' },
      { key: 'B', label: 'B 顶栏面包屑', note: '中间顶栏一个小标签：课程 › 题目 · 截止 · 状态' },
      { key: 'C', label: 'C 右栏「题目」', note: '项目右栏多一个「题目」页，就地看题、提交' },
      { key: 'D', label: 'D 卡片', note: '侧栏顶上的题目卡片：橙色「提交」、未提交、倒计时，一直亮着' },
      { key: 'D1', label: 'D1 安静卡片', note: '同一张卡片，全灰；最后 24 小时才出现琥珀色倒计时' },
      { key: 'D2', label: 'D2 进度', note: '卡片先说做到哪了：本地运行几个通过、最近一次提交；截止是小灰字' },
      { key: 'D3', label: 'D3 平时收起', note: '平时一行带状态点；点开是 D1 的卡片，快截止或有新批改时自己展开' },
    ],
  },
]

export type SubmitVariant = 'A' | 'B' | 'C' | 'D' | 'D1' | 'D2' | 'D3'

const DEFAULTS = { submit: 'D3' as SubmitVariant }
const KEY = 'oneThingDemo.variants'

const valid = (q: Question['key'], v: unknown) =>
  typeof v === 'string' && !!QUESTIONS.find((x) => x.key === q)?.options.some((o) => o.key === v)

/** #v=submit:A → { submit: 'A' }; anything it does not name, or no longer asks, is left out. */
function fromHash(): Partial<typeof DEFAULTS> {
  const m = /(?:^#|&)v=([^&]*)/.exec(location.hash)
  const out: Record<string, string> = {}
  for (const part of decodeURIComponent(m?.[1] ?? '').split(',')) {
    const [q, v] = part.split(':')
    if (q && v && valid(q as Question['key'], v.toUpperCase())) out[q] = v.toUpperCase()
  }
  return out
}

function fromStorage(): Partial<typeof DEFAULTS> {
  const saved = storageGetJson<Record<string, unknown>>(KEY) ?? {}
  const out: Record<string, string> = {}
  for (const q of QUESTIONS) if (valid(q.key, saved[q.key])) out[q.key] = saved[q.key] as string
  return out
}

export const V = reactive({ ...DEFAULTS, ...fromStorage(), ...fromHash() })

/** The D family: the 题目's card sits in the claimed project's sidebar, and 提交 is made from the project. */
export const onCard = () => V.submit.startsWith('D')

/** Demo-only clock for deadlines: 「模拟：离截止不到 24 小时」 in the 方案 menu. Not stored, not in the link. */
export const CLOCK = reactive({ dueSoon: false })

/** The link that shows the variants on screen now. */
export const variantHash = () => `#v=submit:${V.submit}`

export function setVariant(q: Question['key'], v: string): void {
  if (!valid(q, v)) return
  Object.assign(V, { [q]: v })
  // Storage can be missing (a private window, blocked site data): the choice then lasts until reload.
  storageSetJson(KEY, { submit: V.submit })
  history.replaceState(null, '', variantHash())
}

// A link pasted into the same tab changes only the hash: follow it.
window.addEventListener('hashchange', () => Object.assign(V, fromHash()))
