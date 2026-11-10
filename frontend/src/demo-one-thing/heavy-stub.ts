/**
 * mermaid / echarts / exceljs / pdfjs 的替身，只给单文件演示构建用（vite.one-thing-demo.single.config.mts）。
 *
 * 单文件构建把所有懒加载块并进同一个 app.js，这几个库合起来占了 12.5 MB 里的大半，
 * 页面要等它们全下完才开始渲染，于是打开时白屏几十秒。演示数据里没有图表、表格、
 * PDF 预览和 mermaid 图，这些代码路径不会走到；真走到了就在这里报错，而不是静默空白。
 */
const fail = (): never => {
  throw new Error('演示构建里没有带这个库（heavy-stub.ts）')
}
export default new Proxy({}, { get: fail })
export const use = fail
export const init = fail
export const GlobalWorkerOptions = {}
export const getDocument = fail
