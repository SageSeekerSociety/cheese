// 预渲染页的拼装（prerender-core.mjs）。跑法：`pnpm run test:ratchet`。
//
// 构建时才跑全套（scripts/prerender.mjs），这里守几条拼装上的规矩：页面本身进 #app，带着
// 让启动脚本认得它的标记；暗色的人第一帧就是暗的；模板的形状变了要让构建失败，而不是悄悄
// 发出一份没有正文的页。
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { test } from 'node:test'
import { fileURLToPath } from 'node:url'

import { assemblePage, dualThemeCss, pageAssets, PAGES, stripThemeClass, withStylePreloads } from './prerender-core.mjs'

const TEMPLATE = readFileSync(join(dirname(fileURLToPath(import.meta.url)), '..', 'index.html'), 'utf8')
const NGINX = readFileSync(join(dirname(fileURLToPath(import.meta.url)), '..', 'nginx.conf'), 'utf8')

const page = {
  url: '/',
  html: '<main><h1>众智成事</h1></main>',
  title: '知是',
  css: ['/assets/a.css'],
  js: ['/assets/a.js'],
  themeCss: '',
}

test('the page goes into #app, marked so the boot script knows which address it was rendered for', () => {
  const out = assemblePage(TEMPLATE, page)
  assert.match(out, /<div id="app"><main><h1>众智成事<\/h1><\/main><\/div>/)
  assert.match(out, /<meta name="cx-prerendered" content="\/" \/>/)
  // The marker has to be parsed before the boot script reads it.
  assert.ok(out.indexOf('name="cx-prerendered"') < out.indexOf('html.dataset.boot = boot'))
})

test('the page carries its own title and stylesheets', () => {
  const out = assemblePage(TEMPLATE, { ...page, title: '方案 - 知是' })
  assert.match(out, /<title>方案 - 知是<\/title>/)
  assert.match(out, /href="\/assets\/a.css"/)
})

test('the page names itself as the canonical URL', () => {
  const out = assemblePage(TEMPLATE, { ...page, url: '/download' })
  assert.match(out, /<link rel="canonical" href="\/download">/)
})

test('a template the page no longer fits into fails the build instead of shipping an empty page', () => {
  assert.throws(() => assemblePage(TEMPLATE.replace('<div id="app"></div>', '<div id="root"></div>'), page))
})

test('every prerendered page has an nginx location that serves it', () => {
  for (const { url, file } of PAGES) {
    const block = NGINX.match(new RegExp(`location = ${url.replace(/\//g, '\\/')} \\{[^}]*\\}`))
    assert.ok(block, `nginx.conf has no location for ${url}`)
    assert.match(block[0], new RegExp(`try_files /${file.replace(/\./g, '\\.')} /index\\.html;`))
  }
})

test('a dark-theme visitor gets dark theme variables before any script runs', () => {
  const css = dualThemeCss(':root { --v-theme-surface: 255,255,255; }', ':root { --v-theme-surface: 27,29,32; }')
  assert.match(css, /:root \{ --v-theme-surface: 255,255,255; \}/)
  assert.match(css, /:root\[data-theme='dark'\] \{ --v-theme-surface: 27,29,32; \}/)
})

test('server-rendered components no longer pin themselves to the light theme', () => {
  const out = stripThemeClass(
    '<button class="v-btn v-theme--light v-btn--flat"></button><i class="v-theme--light"></i>'
  )
  assert.equal(out, '<button class="v-btn v-btn--flat"></button><i></i>')
})

test('a page links only the chunks and stylesheets the entry does not already carry', () => {
  const manifest = {
    'src/Landing.vue': ['/assets/Landing.js', '/assets/Landing.css'],
    'src/main.ts': ['/assets/main.js'],
  }
  const template = '<script type="module" src="/assets/main.js"></script>'
  assert.deepEqual(pageAssets(['src/Landing.vue', 'src/main.ts', 'src/unknown.vue'], manifest, template), {
    css: ['/assets/Landing.css'],
    js: ['/assets/Landing.js'],
  })
})

test('a stylesheet loaded off the first paint is still a stylesheet, fetched early at its own priority', () => {
  const late = `<link rel="stylesheet" crossorigin href="/assets/vuetify.css" media="print" onload="this.media='all'">`
  const blocking = '<link rel="stylesheet" href="/assets/blocking.css">'
  const out = withStylePreloads(`<head>${late}${blocking}<noscript>${blocking}</noscript></head>`)
  // The stylesheet itself is left as it was: the app's code loader recognises it only as rel="stylesheet".
  assert.ok(out.includes(late))
  assert.match(out, /<link rel="preload" as="style" href="\/assets\/vuetify\.css" crossorigin><link rel="stylesheet"/)
  assert.equal(out.match(/rel="preload"/g).length, 1)
})
