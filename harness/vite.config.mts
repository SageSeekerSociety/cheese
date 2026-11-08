// 亲验页的 vite 配置：在产品配置之上加一个解析插件。
//  * 把三个 API 模块换成记录器（面板和 composable 照原样 import）；
//  * 改前那三份文件（D 分支头）里的 import 按产品源码树解析，但同名文件优先用改前那份。
import { existsSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import { defineConfig, mergeConfig, type Plugin } from 'vite'

const H = dirname(fileURLToPath(import.meta.url))
const ROOT = resolve(H, '..')
const SRC = resolve(ROOT, 'src')
const BEFORE = resolve(H, 'before/src')
const MOCKS: Record<string, string> = {
  [resolve(SRC, 'api.ts')]: resolve(H, 'mockApi.ts'),
  [resolve(SRC, 'api/machineSkills.ts')]: resolve(H, 'mockMachineSkills.ts'),
  [resolve(SRC, 'api/projectSkills.ts')]: resolve(H, 'mockProjectSkills.ts'),
}

function harness(): Plugin {
  return {
    name: 'harness-ro',
    enforce: 'pre',
    // 预览打开的是根地址：直接落到亲验页，不加载整个产品。
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        if (req.url === '/' || req.url === '/index.html') {
          res.statusCode = 302
          res.setHeader('Location', '/harness-ro.html?side=before&hold=blocked')
          res.end()
          return
        }
        next()
      })
    },
    async resolveId(id, importer, opts) {
      if (!importer || id.startsWith('\0')) return null
      const imp = importer.split('?')[0]
      const fromBefore = imp.startsWith(BEFORE)
      const asImporter = fromBefore ? SRC + imp.slice(BEFORE.length) : imp
      const r = await this.resolve(id, asImporter, { ...opts, skipSelf: true })
      if (!r) return null
      const [p, ...rest] = r.id.split('?')
      const query = rest.length ? `?${rest.join('?')}` : ''
      if (MOCKS[p] && !imp.startsWith(H + '/mock')) return MOCKS[p]
      if (fromBefore && p.startsWith(SRC)) {
        const b = BEFORE + p.slice(SRC.length)
        if (existsSync(b)) return b + query
      }
      return fromBefore ? r : null
    },
  }
}

export default defineConfig(async () => {
  const base = (await import('../vite.config.ts')).default
  const cfg = await (typeof base === 'function' ? base({ command: 'serve', mode: 'development' }) : base)
  return mergeConfig(cfg, {
    plugins: [harness()],
    server: { port: Number(process.env.HARNESS_PORT || 5291), strictPort: true, host: '127.0.0.1', allowedHosts: true },
  })
})
