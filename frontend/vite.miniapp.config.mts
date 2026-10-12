// The mini-app shells (miniapp/v<n>/), each built into ONE self-contained HTML
// file at public/miniapp/v<n>/index.html, so the app build after it ships them
// in dist and `vite` serves them while developing.
//
// One file because of where it runs: the content host serves exactly that file
// (backend/app/api/app_shell_host.py), and the platform's browser checks a new
// mini-app by loading it as markup, where a sibling file it names is never even
// requested. So the script, the styles, KaTeX's fonts — everything — is inside.
//
//     pnpm build:miniapp
//
// The tokens are src/style.css's, read here at build time
// (`virtual:doc-tokens.css`), so the shell is in the document's colours without
// a second copy of the palette to keep in step.
import { readFileSync } from 'node:fs'
import { fileURLToPath, URL } from 'node:url'

import type { Plugin } from 'vite'

import { defineConfig } from 'vite'

const SHELL = process.env.MINIAPP_SHELL ?? '1'
const here = (path: string) => fileURLToPath(new URL(path, import.meta.url))

/** The light `:root { … }` block and the dark one, out of src/style.css. */
function tokenBlocks(): string {
  const css = readFileSync(here('./src/style.css'), 'utf8')
  const block = (opener: string) => {
    const start = css.indexOf(`\n${opener} {`)
    if (start < 0) throw new Error(`src/style.css has no "${opener} {" block`)
    let depth = 0
    for (let i = css.indexOf('{', start); i < css.length; i++) {
      if (css[i] === '{') depth++
      if (css[i] === '}' && --depth === 0) return css.slice(start + 1, i + 1)
    }
    throw new Error(`src/style.css: "${opener}" never closes`)
  }
  return `${block(':root')}\n${block(":root[data-theme='dark']")}\n`
}

function docTokens(): Plugin {
  const id = 'virtual:doc-tokens.css'
  return {
    name: 'miniapp-doc-tokens',
    resolveId: (source) => (source === id ? `\0${id}` : null),
    load: (resolved) => (resolved === `\0${id}` ? tokenBlocks() : null),
  }
}

/** Fold the built script and stylesheet into the HTML: one file, nothing else. */
function singleFile(): Plugin {
  return {
    name: 'miniapp-single-file',
    enforce: 'post',
    generateBundle(_options, bundle) {
      const page = Object.values(bundle).find((file) => file.fileName.endsWith('.html'))
      if (!page || page.type !== 'asset') return
      let html = String(page.source)
      for (const [name, file] of Object.entries(bundle)) {
        if (file.type === 'chunk') {
          const tag = new RegExp(
            `<script[^>]*src="[^"]*${file.fileName.replace(/[.*+?^${}()|[\]\\/]/g, '\\$&')}"[^>]*></script>`
          )
          const code = file.code.replace(/<\/script/gi, '<\\/script')
          // A function replacement: the code may hold `$&` and the like.
          html = html.replace(tag, () => `<script type="module">${code}</script>`)
          delete bundle[name]
        } else if (file.fileName.endsWith('.css')) {
          const tag = new RegExp(`<link[^>]*href="[^"]*${file.fileName.replace(/[.*+?^${}()|[\]\\/]/g, '\\$&')}"[^>]*>`)
          // KaTeX names each face in woff2, woff and ttf; every browser that
          // runs this takes the woff2, so the other two are a megabyte of nothing.
          const css = String(file.source).replace(/,\s*url\([^)]*\)\s*format\(["']?(?:woff|truetype)["']?\)/g, '')
          html = html.replace(tag, () => `<style>${css}</style>`)
          delete bundle[name]
        }
      }
      page.source = html
    },
  }
}

export default defineConfig({
  root: here(`./miniapp/v${SHELL}`),
  base: './',
  publicDir: false,
  plugins: [docTokens(), singleFile()],
  build: {
    outDir: here(`./public/miniapp/v${SHELL}`),
    emptyOutDir: true,
    cssCodeSplit: false,
    modulePreload: false,
    // Fonts and anything else a stylesheet names become data: URLs.
    assetsInlineLimit: () => true,
    rolldownOptions: {
      input: here(`./miniapp/v${SHELL}/index.html`),
      output: { codeSplitting: false },
    },
  },
})
