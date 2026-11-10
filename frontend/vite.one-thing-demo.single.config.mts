// 「一件事」演示的单文件构建：artifact 页面跑在 origin 为 null 的沙箱里，
// ES module 脚本和 @font-face 都按 CORS 取，在那里一律失败（页面空白）。
// 所以这份构建把全部代码打成一个经典脚本、样式和字体全部内联；
// scripts/one-thing-demo-inline.mjs 再把样式塞进 index.html，脚本放成旁边的 app.js。
import { fileURLToPath, URL } from 'node:url'

import base from './vite.one-thing-demo.config.mts'
import { mergeConfig } from 'vite'

// 单文件里没有懒加载可言，重依赖换成替身，理由见 src/demo-one-thing/heavy-stub.ts。
const stub = fileURLToPath(new URL('./src/demo-one-thing/heavy-stub.ts', import.meta.url))

export default mergeConfig(base, {
  resolve: { alias: [{ find: /^(mermaid|exceljs|pdfjs-dist|echarts)(\/.*)?$/, replacement: stub }] },
  build: {
    outDir: 'dist-one-thing-demo-single',
    assetsInlineLimit: 100_000_000,
    cssCodeSplit: false,
    modulePreload: false,
    rollupOptions: { output: { format: 'iife', inlineDynamicImports: true } },
  },
})
