// 公开页预渲染：`vite build` 之后跑，把 PAGES 里那几页渲染成静态 HTML 放进 dist/prerender/，
// nginx 直接把它们发给浏览器（nginx.conf）。百度的爬虫不跑脚本，以前拿到的是一张空页；
// 没登录的访客以前要等入口那几百 KB 下完、跑完才看得到首页，现在 HTML 一到就画出来，
// 脚本到了再在这份 HTML 上接管（main.ts 里的 hydrate）。
//
// 四步：
//   1. 用 vite.prerender.config.mts 把 src/prerender/entry-server.ts 打成一份 Node 能跑的包。
//   2. 逐页渲染，按 SSR 清单配上这一页的样式和代码块，装进 dist/index.html 那份模板。
//   3. 每份 HTML 里写上按落点预加载第一页代码块的那段脚本（prerender-core.mjs 的 BOOT_ROUTES）。
//   4. beasties 把每份 HTML（连同 dist/index.html）首屏用到的样式内联进去，其余样式表改成
//      不挡首次绘制的异步加载——以前 <head> 里那十几张样式表要全部下完，浏览器才肯画第一帧，
//      慢网下前三秒是白屏。挂载前等样式表到齐的那一步在 main.ts。
//
// 哪一步失败都让构建失败：发出去一份半截的页面，比不预渲染更糟。
import { mkdir, readFile, rm, writeFile } from 'node:fs/promises'
import { dirname, join } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

import Beasties from 'beasties'
import { build } from 'vite'

import {
  assemblePage,
  BOOT_ROUTES,
  bootPreloadScript,
  chunksFor,
  dualThemeCss,
  pageAssets,
  PAGES,
  stripThemeClass,
  withBootPreload,
} from './prerender-core.mjs'

const root = fileURLToPath(new URL('..', import.meta.url))
const dist = join(root, 'dist')
const serverOut = join(root, 'dist-ssr')

await build({ root, configFile: join(root, 'vite.prerender.config.mts'), logLevel: 'warn' })
const server = await import(pathToFileURL(join(serverOut, 'entry-server.js')).href)

const built = await readFile(join(dist, 'index.html'), 'utf8')
const ssrManifest = JSON.parse(await readFile(join(dist, '.vite', 'ssr-manifest.json'), 'utf8'))
const manifest = JSON.parse(await readFile(join(dist, '.vite', 'manifest.json'), 'utf8'))

// 每种落点要下的代码块，写进每一份 HTML（见 prerender-core.mjs 的 BOOT_ROUTES）。
const plan = Object.fromEntries(
  Object.entries(BOOT_ROUTES).map(([boot, sources]) => [boot, chunksFor(sources, manifest, ssrManifest, built)])
)
const template = withBootPreload(built, bootPreloadScript(plan))
const themeCss = dualThemeCss(server.themeStyles('light'), server.themeStyles('dark'))

const beasties = new Beasties({
  path: dist,
  publicPath: '/',
  // 改写成 <link rel="preload" as="style">，下完再换成样式表：不挡首次绘制，也照样按
  // 样式表的高优先级去下。media="print" 那种写法会被浏览器排到最后，应用挂载要等它们，
  // 慢网下登录后的那一页因此晚出来半秒多。
  preload: 'swap',
  // 字体由 index.html 里那条 preload 管（vite.config.ts 的 mdiFont）。
  inlineFonts: false,
  preloadFonts: false,
  // beasties 拿构建时这份 DOM 判断一条规则用不用得上，而有几类规则构建时根本匹配不到：
  // 暗色主题挂在 <html data-theme> 上、桌面 app 挂在 <html class="in-desktop-app"> 上，
  // 两样都是启动脚本在浏览器里才写上的。删了它们，暗色的人第一帧是亮的。所以 index.html
  // 自己写的那几段内联样式一条不删，外部样式表里这两类规则也照样进首屏那一份。
  reduceInlineStyles: false,
  allowRules: [/data-theme/, /in-desktop-app/],
  logLevel: 'warn',
})

const written = []
for (const page of PAGES) {
  const { html, modules, title } = await server.render(page.url)
  if (!html.includes('<main')) throw new Error(`prerender: ${page.url} 渲染出来是空的`)
  // 拿构建出来的那份比（不是加了预加载脚本的）：那段脚本里列着别的落点的文件名。
  const { css, js } = pageAssets(modules, ssrManifest, built)
  const assembled = assemblePage(template, { url: page.url, html: stripThemeClass(html), title, css, js, themeCss })
  const target = join(dist, page.file)
  await mkdir(dirname(target), { recursive: true })
  await writeFile(target, await beasties.process(assembled))
  written.push(page.file)
}

// 应用本体那一份（所有没预渲染的地址都落到它上面）：首屏只有启动骨架，内联的就是骨架
// 和 <html>/<body> 用到的那几条，其余样式表一样改成异步。
await writeFile(join(dist, 'index.html'), await beasties.process(template))

// 两份清单只在构建期用，不随镜像发出去；Node 那份包同理。
await rm(join(dist, '.vite'), { recursive: true, force: true })
await rm(serverOut, { recursive: true, force: true })

console.log(`prerender: ${written.join(', ')}`)
// 入口里 i18n 那次取语言包的计时器、Vuetify 的监听会让进程多挂几秒，渲染完就走。
process.exit(0)
