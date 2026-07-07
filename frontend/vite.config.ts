// Plugins
import { fileURLToPath, URL } from 'node:url'

import legacy from '@vitejs/plugin-legacy'
import vue from '@vitejs/plugin-vue'
import vueJsx from '@vitejs/plugin-vue-jsx'
import ViteFonts from 'unplugin-fonts/vite'
// Utilities
import { defineConfig } from 'vite'
import viteCompression from 'vite-plugin-compression'
import { prismjsPlugin } from 'vite-plugin-prismjs'
import vuetify, { transformAssetUrls } from 'vite-plugin-vuetify'
import svgLoader from 'vite-svg-loader'

// Forward the browser's original Host to the backend so it can reconstruct the public
// origin (scheme+host) the request actually came in on — the backend derives the cli
// `base` and the device-approve link from this, keeping the code independent of how the
// site is reached (host/port/proxy). Mirrors what a prod ingress sets as X-Forwarded-*.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const forwardOriginalHost = (proxy: any) => {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  proxy.on('proxyReq', (proxyReq: any, req: any) => {
    const host = req.headers?.host
    if (host) proxyReq.setHeader('x-forwarded-host', host)
    proxyReq.setHeader('x-forwarded-proto', 'http')
  })
}

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
    legacy({
      targets: ['defaults', 'not IE 11'],
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
    host: true,
    port: 3000,
    allowedHosts: true,
    proxy: {
      // Standard API edge (dev mirror of the prod ingress): <origin>/api/* → backend
      // root. Covers the REST API *and* every cli-facing connector endpoint the frozen
      // cli reaches with its <origin>/api base — device flow (/api/auth/device/*),
      // control WS (/api/agent), tool door (/api/agent-api/*), client artifacts
      // (/api/connector/latest/*) and the OpenAPI spec (/api/openapi.json). ws:true so
      // the /api/agent upgrade reaches the backend.
      '/api': {
        target: 'http://localhost:8080',
        changeOrigin: true,
        ws: true,
        rewrite: (p) => p.replace(/^\/api/, ''),
        configure: forwardOriginalHost,
      },
      // Browser-side connector calls (group chat, agents list) + 现场 viewer WS still
      // address the backend directly at /connector (they don't go through /api).
      '/connector': {
        target: 'http://localhost:8080',
        changeOrigin: true,
        ws: true,
        configure: forwardOriginalHost,
      },
      // Memorable installer: `curl <origin>/install.sh | sh`.
      '/install.sh': {
        target: 'http://localhost:8080',
        changeOrigin: true,
        configure: forwardOriginalHost,
      },
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
