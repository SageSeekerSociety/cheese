// 隔离亲验入口用的 vite 配置（不进仓库）：根地址直接给隔离页。
import { defineConfig, mergeConfig } from 'vite'
import base from './vite.config'
export default defineConfig(async (env) => {
  const resolved = typeof base === 'function' ? await base(env) : await base
  return mergeConfig(resolved, {
    plugins: [{
      name: 'harness-root',
      configureServer(server) {
        server.middlewares.use((req, _res, next) => {
          if (req.url === '/' || req.url?.startsWith('/?')) req.url = '/temp-skills.html' + (req.url.slice(1) || '')
          next()
        })
      },
    }],
  })
})
