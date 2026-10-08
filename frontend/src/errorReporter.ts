/**
 * Global frontend error capture → run records (backend: app/domain/frontend_log.py).
 *
 * The dogfooding debugger is usually an agent, and an agent can never read a
 * user's browser console — so runtime errors are reported to the backend, kept
 * as `kind = "frontend_error"` run records and read on the admin 运行记录 page,
 * along with the conversation that was open. They do not enter a room's 现场.
 * console.log stays a local dev convenience; this reporter is the guarantee.
 *
 * Self-contained on purpose: plain fetch (not api.ts's request helper — an
 * error inside the API layer must not recurse into reporting), its own queue
 * and caps, and every path swallows its own failures. The backend dedups and
 * rate-limits again server-side; the client-side cooldown just keeps the
 * network quiet. What it does borrow is the address book (./lib/addresses, read
 * synchronously): the URL says `/projects/<短名>/channels/<编号>`, the intake
 * wants UUIDs.
 */
import type { App } from 'vue'

import { isUuid, routeIds } from './lib/addresses'
import { BASE } from './api'

type WireError = { message: string; stack?: string; source?: string; page?: string }

/** 一条待发项：错误本身，加上报错那一刻所在的项目和频道。 */
type Pending = { projectId: string; topicId: string | null; error: WireError }

const FLUSH_INTERVAL_MS = 10_000
const COOLDOWN_MS = 60_000 // per-fingerprint client-side cooldown
const MAX_BATCH = 10 // matches the backend's batch cap
const MAX_PER_SESSION = 50 // hard stop for a hopelessly broken session

const lastSent = new Map<string, number>()
const queue: Pending[] = []
let sessionCount = 0
let timer: number | null = null

// 浏览器自己发的通告，不是任何人写的代码抛出来的异常。ResizeObserver 那一条说
// 的是「一次回调改了布局，这一轮量不完了」—— 浏览器把剩下的推到下一帧，什么都
// 没丢，页面也没坏。它偏偏是以 window error 的形式发出来的，于是每次都落进运行
// 记录，把真正的报错埋在里面。没有栈、没有文件、没有人能修，就别上报。
const IGNORED = [/^ResizeObserver loop/]

export function isIgnorable(message: string): boolean {
  return IGNORED.some((re) => re.test(message))
}

function fingerprint(e: WireError): string {
  return `${e.message}\n${(e.stack || '').split('\n')[0]}\n${e.source || ''}`
}

// `/projects/<项目>[/channels/<频道>]`，任务页是 `/projects/<项目>/tasks/<任务>`。旧
// 地址的 `topics/` 也认：改名前后发出去的链接都往这里落。项目段和频道段是短名/编号
// 还是 UUID 都收，认出来之后交给地址表换算。
const WORKSPACE_PATH = /^\/projects\/([^/?#]+)(?:\/(?:channels|topics)\/([^/?#]+)|\/tasks\/([^/?#]+))?/

function pathParams(pathname: string): Record<string, string> | null {
  const m = WORKSPACE_PATH.exec(pathname)
  if (!m) return null
  const params: Record<string, string> = { projectId: m[1] }
  if (m[2]) params.topicId = m[2]
  if (m[3]) params.taskId = m[3]
  return params
}

/**
 * 报错时所在的项目和频道。地址上写的是给人看的短名和编号
 * （`/projects/helper/channels/7`，见 lib/addresses.ts），接口要 UUID，所以过一遍
 * 地址表——`routeIds` 就是页面拿 props 用的那条规则。表里还没有的短名/编号会原样
 * 返回，这里就当「不在项目里」：归不进任何地方的报错宁可不报，也不能拿短名冒充
 * UUID 让接口把这批整个 422 掉。
 */
function place(): { projectId: string | null; topicId: string | null } {
  const params = pathParams(location.pathname)
  if (!params) return { projectId: null, topicId: null }
  const ids = routeIds(params)
  const projectId = isUuid(ids.projectId) ? ids.projectId : null
  const topicId = ids.topicId && isUuid(ids.topicId) ? ids.topicId : null
  return { projectId, topicId }
}

export function reportError(message: string, stack?: string, source?: string): void {
  try {
    if (!message || isIgnorable(message) || sessionCount >= MAX_PER_SESSION) return
    // 归属在这里定下来，不在发送时再读一遍路径：队列是模块级的、最多等 10 秒，其间
    // 用户可能已经切走，那时按当前路径算就会把这条错误记到别人的名下。
    const { projectId, topicId } = place()
    if (!projectId) return
    const item: Pending = {
      projectId,
      topicId,
      error: {
        message: String(message).slice(0, 500),
        stack: stack ? String(stack).slice(0, 4000) : undefined,
        source: source ? String(source).slice(0, 300) : undefined,
        page: (location.pathname + location.search).slice(0, 300),
      },
    }
    const fp = fingerprint(item.error)
    const now = Date.now()
    const last = lastSent.get(fp)
    if (last !== undefined && now - last < COOLDOWN_MS) return
    lastSent.set(fp, now)
    sessionCount += 1
    queue.push(item)
    if (queue.length >= MAX_BATCH) {
      void flush()
    } else if (timer === null) {
      timer = window.setTimeout(() => void flush(), FLUSH_INTERVAL_MS)
    }
  } catch {
    // The reporter itself must never throw.
  }
}

/**
 * 队列里的一批，按「哪个项目、哪个频道」分好组。一批可能跨项目和频道——用户在这 10
 * 秒里切了页面——而一条运行记录只写一个对话，所以不能按最后一个地方整批发出去。
 */
function takeBatches(): Pending[][] {
  const groups = new Map<string, Pending[]>()
  for (const item of queue.splice(0, MAX_BATCH)) {
    const key = `${item.projectId}\n${item.topicId ?? ''}`
    const group = groups.get(key)
    if (group) group.push(item)
    else groups.set(key, [item])
  }
  return [...groups.values()]
}

function batchBody(group: Pending[]): string {
  return JSON.stringify({
    project_id: group[0].projectId,
    topic_id: group[0].topicId || undefined,
    errors: group.map((item) => item.error),
  })
}

async function flush(): Promise<void> {
  if (timer !== null) {
    clearTimeout(timer)
    timer = null
  }
  for (const group of takeBatches()) {
    try {
      await fetch(`${BASE}/frontend-errors`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: batchBody(group),
        keepalive: true,
      })
    } catch {
      // Backend unreachable — drop; 现场 reporting is best-effort by design.
    }
  }
}

export function installErrorReporter(app: App): void {
  window.addEventListener('error', (ev: ErrorEvent) => {
    // Resource-load failures dispatch a plain Event (no message) — skip those.
    if (!ev.message) return
    const src = ev.filename ? `${ev.filename}:${ev.lineno}` : undefined
    reportError(ev.message, ev.error instanceof Error ? ev.error.stack : undefined, src)
  })
  window.addEventListener('unhandledrejection', (ev: PromiseRejectionEvent) => {
    const r: unknown = ev.reason
    if (r instanceof Error) {
      reportError(`Unhandled rejection: ${r.message}`, r.stack)
    } else {
      reportError(`Unhandled rejection: ${String(r)}`)
    }
  })
  app.config.errorHandler = (err, _instance, info) => {
    if (err instanceof Error) {
      reportError(err.message, err.stack, `vue:${info}`)
    } else {
      reportError(String(err), undefined, `vue:${info}`)
    }
    // Keep the default devtools visibility — reporting replaces silence,
    // not the console.
    console.error(err)
  }
  window.addEventListener('pagehide', () => {
    for (const group of takeBatches()) {
      try {
        navigator.sendBeacon?.(`${BASE}/frontend-errors`, new Blob([batchBody(group)], { type: 'application/json' }))
      } catch {
        // best-effort
      }
    }
  })
}
