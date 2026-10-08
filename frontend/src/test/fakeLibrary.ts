// 资料库和文档两条列表接口的替身：给一份清单，照后端的规矩一页一页地答。
//
// 规矩和后端一样（`backend/app/domain/library/listing.py`、`app/core/rank.py`）：不搜
// 不筛时答 `dir` 那一层，文件夹在前；`flat` / `q` / `kind` 给了就是整个资料库平铺；
// 每一行带 `rank`，按字符串从小到大就是显示顺序，下一页从游标之后接着给。
import type { ListedDocument, ProjectDocument } from '../api/projectDocuments'
import type { LibraryEntry, LibraryFile, LibraryPage, LibraryQuery } from '../lib/libraryApi'

import { kindOf } from '../lib/libraryKinds'

const SPAN = 10n ** 17n

function rankOf(group: number, micros: bigint, tie: string): string {
  return `${group}${(SPAN - micros).toString().padStart(17, '0')}${tie}`
}

/** 一份资料库里的文件；`modified` 是 Unix 秒。 */
export function libraryFile(path: string, extra: Partial<LibraryFile> = {}): LibraryFile {
  const modified = extra.modified ?? 1758000000
  return {
    type: 'file',
    path,
    bytes: 2048,
    modified,
    added_by: 'alice',
    added_at: new Date(modified * 1000).toISOString(),
    room: null,
    replaced: 0,
    references: 0,
    ...extra,
    rank: rankOf(1, BigInt(Math.round(modified * 1e6)), path),
  }
}

/** 文档列表里的一份：带上它的位置。 */
export function listedDocument(doc: ProjectDocument): ListedDocument {
  return { ...doc, rank: rankOf(1, BigInt(Date.parse(doc.updated_at)) * 1000n, doc.id) }
}

function page<T extends { rank: string }>(rows: T[], cursor: string | null | undefined, limit = 50) {
  const sorted = [...rows].sort((a, b) => (a.rank < b.rank ? -1 : 1))
  const after = cursor ? sorted.filter((row) => row.rank > cursor) : sorted
  const data = after.slice(0, limit)
  return { data, next: after.length > limit ? data[data.length - 1].rank : null }
}

/** `listProjectLibrary` 的替身，答的是 `files()` 此刻的样子。 */
export function servesLibrary(files: () => LibraryFile[]) {
  return async (_projectId: string, query: LibraryQuery = {}): Promise<LibraryPage> => {
    const all = files()
    const q = (query.q ?? '').trim().toLowerCase()
    if (query.flat || q || query.kind) {
      const matched = all.filter(
        (file) => (!q || file.path.toLowerCase().includes(q)) && (!query.kind || kindOf(file.path) === query.kind)
      )
      return page<LibraryEntry>(matched, query.cursor, query.limit)
    }
    const prefix = query.dir ? `${query.dir}/` : ''
    const folders = new Map<string, { count: number; modified: number }>()
    const here: LibraryEntry[] = []
    for (const file of all) {
      if (!file.path.startsWith(prefix)) continue
      const rest = file.path.slice(prefix.length)
      const slash = rest.indexOf('/')
      if (slash < 0) {
        here.push(file)
        continue
      }
      const name = rest.slice(0, slash)
      const folder = folders.get(name) ?? { count: 0, modified: 0 }
      folders.set(name, { count: folder.count + 1, modified: Math.max(folder.modified, file.modified) })
    }
    for (const [name, folder] of folders)
      here.push({
        type: 'folder',
        path: prefix + name,
        name,
        count: folder.count,
        modified: folder.modified,
        rank: rankOf(0, BigInt(Math.round(folder.modified * 1e6)), name),
      })
    return page(here, query.cursor, query.limit)
  }
}

/** `listProjectDocuments` 的替身。 */
export function servesDocuments(docs: () => ProjectDocument[]) {
  return async (_projectId: string, cursor: string | null = null, limit = 50) =>
    page(docs().map(listedDocument), cursor, limit)
}
