// Plugins
import { fileURLToPath, URL } from 'node:url'

import vue from '@vitejs/plugin-vue'
import vueJsx from '@vitejs/plugin-vue-jsx'
import ViteFonts from 'unplugin-fonts/vite'
// Utilities
import { defineConfig } from 'vite'
import viteCompression from 'vite-plugin-compression'
import { prismjsPlugin } from 'vite-plugin-prismjs'
import { VitePWA } from 'vite-plugin-pwa'
import vuetify, { transformAssetUrls } from 'vite-plugin-vuetify'
import svgLoader from 'vite-svg-loader'
import { configDefaults } from 'vitest/config'

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [
    vue({
      template: { transformAssetUrls },
    }),
    svgLoader(),
    vueJsx(),
    // https://github.com/vuetifyjs/vuetify-loader/tree/next/packages/vite-plugin
    vuetify({
      autoImport: true,
    }),
    ViteFonts({
      google: {
        families: [
          {
            name: 'Roboto',
            styles: 'wght@100;300;400;500;700;900',
          },
        ],
      },
    }),
    prismjsPlugin({
      languages: 'all',
      // 配置行号插件
      plugins: ['line-numbers', 'copy-to-clipboard'],
      // 主题名
      theme: 'solarizedlight',
      css: true,
    }),
    viteCompression(),
    // PWA / offline support. Goal (owner spec): the app shell + already-seen
    // content load offline; live features (WS chat, 现场 terminal, notifications)
    // degrade gracefully and auto-recover when the network returns. NO offline
    // writes / message queue — reads only.
    VitePWA({
      // autoUpdate: a new SW takes control and the page reloads itself, so a
      // deploy reaches every open tab without a manual refresh. We register it
      // ourselves in src/pwa.ts (registerSW), so nothing is injected here.
      registerType: 'autoUpdate',
      injectRegister: false,
      // The SW controls the whole origin; keep it at root scope.
      scope: '/',
      manifest: {
        name: 'Cheese',
        short_name: 'Cheese',
        description: '知是 · 协作与智能体工作台',
        lang: 'zh-CN',
        display: 'standalone',
        start_url: '/',
        scope: '/',
        // Theme/background mirror the Vuetify light theme (src/plugins/vuetify.ts):
        // primary amber #F57F17, canvas #F7F8FA.
        theme_color: '#F57F17',
        background_color: '#F7F8FA',
        icons: [
          { src: 'pwa-192x192.png', sizes: '192x192', type: 'image/png' },
          { src: 'pwa-512x512.png', sizes: '512x512', type: 'image/png' },
          // Maskable so Android/desktop can crop to their own icon shape
          // without clipping the mark (the source keeps a safe zone).
          { src: 'pwa-maskable-512x512.png', sizes: '512x512', type: 'image/png', purpose: 'maskable' },
        ],
      },
      workbox: {
        // Precache the app shell. maximumFileSizeToCacheInBytes is raised well
        // above the 2 MiB default because this bundle is heavy (monaco / tiptap
        // / prismjs-all) — the shell-critical chunks (vue, vuetify, entry) must
        // land in precache or an offline reload white-screens. The genuinely
        // huge, view-specific chunks that exceed even this are NOT precached;
        // the /assets/ runtime cache below picks them up on first online visit
        // instead, so precache stays bounded.
        globPatterns: ['**/*.{js,css,html,svg,woff,woff2,ico,png,webmanifest}'],
        // The Monaco language workers (editor/json/html/css/ts.worker-*.js, up
        // to ~7 MB each) are the biggest chunks in the bundle and are purely
        // optional — they load only inside the code editor, which is not part
        // of the shell. Keep them OUT of precache (that is "别缓存到爆"); the
        // /assets/ runtime cache below picks them up on first online use.
        globIgnores: ['**/*.worker-*.js'],
        // Raised from the 2 MiB default so the shell-critical `vendor` chunk
        // (~5 MB) is precached — leaving it out is exactly the "离线白屏" the
        // spec warns against. The only files bigger than this are the Monaco
        // workers, already excluded above.
        maximumFileSizeToCacheInBytes: 6 * 1024 * 1024,
        cleanupOutdatedCaches: true,
        // Take control of open pages as soon as a new SW activates. With
        // autoUpdate this is what makes a deploy reach every already-open tab
        // (the reload fires on controllerchange), and it lets the very first
        // visit be SW-controlled so an offline reload works without a second
        // manual load first.
        clientsClaim: true,
        skipWaiting: true,
        // Inline the workbox runtime into sw.js — one root file to keep
        // no-cached in nginx, instead of a separate workbox-*.js.
        inlineWorkboxRuntime: true,
        // SPA offline fallback: serve the cached index.html for navigations…
        navigateFallback: 'index.html',
        // …but NEVER for backend routes. These are same-origin navigations that
        // must reach the server (or fail, when offline) — not be answered with
        // the SPA HTML: the /api/* backend API, the /connector/* device plane,
        // bare 1.0 routes, and especially the two live iframes
        // (/api/topics/<id>/{terminal,app}/) whose content is served by the
        // backend and would break if shadowed by index.html.
        navigateFallbackDenylist: [/^\/api\//, /^\/connector\//, /^\/users\//],
        runtimeCaching: [
          {
            // Read-only API data (rooms / messages / project lists …):
            // network-first so online is always fresh, with a cache fallback so
            // offline still shows the last-seen content. Deliberately narrow —
            // see the guards below for everything that must NOT be cached.
            urlPattern: ({ url, request, sameOrigin }) => {
              if (request.method !== 'GET') return false
              if (!sameOrigin) return false
              if (!url.pathname.startsWith('/api/')) return false
              // Auth / token endpoints: never cache (session-sensitive).
              if (url.pathname.includes('/auth/')) return false
              if (url.pathname.includes('/oauth')) return false
              if (url.pathname.endsWith('/refresh-token')) return false
              // Token-in-query URLs (device screen, running-app iframe,
              // attachment downloads) — caching them would persist a bearer
              // token on disk and serve another user stale bytes.
              if (url.searchParams.has('token')) return false
              // Live iframes: served by the backend, must stay live.
              if (/^\/api\/topics\/[^/]+\/(terminal|app)(\/|$)/.test(url.pathname)) return false
              // SSE streams (agent advice): a NetworkFirst would hang forever
              // waiting to cache a response that never ends.
              if ((request.headers.get('accept') || '').includes('text/event-stream')) return false
              if (url.pathname.endsWith('/stream')) return false
              // Documents/iframes are navigations, handled above — not data.
              if (request.destination === 'document' || request.destination === 'iframe') return false
              return true
            },
            handler: 'NetworkFirst',
            options: {
              cacheName: 'cheese-api-get',
              // Short window: the SW cache is per-browser, not per-user, so a
              // shared machine could otherwise show one user another's cached
              // reads. Kept small in count and age to bound that exposure; the
              // cache is also wiped on logout (src/services/account.ts).
              networkTimeoutSeconds: 5,
              expiration: { maxEntries: 200, maxAgeSeconds: 60 * 30 },
              cacheableResponse: { statuses: [200] },
            },
          },
          {
            // Content-hashed build assets that were too big to precache
            // (monaco / prismjs-all, etc.). Immutable filenames → CacheFirst is
            // safe: a code change ships a new hash, never a stale hit.
            urlPattern: ({ url, sameOrigin }) => sameOrigin && url.pathname.startsWith('/assets/'),
            handler: 'CacheFirst',
            options: {
              cacheName: 'cheese-assets',
              expiration: { maxEntries: 200, maxAgeSeconds: 60 * 60 * 24 * 30 },
              cacheableResponse: { statuses: [0, 200] },
            },
          },
          {
            // Self-hosted / Google web fonts pulled at runtime.
            urlPattern: ({ request }) => request.destination === 'font',
            handler: 'CacheFirst',
            options: {
              cacheName: 'cheese-fonts',
              expiration: { maxEntries: 30, maxAgeSeconds: 60 * 60 * 24 * 365 },
              cacheableResponse: { statuses: [0, 200] },
            },
          },
        ],
      },
      // Keep the dev server clean: no SW in `pnpm dev` (it caches aggressively
      // and fights HMR). The SW is a production concern, verified via preview.
      devOptions: { enabled: false },
    }),
  ],
  define: { 'process.env': {} },
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
    extensions: ['.js', '.json', '.jsx', '.mjs', '.ts', '.tsx', '.vue'],
  },
  server: {
    port: 3000,
    proxy: {
      // Mirror the production nginx gateway (frontend/nginx.conf): `location /api/
      // { proxy_pass http://backend:8081/; }` strips exactly one `/api` from every
      // request. The frontend leans on that — api.ts uses BASE='/api/api' for 2.0
      // routes, and the 知是 1.0 layer rides VITE_API_BASE_URL=/api — so calls
      // arrive here double- (`/api/api/*`) or single- (`/api/users/*`) prefixed and
      // must lose exactly one `/api` to hit the real backend route.
      '/api': {
        // :8081 is where `task dev` puts the backend (backend/Taskfile.yml),
        // what e2e/playwright.config.ts starts, and what nginx talks to in
        // production. It defaulted to :8799 — the port scripts/dev/backend.sh
        // uses for the seeded demo — so the documented path (`task dev`) served
        // a page whose every API call reached a port nothing was listening on.
        // BACKEND_URL points this at another backend (e.g. that demo).
        target: process.env.BACKEND_URL ?? 'http://127.0.0.1:8081',
        changeOrigin: true,
        ws: true,
        // Exception: the two iframe proxies — the ttyd terminal
        // (/api/topics/<id>/terminal/live/) and the running-app preview
        // (/api/topics/<id>/app/) — are loaded verbatim by an iframe that resolves
        // its assets/WebSocket against that path, and the backend serves them at
        // that exact /api-prefixed path, so they must pass through unrewritten
        // (see routes/terminal.py, routes/app_preview.py — and nginx.conf, which
        // carries the same exception). Everything else loses one /api like nginx.
        rewrite: (path) =>
          /^\/api\/topics\/[^/]+\/(terminal|app)(\/|$)/.test(path) ? path : path.replace(/^\/api/, ''),
      },
      // Unlike /api, the backend serves /connector/* natively — no strip (matches nginx).
      '/connector': { target: process.env.BACKEND_URL ?? 'http://127.0.0.1:8081', changeOrigin: true, ws: true },
      // Safety net for any bare 1.0 call that bypasses the /api-prefixed axios layer
      // (e.g. SRP login GET /users/auth/methods/:username): reach the backend directly.
      '/users': { target: process.env.BACKEND_URL ?? 'http://127.0.0.1:8081', changeOrigin: true, ws: true },
    },
  },
  build: {
    minify: 'terser',
    terserOptions: {
      compress: {
        drop_console: true,
        drop_debugger: true,
      },
    },
    sourcemap: false,
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes('node_modules')) {
            if (id.includes('prosemirror')) {
              return 'prosemirror'
            }
            if (id.includes('dompurify')) {
              return 'dompurify'
            }
            if (id.includes('marked')) {
              return 'marked'
            }
            if (id.includes('zod')) {
              return 'zod'
            }
            if (id.includes('viewerjs')) {
              return 'viewerjs'
            }
            if (id.includes('vuetify-pro-tiptap')) {
              return 'vuetify-pro-tiptap'
            }
            if (id.includes('tiptap')) {
              return 'tiptap'
            }
            if (id.includes('vuetify')) {
              return 'vuetify'
            }
            // Vue 及其相关
            if (id.match(/(vue|vue-router)/)) {
              return 'vue'
            }
            // lodash 单独一个 chunk
            if (id.includes('lodash')) {
              return 'lodash'
            }
            // Editor.js 相关依赖归为一组
            if (id.includes('@editorjs')) {
              return 'editorjs'
            }
            // katex 单独一个 chunk
            if (id.includes('katex')) {
              return 'katex'
            }
            // dayjs 单独一个 chunk
            if (id.includes('dayjs')) {
              return 'dayjs'
            }
            // prismjs 单独一个 chunk
            if (id.includes('prismjs')) {
              return 'prismjs'
            }
            // axios 如果使用量较大，也可单独拆分
            if (id.includes('axios')) {
              return 'axios'
            }
            // 其他 node_modules 内的包统一归到 vendor
            return 'vendor'
          }
        },
      },
    },
  },
  test: {
    // 启用类似 jest 的全局测试 API
    globals: true,
    // 使用 happy-dom 模拟 DOM
    // 这需要你安装 happy-dom 作为对等依赖（peer dependency）
    environment: 'happy-dom',
    // Vuetify 的组件包自带 .css 副作用导入，被 externalize 掉就会以
    // "Unknown file extension .css" 崩在收集阶段——挂真实组件的测试需要它走
    // Vite 的 transform 管线。
    server: { deps: { inline: ['vuetify'] } },
    // `scripts/` is node:test territory (`pnpm run test:ratchet`), not vitest's.
    // Its *.test.mjs files match vitest's default include glob, and vitest fails
    // the whole run on them with "No test suite found" — node:test registers its
    // cases through `node:test`, which vitest's collector never sees.
    exclude: [...configDefaults.exclude, 'scripts/**'],
  },
  optimizeDeps: {
    include: ['editorjs-parser'],
  },
})
