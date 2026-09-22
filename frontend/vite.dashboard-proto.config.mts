// 看板原型的精简构建配置（临时，供话题预览用）。
//
// 和 vite.config.ts 不是一次合并，而是另起一份：那份里的 PWA、prism、压缩、
// Google 字体这些插件在这个产物上的效果是「多出 sw.js、多出字体下载」，而这份
// 产物只有一个 HTML。入口见 src/proto-dashboard.ts 的注释。
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
    // 数组形式而不是对象：`@/router` 那条必须是**精确匹配**（前缀匹配会连
    // `@/router/feedback` 一起吃掉，那份才是预览真正要的路由表），而精确匹配只能
    // 用正则写。数组按顺序取第一个命中的规则，所以精确的那几条排在 `@/` 前面。
    alias: [
      // UpdateBanner 经 @/pwa 引到 vite-plugin-pwa 的虚模块，这份构建里没有
      // 那个插件（产物是单文件、没有 service worker 可注册），补个替身。
      {
        find: /^virtual:pwa-register$/,
        replacement: fileURLToPath(new URL('./src/proto-pwa-stub.ts', import.meta.url)),
      },
      // `@/router` 的替身。原因见替身文件顶部：那一跳会带进整棵应用路由树
      // （工作区、WorkPanel、CodeEditor、monaco，proto.js 从 ~2MB 涨到 12.5MB）。
      {
        find: /^@\/router$/,
        replacement: fileURLToPath(new URL('./src/proto-router-stub.ts', import.meta.url)),
      },
      { find: /^@\//, replacement: fileURLToPath(new URL('./src/', import.meta.url)) },
    ],
  },
  build: {
    outDir: 'dist-dashboard-proto',
    emptyOutDir: true,
    cssCodeSplit: false,
    // 字体、图标全部内联成 data URL：产物要能作为**单个文件**挂到预览上，
    // 预览域只从选中产物所在目录取同级文件，多一个文件就多一次上传。
    assetsInlineLimit: 8 * 1024 * 1024,
    rollupOptions: {
      input: fileURLToPath(new URL('./dashboard-proto.html', import.meta.url)),
      output: {
        inlineDynamicImports: true,
        entryFileNames: 'proto.js',
        assetFileNames: 'proto.[ext]',
      },
    },
  },
})
