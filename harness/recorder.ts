// 亲验页的「网络层」：只记下调用、按场景回话。面板和 composable 都是真的。
import { reactive } from 'vue'

export type HoldMode = 'blocked' | 'late' | 'free'
const q = new URLSearchParams(location.search)
export const side: 'before' | 'after' = q.get('side') === 'before' ? 'before' : 'after'
export const holdMode: HoldMode = (['blocked', 'late', 'free'] as const).find((m) => m === q.get('hold')) ?? 'blocked'

export interface Call {
  at: string
  kind: 'machine' | 'list' | 'lease'
  text: string
}
export const calls = reactive<Call[]>([])
export function record(kind: Call['kind'], text: string) {
  calls.push({ at: new Date().toLocaleTimeString('zh-CN', { hour12: false }), kind, text })
}
export const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))

// 「晚到」场景里的两道闸：人点按钮才放行，不靠计时（来得及手点）。
export const gates = reactive<{ lease: (() => void) | null; install: (() => void) | null }>({ lease: null, install: null })
export function gate(name: 'lease' | 'install') {
  return new Promise<void>((resolve) => {
    gates[name] = () => {
      gates[name] = null
      resolve()
    }
  })
}
