// 公开页预渲染里不碰文件、不碰 Vite 的那几步：把一页渲染好的 HTML 装进 index.html
// 那份模板，以及从 SSR 清单里查出这一页要的样式和代码块。runner 是 prerender.mjs。

/** 预渲染的几页：地址，和它落在 dist 里的文件。nginx.conf 按同一张表把地址指到文件上。 */
export const PAGES = [
  { url: '/', file: 'prerender/index.html' },
  { url: '/about', file: 'prerender/about.html' },
  { url: '/solutions', file: 'prerender/solutions.html' },
  { url: '/download', file: 'prerender/download.html' },
]

/**
 * 这一页用到的模块（`renderToString` 的 `ctx.modules`）各自落在哪几个文件里。SSR 清单
 * （`build.ssrManifest`）的键是相对 frontend/ 的模块路径，值是那个模块所在的 chunk 和
 * 它们的样式。入口那几块已经在模板里，不重复。
 */
export function pageAssets(modules, ssrManifest, template) {
  const css = new Set()
  const js = new Set()
  for (const id of modules) {
    for (const file of ssrManifest[id] ?? []) {
      if (template.includes(`"${file}"`)) continue
      if (file.endsWith('.css')) css.add(file)
      else if (file.endsWith('.js')) js.add(file)
    }
  }
  return { css: [...css], js: [...js] }
}

/**
 * 把一页装进模板：
 *
 * - 渲染出来的 HTML 进 `#app`，客户端在这上面 hydrate（`main.ts`）。
 * - `<meta name="cx-prerendered">` 写在 `<head>` 最前面，启动脚本读它决定画预渲染的这
 *   一页还是骨架（见 index.html）；它必须在那段脚本之前被解析到。
 * - 每页写明自己的规范地址。相对地址会在各主机上解析成那一页，不必把 okcheese.com
 *   和 okcheese.cn 拆成两份构建。
 * - 这一页自己的样式和代码块：样式随后由 beasties 拆成首屏内联的一份和异步加载的其余，
 *   代码块预加载（只在要接管这份 HTML 时），不等入口跑起来再去取。
 * - 主题变量。Vuetify 的主题样式表平时由脚本插进 `<head>`，预渲染的这一页在脚本到之前
 *   就要画出来，所以把两套主题的 `:root` 变量按 `data-theme` 先写进去。
 */
export function assemblePage(template, { url, html, title, css, js, themeCss }) {
  const links = css.map((href) => `<link rel="stylesheet" crossorigin href="${href}">`).join('\n    ')
  // 这一页的代码块只在真要接管这份 HTML 时才预加载：启动脚本认定这一页不给眼前这个人看
  // （登录了、英文）时，那几块用不上，别占他要的那几块的带宽。
  const preload = `<script>
      ;(function () {
        if (document.documentElement.dataset.boot !== 'page') return
        ${JSON.stringify(js)}.forEach(function (href) {
          var link = document.createElement('link')
          link.rel = 'modulepreload'
          link.crossOrigin = ''
          link.href = href
          document.head.appendChild(link)
        })
      })()
    </script>`
  let out = template
    .replace(/<meta charset="UTF-8" \/>/, (m) => `${m}\n    <meta name="cx-prerendered" content="${url}" />`)
    .replace(
      '</head>',
      `    <link rel="canonical" href="${url}">\n    ${links}\n    ${preload}\n    <style id="cx-prerender-theme">${themeCss}</style>\n  </head>`
    )
    .replace('<div id="app"></div>', `<div id="app">${html}</div>`)
  if (title) out = out.replace(/<title>[^<]*<\/title>/, `<title>${escapeHtml(title)}</title>`)
  if (!out.includes('name="cx-prerendered"') || !out.includes(html)) {
    throw new Error(
      `prerender: index.html 的形状变了，${url} 装不进去（找不到 <meta charset> 或 <div id="app"></div>）`
    )
  }
  return out
}

/**
 * Vuetify 的主题样式表只有当前那一套写在 `:root` 上。预渲染的页面在构建时不知道来的人用
 * 哪套，所以亮色的 `:root` 原样留下，暗色的那一份挪到 `:root[data-theme='dark']`
 * ——启动脚本在第一帧之前就把 `data-theme` 写好了。`.v-theme--*` 和 `bg-*` 那些规则两份
 * 一样，留一份。
 */
export function dualThemeCss(lightCss, darkCss) {
  const darkRoots = [...darkCss.matchAll(/:root\s*\{[^}]*\}/g)].map((m) =>
    m[0].replace(':root', ":root[data-theme='dark']")
  )
  return `${lightCss}\n${darkRoots.join('\n')}`.replace(/\s+/g, ' ')
}

/**
 * 服务端渲染的 Vuetify 组件都带着 `v-theme--light`（服务端只能按亮色渲染），这个类会在
 * 组件身上把主题变量钉回亮色，暗色的人在脚本接管之前就会看到一排亮色按钮。去掉它，组件
 * 跟着 `:root` 上的变量走。hydrate 不会把它补回来（Vue 不修 class 的出入），组件下一次
 * 重画时才带上——那时带上的已经是当前那套主题。
 */
export function stripThemeClass(html) {
  return html.replace(/ class="([^"]*)"/g, (_, names) => {
    const kept = names.split(/\s+/).filter((name) => name && name !== 'v-theme--light')
    return kept.length ? ` class="${kept.join(' ')}"` : ''
  })
}

function escapeHtml(text) {
  return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}

/**
 * 冷打开要落到的那一页的代码块，按启动脚本定下的落点（index.html 的 data-boot）分。以前它们
 * 要等应用跑起来、续签完、读完项目清单、路由走到那一页，才开始下：慢网下这一串在入口下完
 * 之后又排了一秒多。现在页面一到就按落点把它们和入口一起下。键是 vite 清单里的源文件（相对
 * frontend/）。`workspace` 在宽屏上落在总览（`overview`），手机上落在项目的列表页。
 */
export const BOOT_ROUTES = {
  workspace: [
    'src/views/workspace/ProjectShell.vue',
    'src/views/workspace/ProjectSidebar.vue',
    'src/views/workspace/WorkspaceEntry.vue',
  ],
  overview: ['src/views/workspace/ProjectOverview.vue'],
  room: [
    'src/views/workspace/ProjectShell.vue',
    'src/views/workspace/ProjectSidebar.vue',
    'src/views/workspace/TopicView.vue',
  ],
  inbox: ['src/layouts/home/Home.vue', 'src/views/home/HomeSidebar.vue', 'src/views/InboxView.vue'],
}

/**
 * 一组源文件要下的代码块和样式：它们自己的块，加上这些块静态 import 的块，一路下去；入口
 * 已经带着的（`template` 里有的）不算。清单里找不到某个源文件就让构建失败：多半是改了名，
 * 不报的话那一页就悄悄回到等应用跑完才下。
 */
export function chunksFor(sources, manifest, ssrManifest, template) {
  const js = new Set()
  const css = new Set()
  const byFile = new Map(Object.entries(manifest).map(([key, entry]) => [`/${entry.file}`, key]))
  // 一个源文件在 vite 清单里有自己的键，只在它自己就是一块的入口时；被别处静态 import 过的
  // （ProjectSidebar 也被 WorkspaceEntry 引着）落在一个共享块里，要从 SSR 清单查它在哪一块。
  const keyOf = (source) =>
    manifest[source] ? source : (ssrManifest[source] ?? []).map((file) => byFile.get(file)).find(Boolean)
  const visit = (key) => {
    const entry = manifest[key]
    if (!entry) throw new Error(`prerender: vite 清单里没有 ${key}`)
    const file = `/${entry.file}`
    if (js.has(file)) return
    if (!template.includes(`"${file}"`)) js.add(file)
    for (const sheet of entry.css ?? []) if (!template.includes(`"/${sheet}"`)) css.add(`/${sheet}`)
    for (const imported of entry.imports ?? []) visit(imported)
  }
  for (const source of sources) {
    const key = keyOf(source)
    if (!key) throw new Error(`prerender: 构建里找不到 ${source}（BOOT_ROUTES 里的文件改名了？）`)
    visit(key)
  }
  return { js: [...js], css: [...css] }
}

/**
 * 按落点预加载的那段脚本，放在 `<head>` 里启动脚本之后。代码块用 modulepreload：应用之后
 * 真去 import 它时 Vite 认得出已经在下了。样式用 preload：下到缓存里，Vite 挂样式表时直接
 * 取；带着 data-boot-preload，挂载前等样式表的那一步（lib/bootReady.ts）不等它们。
 */
export function bootPreloadScript(plan) {
  return `<script>
      ;(function () {
        var plan = ${JSON.stringify(plan)}
        var boot = document.documentElement.dataset.boot
        var picks = [plan[boot]]
        if (boot === 'workspace' && window.innerWidth >= 960) picks.push(plan.overview)
        picks.forEach(function (files) {
          if (!files) return
          files.js.forEach(function (href) {
            var link = document.createElement('link')
            link.rel = 'modulepreload'
            link.crossOrigin = ''
            link.href = href
            document.head.appendChild(link)
          })
          files.css.forEach(function (href) {
            var link = document.createElement('link')
            link.rel = 'preload'
            link.as = 'style'
            link.crossOrigin = ''
            link.href = href
            link.setAttribute('data-boot-preload', '')
            document.head.appendChild(link)
          })
        })
      })()
    </script>`
}

/** 把那段脚本装进模板：紧跟在 data-boot 定下来的那段启动脚本后面。 */
export function withBootPreload(template, script) {
  const marker = 'html.dataset.boot = boot\n      })()\n    </script>\n'
  if (!template.includes(marker)) throw new Error('prerender: index.html 里找不到定落点的启动脚本')
  return template.replace(marker, `${marker}    ${script}\n`)
}

/**
 * 异步加载的样式表前面各补一条高优先级的预加载。beasties 的 `media` 写法把它们挂成
 * `<link rel="stylesheet" media="print" onload="this.media='all'">`：一直是样式表，
 * Vite 给后来加载的代码块补样式时认得出它们已经在页头里（它只认 rel 是 stylesheet 的
 * link），不会再挂一份到最后——挂到最后的那份 Vuetify 会压过页面自己写的同级覆盖，首页
 * 就是这样滚不动的。可打印样式表浏览器排在最后下，前面这条预加载把它提回样式表的优先级，
 * 两条是同一次下载。`crossorigin` 跟着样式表那条：两边不一样，浏览器会各下一遍。
 */
export function withStylePreloads(html) {
  return html.replace(/<link\b[^>]*>/g, (tag) => {
    if (!/\brel="stylesheet"/.test(tag) || !/\bmedia="print"/.test(tag) || !/\bonload=/.test(tag)) return tag
    const href = tag.match(/\bhref="([^"]+)"/)?.[1]
    if (!href) return tag
    const cors = tag.match(/\bcrossorigin(="[^"]*")?/)?.[0]
    return `<link rel="preload" as="style" href="${href}"${cors ? ` ${cors}` : ''}>${tag}`
  })
}
