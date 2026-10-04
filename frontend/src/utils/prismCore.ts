// Prism 的 core 单独放一个模块，只为「先让 core 跑起来」。为什么需要它见 utils/prism.ts。
//
// prism-core 是 CJS，在 Rolldown 下被包成惰性求值：只有有人碰它的导出时才执行。core 执行时
// 会把自己挂到全局（window.Prism），而语法/插件是引用全局 Prism 的顶层脚本。把这个模块的
// 求值放在语法之前，就能保证 core 先执行、全局 Prism 先就位。
import Prism from 'prismjs/components/prism-core'

const globalScope = globalThis as unknown as { Prism?: typeof Prism }
globalScope.Prism = Prism

export default Prism
