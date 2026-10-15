import { buildCatalog } from '../catalog'

// zh-CN 里首屏用不上的那些命名空间：没有一份跟着入口走的代码用到它们，用到它们的页面
// 都是按需加载的。它们不进入口那一块，入口跑起来之后再取，挂载前到位（`../../index.ts`
// 的 `localeReady` 一起等）：入口这一块是每次冷打开都要先下完的，这里省下的就是首屏少
// 等的。清单和 `./index.ts` 里排除的那一份是同一张，两边对得上由 `../../catalog.spec.ts`
// 守着。加一个命名空间时，首屏代码用得到它就别放进来。
const modules = import.meta.glob(
  [
    './catalog.json',
    './teams.json',
    './featureStats.json',
    './admin.json',
    './models.json',
    './ratchet.json',
    './routines.json',
    './notifications.json',
    './credits.json',
    './toolLabels.json',
    './questions.json',
    './members.json',
    './design.json',
    './editor.json',
    './compute.json',
    './usage.json',
    './users.json',
    './slides.json',
    './market.json',
    './ask.json',
    './integrations.json',
    './topic.json',
    './taskList.json',
  ],
  { eager: true, import: 'default' }
)

export default buildCatalog(modules)
