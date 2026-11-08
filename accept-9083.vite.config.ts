// 临时验收入口专用，不进仓库：产品自己的 vite 配置 + 一个现场取数的后端替身。
import base from './vite.config'

const t1 = { s: 1, id: 't1', text: 't1 · 10:00:01 第一条（较早，往后读才带回来）' }
const t2 = { s: 2, id: 't2', text: 't2 · 10:00:02 第二条（较新，socket 先送到）' }
const row = (r: typeof t1) => ({
  id: r.id, conversation_id: 'accept-demo', kind: 'event', author: 'system', author_type: 'system', turn_id: null,
  content: r.text, created_at: `2026-11-07T10:00:0${r.s}Z`, refs: [], run_record: true,
  meta: { event_type: 'message_edited', cursor: String(r.s) },
})
let released = false
const send = (res: any, body: unknown) => { res.setHeader('content-type', 'application/json'); res.end(JSON.stringify(body)) }

export default {
  ...base,
  server: { ...base.server, hmr: false, host: '127.0.0.1', allowedHosts: true },
  plugins: [
    ...(base.plugins ?? []),
    {
      name: 'accept-9083-backend',
      configureServer(server: any) {
        server.middlewares.use((req: any, res: any, next: any) => {
          const url = new URL(req.url, 'http://x')
          if (url.pathname === '/__acc/release') { released = true; return send(res, { ok: true }) }
          if (url.pathname === '/__acc/reset') { released = false; return send(res, { ok: true }) }
          if (url.pathname === '/accept-9083' || url.pathname === '/') { req.url = '/accept-9083.html'; return next() }
          if (url.pathname.startsWith('/api/topics/accept-demo/transcript')) {
            const after = url.searchParams.get('after')
            // 整页（打开时）：空页，带游标 0。往后读（after=0）：放出前空，放出后 [t1, t2]。
            if (after === null) return send(res, { code: 200, data: { data: [], has_more: false, oldest_id: null, newest_id: '0' } })
            const rows = released ? [row(t1), row(t2)].filter((r) => Number(r.meta.cursor) > Number(after)) : []
            return send(res, { code: 200, data: { data: rows, has_more: false, oldest_id: null, newest_id: rows.length ? rows.at(-1)!.meta.cursor : after } })
          }
          if (url.pathname === '/api/topics/accept-demo/agent/control') return send(res, { code: 200, data: { id: null, connected: false, seats: [], controls: [], tasks: {} } })
          if (url.pathname.startsWith('/api/')) return send(res, { code: 200, data: null })
          next()
        })
      },
    },
  ],
}
