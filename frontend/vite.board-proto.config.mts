// 题目板重设计原型的精简构建配置（临时，供话题预览用）。
//
// 与 vite.feedback-proto.config.mts / vite.dashboard-proto.config.mts 同一套路数：
// 产物是**一个自包含 HTML**，挂到话题预览上，所以 PWA、Google 字体这些插件一概不要。
// 入口见 src/proto-board.ts 的注释。
import { fileURLToPath, URL } from 'node:url'

import vue from '@vitejs/plugin-vue'
import vueJsx from '@vitejs/plugin-vue-jsx'
import { defineConfig } from 'vite'
import vuetify, { transformAssetUrls } from 'vite-plugin-vuetify'
import svgLoader from 'vite-svg-loader'

export default defineConfig({
  base: './',
  plugins: [vue({ template: { transformAssetUrls } }), svgLoader(), vueJsx(), vuetify({ autoImport: true })],
  resolve: {
    // 数组形式（理由同看板原型）：`@/router` 必须是**精确匹配**，前缀匹配会连
    // `@/router/spaces` 一起吃掉，而那份子路由表正是原型真正要的。命中的第一个规则生效，
    // 所以精确的两条排在 `@/` 前面。
    alias: [
      {
        find: /^virtual:pwa-register$/,
        replacement: fileURLToPath(new URL('./src/proto-pwa-stub.ts', import.meta.url)),
      },
      {
        find: /^@\/router$/,
        replacement: fileURLToPath(new URL('./src/proto-router-stub.ts', import.meta.url)),
      },
      { find: /^@\//, replacement: fileURLToPath(new URL('./src/', import.meta.url)) },
    ],
  },
  build: {
    outDir: 'dist-board-proto',
    emptyOutDir: true,
    cssCodeSplit: false,
    assetsInlineLimit: 8 * 1024 * 1024,
    rollupOptions: {
      input: fileURLToPath(new URL('./board-proto.html', import.meta.url)),
      output: {
        inlineDynamicImports: true,
        entryFileNames: 'proto.js',
        assetFileNames: 'proto.[ext]',
      },
    },
  },
})
