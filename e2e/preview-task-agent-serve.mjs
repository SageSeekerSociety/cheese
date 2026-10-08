// 只把效果图那一页起起来（vite + 假数据夹具），不拍。浏览器在别处（这台机器上
// WSL 里缺浏览器要的系统库），从这里跑会被别的进程连过去。
import { fileURLToPath } from 'node:url'
import { createRequire } from 'node:module'

const require = createRequire(new URL('../frontend/package.json', import.meta.url))
const { createServer: createVite } = require('vite')
const root = fileURLToPath(new URL('../frontend/', import.meta.url))
process.chdir(root)
const PORT = Number(process.env.PORT || 5327)

const fixturePlugin = {
  name: 'preview-task-agent-fixture',
  configureServer(server) {
    server.middlewares.use('/preview-task-agent', (_req, res) => {
      res.setHeader('Content-Type', 'text/html; charset=utf-8')
      res.end(`<!doctype html><html lang="zh"><meta charset="utf-8"><title>任务页页头效果图</title>
<style>
html,body{margin:0;min-height:100%}
body{background:#f6f6f8}
.preview-bar{border-bottom:1px solid rgba(0,0,0,.08);background:#fff}
.preview-note{padding:14px 24px 6px;color:#8a8a95;font:13px/1.6 system-ui,sans-serif}
</style>
<div class="preview-bar"><div id="app-bar-slot"></div></div>
<div class="preview-note">效果图：这一行跑的是任务页页头真组件、真样式，只有数据是编的。</div>
<div id="app"></div>
<script type="module" src="/@fs/${fileURLToPath(new URL('./preview-task-agent-fixture.ts', import.meta.url))}"></script>
</html>`)
    })
  },
}

const vite = await createVite({
  root,
  plugins: [fixturePlugin],
  server: {
    host: '127.0.0.1',
    port: PORT,
    strictPort: true,
    // 夹具在 e2e/ 下，不在前端的根里：不放开这一条，vite 会拿 403 挡掉那个模块。
    fs: { allow: [fileURLToPath(new URL('../', import.meta.url))] },
  },
  logLevel: 'info',
})
await vite.listen()
console.log('fixture ready on http://127.0.0.1:' + PORT + '/preview-task-agent')
