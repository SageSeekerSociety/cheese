import { buildCatalog } from '../catalog'

// 这个语言包拆在本目录的一棵目录树里，`import.meta.glob` 自己把它们全找出来：加一份
// 词表不用在这里补一行 import，也不用在下面那个对象里补一个字段（写法照仓库里已有的
// `src/views/demo/scenes/index.ts`、`src/commands/palette/sources.ts`）。
// 路径即键路径，见 `../catalog.ts` 和 `docs/i18n.md` 第 2 节。
const modules = import.meta.glob('./**/*.json', { eager: true, import: 'default' })

export default buildCatalog(modules)
