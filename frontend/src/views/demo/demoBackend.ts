// 演示页自己的「后端」：几个会自己取数的真组件（验收卡、看板）照常发请求，这里
// 在 fetch 这一层接住 /api/... 的那几条，拿剧本算出来的数据回答，信封和真后端
// 一样（{code, message, data}）。别的请求一律 404，不出这个页面 —— 演示嵌在
// 文档的 iframe 里，后端在不在都得能看。
//
// 只在演示页里装（DemoView 挂载时），应用本体从来不经过这里。

type Answer = () => unknown

const routes = new Map<string, Answer>()
let installed = false

/** 设一条 GET 路由的答案；answer 为 null 就撤掉它。路径不带 /api、不带查询串。 */
export function answer(path: string, value: Answer | null): void {
  if (value) routes.set(path, value)
  else routes.delete(path)
}

function reply(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })
}

export function installDemoBackend(): void {
  if (installed) return
  installed = true
  const passThrough = window.fetch.bind(window)
  window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
    const path = new URL(url, location.origin).pathname
    if (!path.startsWith('/api/')) return passThrough(input, init)
    const method = (init?.method ?? 'GET').toUpperCase()
    const hit = method === 'GET' ? routes.get(path.slice(4)) : undefined
    if (hit) return reply(200, { code: 200, message: 'ok', data: hit() })
    return reply(404, {
      code: 404,
      message: '演示页没有后端',
      error: { name: 'NotFound', message: '演示页没有后端', retryable: false },
    })
  }
}
