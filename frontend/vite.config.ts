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
      // The prod nginx gateway's `location /api/` strips exactly one `/api`
      // before reaching the backend, which is what makes `BASE = '/api/api'`
      // (see frontend/src/api.ts) land on the real `/api/...` 2.0 routes. The
      // dev server has no such gateway, so `/api/api/...` would otherwise pass
      // straight through and 404. Only unwrap that specific doubled prefix —
      // genuine single-`/api/...` requests (e.g. the terminal iframe URL from
      // `terminal.py`, which is `/api/topics/.../terminal/live/` verbatim)
      // must reach the backend unrewritten.
      '/api': {
        target: process.env.BACKEND_URL ?? 'http://127.0.0.1:8799',
        changeOrigin: true,
        ws: true,
        rewrite: (path) => path.replace(/^\/api\/api\b/, '/api'),
      },
      '/connector': { target: process.env.BACKEND_URL ?? 'http://127.0.0.1:8799', changeOrigin: true, ws: true },
      // 1.0 routers are bare (`/users`, `/spaces`); SRP login
      // (`GET /users/auth/methods/:username`) needs this to reach the backend
      // in dev mode at all.
      '/users': { target: process.env.BACKEND_URL ?? 'http://127.0.0.1:8799', changeOrigin: true, ws: true },
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
  },
  optimizeDeps: {
    include: ['editorjs-parser'],
  },
})
