// 假的现场服务端：只接管 /topics/<task-a|task-b>/transcript，其余请求一律回空。
// 页面上的「正常 / 读失败 / 403」切换改的就是这里的 mode。
import { reactive } from 'vue'

export type Mode = 'ok' | 'fail' | 'forbidden'
export const TASKS = { 'task-a': '任务 A（假数据）', 'task-b': '任务 B（假数据）' } as const
type Tid = keyof typeof TASKS

let seq = 0
function rec(tid: Tid, n: number, text: string, cmd?: string) {
  seq += 1
  const at = new Date(Date.UTC(2026, 10, 7, 8, 0, 0) + seq * 7000).toISOString()
  const parts = cmd
    ? [{ kind: 'tool', name: 'Bash', input: { command: cmd }, preview: cmd }]
    : [{ kind: 'text', text }]
  return {
    id: `${tid}-${n}`,
    conversation_id: tid,
    turn_id: `${tid}-turn`,
    author: 'cheese-demo',
    author_type: 'participant',
    kind: 'event',
    content: '',
    created_at: at,
    refs: [],
    run_record: false,
    meta: { record: { type: 'assistant', message: { content: parts } }, parts, tool: cmd ? 'Bash' : undefined, cursor: `${tid}-c${n}` },
  }
}

const rows: Record<Tid, any[]> = { 'task-a': [], 'task-b': [] }
const aLines = ['读一遍现场缓存的代码', 'git log --oneline -5', '复现：缓存一条，读取抛 offline', 'pnpm exec vitest run PanelSite', '修复：有缓存时读失败不再报错页', 'pnpm exec eslint src/composables']
aLines.forEach((t, i) => rows['task-a'].push(i % 2 ? rec('task-a', i + 1, '', t) : rec('task-a', i + 1, `【A】${t}`)))
const bLines = ['看一下资料库缓存', 'ls frontend/src/stores', '这是任务 B 的现场']
bLines.forEach((t, i) => rows['task-b'].push(i % 2 ? rec('task-b', i + 1, '', t) : rec('task-b', i + 1, `【B】${t}`)))

export const state = reactive({ mode: 'ok' as Mode, log: [] as string[] })

export function writeNewRow(tid: Tid) {
  const n = rows[tid].length + 1
  rows[tid].push(rec(tid, n, `【${tid === 'task-a' ? 'A' : 'B'}】服务端新写入的第 ${n} 条（${new Date().toLocaleTimeString()}）`))
}

function note(line: string) {
  state.log.unshift(`${new Date().toLocaleTimeString()} ${line}`)
  state.log.splice(8)
}

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

const realFetch = window.fetch.bind(window)
window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
  const url = new URL(typeof input === 'string' ? input : input instanceof URL ? input.href : input.url, location.href)
  if (!url.pathname.startsWith('/api/')) return realFetch(input, init)
  const m = url.pathname.match(/\/topics\/(task-[ab])\/transcript$/)
  if (/\/agent\/control$/.test(url.pathname)) return json(200, { code: 200, data: { id: null, connected: false, seats: [] } })
  if (!m) { console.warn('SB-UNHANDLED', url.pathname + url.search); return json(200, { code: 200, data: null }) }
  const tid = m[1] as Tid
  const label = tid === 'task-a' ? 'A' : 'B'
  if (state.mode === 'fail') {
    note(`读 ${label} 的现场 → 读失败（模拟断网）`)
    throw new TypeError('Failed to fetch')
  }
  if (state.mode === 'forbidden') {
    note(`读 ${label} 的现场 → 403`)
    return json(403, { code: 403, message: '你没有权限查看这段现场（模拟 403）' })
  }
  const after = url.searchParams.get('after')
  const before = url.searchParams.get('before')
  const all = rows[tid]
  let data = all
  if (after) data = all.slice(all.findIndex((r) => r.meta.cursor === after) + 1)
  if (before) data = []
  const last = all[all.length - 1]
  note(`读 ${label} 的现场 → 正常，${data.length} 条${after ? '新记录' : ''}`)
  return json(200, {
    code: 200,
    data: { data, has_more: false, oldest_id: before ? null : all[0]?.meta.cursor ?? null, newest_id: last?.meta.cursor ?? null },
  })
}
