import { buildCatalog } from '../catalog'

// 这个语言包拆在本目录的一棵目录树里，形状与 zh-CN 那份一一对应：每个 zh-CN 键在
// 这里都有英文值，缺一个或值空着都由 `src/i18n/catalog.spec.ts` 报出来。
const modules = import.meta.glob('./**/*.json', { eager: true, import: 'default' })

export default buildCatalog(modules)
