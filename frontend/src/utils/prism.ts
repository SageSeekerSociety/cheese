/* eslint-disable simple-import-sort/imports -- 导入顺序是功能的一部分：core 必须先于语法/插件
   求值（见下），自动排序会把副作用导入提前，重新触发 `Prism is not defined`。 */
// 应用里统一从这里拿 Prism。
//
// 为什么不用 vite-plugin-prismjs 自动注入：它把 `import Prism from 'prismjs'` 展开成一串
// 「引用全局 Prism」的副作用脚本导入（prism-markup 等）。在 Rolldown 下 prism-core 是 CJS、
// 被包成惰性求值，而这些脚本是顶层语句、会立即执行 —— 脚本先跑、core 还没跑，于是整块抛
// `ReferenceError: Prism is not defined`：凡是加载 Prism 的路由（空间待审核、题目答案、
// Markdown 渲染）都会在模块求值时炸掉、页面白屏。自己按顺序导入可保证 core 先于语法执行。
//
// 语法/插件的清单要和 vite.config.ts 里 PRISM_LANGUAGES / PRISM_PLUGINS 保持一致。
import Prism from './prismCore'

// clike 是 javascript / typescript 等的前置语法，必须最先。
import 'prismjs/components/prism-clike'
import 'prismjs/components/prism-markup'
import 'prismjs/components/prism-css'
import 'prismjs/components/prism-javascript'
import 'prismjs/components/prism-typescript'
import 'prismjs/components/prism-jsx'
import 'prismjs/components/prism-tsx'
import 'prismjs/components/prism-python'
import 'prismjs/components/prism-go'
import 'prismjs/components/prism-rust'
import 'prismjs/components/prism-bash'
import 'prismjs/components/prism-json'
import 'prismjs/components/prism-yaml'
import 'prismjs/components/prism-toml'
import 'prismjs/components/prism-sql'
import 'prismjs/components/prism-markdown'
import 'prismjs/components/prism-diff'
import 'prismjs/components/prism-docker'
// toolbar 是 line-numbers / copy-to-clipboard 的前置插件。
import 'prismjs/plugins/toolbar/prism-toolbar'
import 'prismjs/plugins/line-numbers/prism-line-numbers'
import 'prismjs/plugins/copy-to-clipboard/prism-copy-to-clipboard'
import 'prismjs/themes/prism-solarizedlight.css'

export default Prism
