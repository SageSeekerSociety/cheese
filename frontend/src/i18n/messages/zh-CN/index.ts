import { buildCatalog } from '../catalog'

// 这个语言包拆在本目录的一棵目录树里，`import.meta.glob` 自己把它们全找出来：加一份
// 词表不用在这里补一行 import，也不用在下面那个对象里补一个字段（写法照仓库里已有的
// `src/views/demo/scenes/index.ts`、`src/commands/palette/sources.ts`）。
// 路径即键路径，见 `../catalog.ts` 和 `docs/i18n.md` 第 2 节。
//
// 首屏用不上的那些命名空间不在这里，见 `./deferred.ts`。
const modules = import.meta.glob(
  [
    // Top-level files and the trees under subdirectories as two patterns: Vite drops
    // the `!` exclusions below when they sit beside a `./**/*.json` pattern.
    './*.json',
    './*/**/*.json',
    '!./catalog.json',
    '!./teams.json',
    '!./featureStats.json',
    '!./admin.json',
    '!./models.json',
    '!./ratchet.json',
    '!./routines.json',
    '!./notifications.json',
    '!./credits.json',
    '!./toolLabels.json',
    '!./questions.json',
    '!./members.json',
    '!./design.json',
    '!./editor.json',
    '!./compute.json',
    '!./usage.json',
    '!./users.json',
    '!./slides.json',
    '!./market.json',
    '!./ask.json',
    '!./integrations.json',
    '!./topic.json',
    '!./taskList.json',
    // 后端说的那两类话，见 `./sentences.ts`。
    '!./roomNotice.json',
    '!./apiError/**',
  ],
  { eager: true, import: 'default' }
)

export default buildCatalog(modules)

/** zh-CN 的另一部分：首屏用不上的命名空间，单独一块，用时才取。 */
export const loadDeferred = () => import('./deferred').then((module) => module.default)

/** zh-CN 的第三部分：后端说的报错和系统消息，挂载之后才取（见 `./sentences.ts`）。 */
export const loadSentences = () => import('./sentences').then((module) => module.default)
