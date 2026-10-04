// prismjs 的 core 子路径（`prismjs/components/prism-core`）没有自带的类型声明 ——
// `@types/prismjs` 只覆盖入口（`prismjs`）和 components 目录，没覆盖逐个组件文件。
// 这里补一句：core 导出的是 Prism 本体，形状和入口一致，所以直接借用入口的类型。
//
// 为什么需要 default 导入而不是裸副作用导入：core 是 CJS，在 Rolldown 下被包成惰性
// 求值，只有它的导出被**碰到**时才会执行；默认导入并在 `prismCore.ts` 里用一次，才能
// 保证 core 先于语法脚本执行（详见 `utils/prism.ts`）。
declare module 'prismjs/components/prism-core' {
  import * as Prism from 'prismjs'
  const value: typeof Prism
  export default value
}
