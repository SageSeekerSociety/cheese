// 「一件事」演示的构建配置（演示用，不并进产品构建）。
//
// 和 vite.config.ts 不是一次合并，而是另起一份：那份里的 PWA、prism、压缩这些插件
// 在这个产物上的效果是「多出 sw.js、多出预缓存」，而这份产物只是一页静态演示。
// `base: './'`：产物是一个 index.html 加 assets/，放在哪个目录下都能直接打开。
// 入口见 src/demo-one-thing/main.ts 的注释。
import { fileURLToPath, URL } from 'node:url'

import vue from '@vitejs/plugin-vue'
import vueJsx from '@vitejs/plugin-vue-jsx'
import { defineConfig } from 'vite'
import vuetify, { transformAssetUrls } from 'vite-plugin-vuetify'
import svgLoader from 'vite-svg-loader'

export default defineConfig({
  base: './',
  // public/ 是应用本体的那一套（图标、PWA 清单、push-sw.js、截图）：演示一样都不用，也不该带上 service worker。
  publicDir: false,
  plugins: [
    vue({ template: { transformAssetUrls } }),
    svgLoader(),
    vueJsx(),
    vuetify({ autoImport: true }),
    // 产物的页面叫 index.html：静态托管按目录取它。
    {
      name: 'one-thing-demo-index',
      enforce: 'post',
      generateBundle(_, bundle) {
        const page = bundle['one-thing-demo.html']
        if (page) page.fileName = 'index.html'
        // 图标和公式字体的 eot / ttf / woff 只是 woff2 后面的兜底，浏览器认 woff2 就不会去取它们。
        // 带着它们产物多出约 4 MB，超过发布的体积上限，所以只留 woff2。
        for (const name of Object.keys(bundle)) if (/\.(eot|ttf|woff)$/.test(name)) delete bundle[name]
      },
    },
  ],
  resolve: {
    // 和 vite.feedback-proto.config.mts 同一组替身，理由见那两个替身文件顶部。
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
    outDir: 'dist-one-thing-demo',
    emptyOutDir: true,
    rollupOptions: {
      input: { index: fileURLToPath(new URL('./one-thing-demo.html', import.meta.url)) },
    },
  },
})
