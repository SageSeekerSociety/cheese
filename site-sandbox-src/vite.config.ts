// 验收台专用：沿用仓库的 vite 配置，只把根路径指到 site-sandbox.html。不进仓库。
import { mergeConfig } from 'vite'

import base from '../vite.config'

export default mergeConfig(base, {
  plugins: [
    {
      name: 'site-sandbox-root',
      configureServer(server: any) {
        server.middlewares.use((req: any, _res: any, next: any) => {
          if (req.url === '/' || req.url === '/index.html' || req.url?.startsWith('/?')) req.url = '/site-sandbox.html'
          next()
        })
      },
    },
  ],
})
