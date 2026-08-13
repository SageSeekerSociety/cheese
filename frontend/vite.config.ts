// Plugins
import { fileURLToPath, URL } from 'node:url'

import vue from '@vitejs/plugin-vue'
import vueJsx from '@vitejs/plugin-vue-jsx'
import ViteFonts from 'unplugin-fonts/vite'
// Utilities
import { defineConfig } from 'vite'
import viteCompression from 'vite-plugin-compression'
import { prismjsPlugin } from 'vite-plugin-prismjs'
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
