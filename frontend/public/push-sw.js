/* 浏览器推送的两个处理函数，跑在 service worker 里。
 *
 * 为什么单独一个文件：sw.js 是 VitePWA 的 generateSW 生成的，我们写不进去。它支持
 * `importScripts`（vite.config.ts 的 workbox 配置里列着这个文件），被引入的脚本就
 * 跑在 worker 自己的全局作用域里，所以这里的 `self.addEventListener` 和写在 sw.js
 * 里完全等价 —— 而那一整套缓存策略一行都不用动。
 *
 * 另一条路是换成 injectManifest、自己写整个 sw.js，那要把四条运行时缓存规则连同它
 * 们注释里记着的那些教训（哪些不能缓存、为什么、踩过什么）全部手抄一遍。不值当。
 *
 * 这个文件不经打包，所以它只能用 service worker 环境里本来就有的东西：没有 import，
 * 没有 TypeScript，没有 @/ 别名。
 */

/* global clients */

// 推送里带的那点数据（后端 `push_delivery.drain_push_queue` 拼的）。
// 解不出来也要显示一条：浏览器已经答应过「收到推送必定显示通知」
// （订阅时的 userVisibleOnly: true），一条都不显示的话，它会开始惩罚这个站点 ——
// 先是替我们显示一条「此站点在后台更新」，反复之后直接撤掉推送权限。
function readPayload(event) {
  const fallback = { title: '知是有一条新提示', body: '', url: '/inbox' }
  if (!event.data) return fallback
  try {
    const data = event.data.json()
    const projectId = data.projectId
    const topicId = data.topicId
    return {
      title: data.title || fallback.title,
      body: data.body || '',
      // 点开就落到那件事本身，而不是首页 —— 推送的作用是把人带回现场。两个 id
      // 缺一个就退回待办列表：那里一定列着这件事。
      url: projectId && topicId ? `/projects/${projectId}/topics/${topicId}` : '/inbox',
    }
  } catch {
    return fallback
  }
}

self.addEventListener('push', (event) => {
  const payload = readPayload(event)
  event.waitUntil(
    self.registration.showNotification(payload.title, {
      body: payload.body,
      icon: '/pwa-192x192.png',
      badge: '/pwa-192x192.png',
      // 同一个房间的后一条盖掉前一条：平台在一个房间里连着说两句时，通知栏里该
      // 留最新那句，而不是攒成一列。
      tag: payload.url,
      renotify: true,
      data: { url: payload.url },
    })
  )
})

self.addEventListener('notificationclick', (event) => {
  event.notification.close()
  const target = (event.notification.data && event.notification.data.url) || '/inbox'
  event.waitUntil(
    (async () => {
      const windows = await clients.matchAll({ type: 'window', includeUncontrolled: true })
      // 已经开着这个站就复用那个标签页：再开一个等于让人自己去关。能不能直接导航
      // 取决于浏览器（focus 一定有，navigate 不一定），所以两步都试。
      for (const client of windows) {
        if (new URL(client.url).origin !== self.location.origin) continue
        await client.focus()
        if (typeof client.navigate === 'function') {
          try {
            await client.navigate(target)
          } catch {
            // 跨源或已卸载的页面会拒绝导航；聚焦已经完成了，就到此为止。
          }
        }
        return
      }
      await clients.openWindow(target)
    })()
  )
})
