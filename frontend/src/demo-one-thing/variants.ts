// The design questions still open, each with the variants the demo can show
// side by side. The top bar's 「方案」 menu picks one per question; the choice
// is read from the link first (#v=submit:A,space:1, so a variant can be sent),
// then from this browser's storage, else the default. Switching only changes
// how the screen draws the same state, so it never resets the demo.
import { reactive } from 'vue'

import { storageGetJson, storageSetJson } from '@/lib/browserStorage'

export interface Question {
  key: 'submit' | 'space'
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
      { key: 'D', label: 'D 卡片（现状）', note: '侧栏顶上的题目卡片和「提交」' },
    ],
  },
  {
    key: 'space',
    title: '空间在侧栏的样子',
    options: [
      { key: '1', label: '1 白底描边', note: '白底，课程色只做边框和字' },
      { key: '2', label: '2 圆形分组', note: '圆形，首页下面自成一组，和方形的项目分开' },
      { key: '3', label: '3 现状', note: '课程色方块加学士帽' },
    ],
  },
]

export type SubmitVariant = 'A' | 'B' | 'C' | 'D'
export type SpaceVariant = '1' | '2' | '3'

const DEFAULTS = { submit: 'A' as SubmitVariant, space: '1' as SpaceVariant }
const KEY = 'oneThingDemo.variants'

const valid = (q: Question['key'], v: unknown) =>
  typeof v === 'string' && !!QUESTIONS.find((x) => x.key === q)?.options.some((o) => o.key === v)

/** #v=submit:A,space:1 → { submit: 'A', space: '1' }; anything it does not name is left out. */
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

/** The link that shows the variants on screen now. */
export const variantHash = () => `#v=submit:${V.submit},space:${V.space}`

export function setVariant(q: Question['key'], v: string): void {
  if (!valid(q, v)) return
  Object.assign(V, { [q]: v })
  // Storage can be missing (a private window, blocked site data): the choice then lasts until reload.
  storageSetJson(KEY, { submit: V.submit, space: V.space })
  history.replaceState(null, '', variantHash())
}

// A link pasted into the same tab changes only the hash: follow it.
window.addEventListener('hashchange', () => Object.assign(V, fromHash()))
