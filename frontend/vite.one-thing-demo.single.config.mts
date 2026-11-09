// 「一件事」演示的单文件构建：artifact 页面跑在 origin 为 null 的沙箱里，
// ES module 脚本和 @font-face 都按 CORS 取，在那里一律失败（页面空白）。
// 所以这份构建把全部代码打成一个经典脚本、样式和字体全部内联，再由
// scripts/one-thing-demo-inline.mjs 塞进 index.html，产物只有这一个文件。
import base from './vite.one-thing-demo.config.mts'
import { mergeConfig } from 'vite'

export default mergeConfig(base, {
  build: {
    outDir: 'dist-one-thing-demo-single',
    assetsInlineLimit: 100_000_000,
    cssCodeSplit: false,
    modulePreload: false,
    rollupOptions: { output: { format: 'iife', inlineDynamicImports: true } },
  },
})
