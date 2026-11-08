// 临时验收入口专用，不进仓库：产品自己的 vite 配置 + 助手对话的后端替身。
import base from './vite.config'

const NOW = '2026-11-08T00:00:00.000Z'
const C1 = { id: 'c1', title: '把报告打分', lastActiveAt: NOW, questions: 1 }
const send = (res: any, body: unknown, status = 200) => {
  res.statusCode = status
  res.setHeader('content-type', 'application/json')
  res.end(JSON.stringify(body))
}

export default {
  ...base,
  server: { ...base.server, hmr: false, host: '127.0.0.1', allowedHosts: true },
  plugins: [
    ...(base.plugins ?? []),
    {
      name: 'accept-9a6b-backend',
      configureServer(server: any) {
        server.middlewares.use((req: any, res: any, next: any) => {
          const url = new URL(req.url, 'http://x')
          const p = url.pathname
          if (p === '/__acc/reset') return send(res, { ok: true })
          if (p === '/' || p === '/accept-9a6b') { req.url = '/accept-9a6b.html'; return next() }
          if (p === '/api/assistant/conversations') return send(res, { code: 200, data: { conversations: [C1], next: null } })
          // 读回来的对话：一句服务端存下的话（没有面板自己的标记），芝士还在答 q1。
          if (p === '/api/assistant/conversations/c1')
            return send(res, { code: 200, data: { ...C1, answering: 'q1', earlier: false, messages: [{ seq: 1, role: 'user', text: '旧的一句', at: NOW }] } })
          // 正在答的那一轮：先写一个字，之后一直不结束。
          if (p === '/api/assistant/conversations/c1/answers/q1') {
            res.setHeader('content-type', 'text/event-stream')
            res.write(`event: delta\ndata: ${JSON.stringify({ text: '正在读报告……' })}\n\n`)
            return
          }
          // 插话失败（服务端出错）。
          // 插话失败（服务端出错）：晚 2.5 秒才回，好让刚发的那句先临时显示一会儿。
          if (p === '/api/assistant/conversations/c1/say') {
            setTimeout(() => send(res, { code: 500, message: '服务端出错（替身）' }, 500), 2500)
            return
          }
          if (p.startsWith('/api/')) return send(res, { code: 200, data: null })
          next()
        })
      },
    },
  ],
}
