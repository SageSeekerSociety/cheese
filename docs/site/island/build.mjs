// Builds island/ against the frontend's own source and dependencies, so the
// demo renders with the real components and design tokens.
//   node prototypes/docs-site/island/build.mjs   (needs: cd frontend && pnpm install)
// Vite resolves bare imports only for files under its root, so the island's
// three files are staged in a scratch folder inside frontend/ for the build and
// removed afterwards; nothing is written into frontend/ permanently.
import fs from 'node:fs'
import { createRequire } from 'node:module'
import path from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const here = path.dirname(fileURLToPath(import.meta.url))
const fe = path.resolve(here, '../../../frontend')
const stage = path.join(fe, '.docs-island-build')
const req = createRequire(path.join(fe, 'package.json'))
const load = async (name) => (await import(pathToFileURL(req.resolve(name)).href)).default

process.chdir(fe)
const vite = await import(pathToFileURL(req.resolve('vite')).href)
const vue = await load('@vitejs/plugin-vue')
const vuetify = await load('vite-plugin-vuetify')
const svgLoader = await load('vite-svg-loader')

fs.rmSync(stage, { recursive: true, force: true })
fs.mkdirSync(stage)
for (const f of ['index.html', 'entry.ts', 'DocsRoom.vue']) fs.copyFileSync(path.join(here, f), path.join(stage, f))
try {
  await vite.build({
    configFile: false,
    root: stage,
    base: './',
    logLevel: 'warn',
    plugins: [vue(), svgLoader(), vuetify({ autoImport: true, styles: { configFile: path.join(fe, 'src/styles/vuetify-settings.scss') } })],
    resolve: { alias: [{ find: /^@\//, replacement: path.join(fe, 'src') + '/' }] },
    build: { outDir: path.join(here, 'dist'), emptyOutDir: true, assetsInlineLimit: 100_000_000, cssCodeSplit: false, modulePreload: false, rollupOptions: { output: { inlineDynamicImports: true } } },
  })
} finally {
  fs.rmSync(stage, { recursive: true, force: true })
}
// One self-contained file for the docs page to embed. Every browser in use
// takes woff2, so the icon font's other three formats (2.4 MB inlined) go.
const dist = path.join(here, 'dist')
const asset = (ext) => fs.readdirSync(path.join(dist, 'assets')).find((f) => f.endsWith(ext))
const css = fs.readFileSync(path.join(dist, 'assets', asset('.css')), 'utf8').replace(/@font-face\{[^}]*\}/g, (face) => {
  const woff2 = face.includes('Material Design Icons') && /url\((data:font\/woff2[^)]*)\)/.exec(face)
  return woff2 ? `@font-face{font-family:"Material Design Icons";src:url(${woff2[1]}) format("woff2");font-weight:400;font-style:normal}` : face
})
const js = fs.readFileSync(path.join(dist, 'assets', asset('.js')), 'utf8')
const html = `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><style>${css}</style></head><body><div id="room"></div><script type="module">${js.replace(/<\/script/g, '<\\/script')}</script></body></html>`
fs.writeFileSync(path.join(here, 'room.html'), html)
fs.rmSync(dist, { recursive: true, force: true })
console.log(`island/room.html ${(html.length / 1024).toFixed(0)} KB`)
