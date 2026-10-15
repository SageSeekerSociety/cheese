import { buildCatalog } from '../catalog'

// zh-CN 里后端说的那两类话：请求被拒时的报错（`apiError`）和频道里的系统消息
// （`roomNotice`）。后端说它们时，键和一句写好的中文一起发过来（`backend/app/core/sentences.py`），
// 前端查不到这个键就显示那句中文（`src/lib/noticeText.ts`）。所以这一块不必在第一页之前到：
// 挂载之后才取（`../../index.ts` 的 `loadPlatformSentences`），到之前中文读者看到的字一样，
// 也不会露出键名。界面上直接用这两个命名空间的键，就等于丢了那句中文，别这么写。
const modules = import.meta.glob(['./apiError/**/*.json', './roomNotice.json'], { eager: true, import: 'default' })

export default buildCatalog(modules)
