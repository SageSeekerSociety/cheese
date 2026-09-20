// 反馈原型的精简构建配置（临时，供话题预览用）。
//
// 和 vite.config.ts 不是一次合并，而是另起一份：那份里的 PWA、prism、压缩、
// Google 字体这些插件在这个产物上的效果是「多出 sw.js、多出字体下载」，而这份
// 产物只有一个 HTML。入口见 src/proto-feedback.ts 的注释。
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
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
      // UpdateBanner 经 @/pwa 引到 vite-plugin-pwa 的虚模块，这份构建里没有
      // 那个插件（产物是单文件、没有 service worker 可注册），补个替身。
      'virtual:pwa-register': fileURLToPath(new URL('./src/proto-pwa-stub.ts', import.meta.url)),
    },
  },
  build: {
    outDir: 'dist-feedback-proto',
    emptyOutDir: true,
    cssCodeSplit: false,
    // 字体、图标全部内联成 data URL：产物要能作为**单个文件**挂到预览上，
    // 预览域只从选中产物所在目录取同级文件，多一个文件就多一次上传。
    assetsInlineLimit: 8 * 1024 * 1024,
    rollupOptions: {
      input: fileURLToPath(new URL('./feedback-proto.html', import.meta.url)),
      output: {
        inlineDynamicImports: true,
        entryFileNames: 'proto.js',
        assetFileNames: 'proto.[ext]',
      },
    },
  },
})
