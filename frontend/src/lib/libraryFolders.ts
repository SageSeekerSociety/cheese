// 资料库的文件夹：名字里的 `/` 就是文件夹，没有单独的文件夹清单。
//
// 一个文件夹在它里面还有文件时存在。所以这里从文件清单算出「某一层有哪些文件夹、
// 哪些文件」，而不是去问服务端要一棵树。
import type { LibraryFile } from './libraryApi'

export interface LibraryFolder {
  /** 整条路径，`合同/2026`。 */
  path: string
  /** 最后一层，`2026`。 */
  name: string
  /** 里面（含更深几层）有几份文件。 */
  count: number
  /** 里面最近放进来的那一份的时间（Unix 秒）。 */
  modified: number
}

/** 最后一层的名字。 */
export function leafOf(path: string): string {
  return path.slice(path.lastIndexOf('/') + 1)
}

/** 所在的文件夹；在最上层时是 `''`。 */
export function folderOf(path: string): string {
  const at = path.lastIndexOf('/')
  return at < 0 ? '' : path.slice(0, at)
}

/** 面包屑：从最上层到 `dir` 的每一层，`合同/2026` → `合同`、`合同/2026`。 */
export function crumbs(dir: string): { path: string; name: string }[] {
  if (!dir) return []
  const parts = dir.split('/')
  return parts.map((name, i) => ({ path: parts.slice(0, i + 1).join('/'), name }))
}

/** `dir` 这一层：直接在里面的文件夹（新的在前）和文件（保持传进来的顺序）。 */
export function folderListing(files: LibraryFile[], dir: string): { folders: LibraryFolder[]; files: LibraryFile[] } {
  const prefix = dir ? `${dir}/` : ''
  const folders = new Map<string, LibraryFolder>()
  const here: LibraryFile[] = []
  for (const file of files) {
    if (!file.path.startsWith(prefix)) continue
    const rest = file.path.slice(prefix.length)
    const slash = rest.indexOf('/')
    if (slash < 0) {
      here.push(file)
      continue
    }
    const name = rest.slice(0, slash)
    const folder = folders.get(name) ?? { path: prefix + name, name, count: 0, modified: 0 }
    folder.count += 1
    folder.modified = Math.max(folder.modified, file.modified)
    folders.set(name, folder)
  }
  return { folders: [...folders.values()].sort((a, b) => b.modified - a.modified), files: here }
}

/** 资料库里的每一个文件夹（整条路径），给「移动到」挑。 */
export function allFolders(files: LibraryFile[]): string[] {
  const found = new Set<string>()
  for (const file of files) {
    let dir = folderOf(file.path)
    while (dir && !found.has(dir)) {
      found.add(dir)
      dir = folderOf(dir)
    }
  }
  return [...found].sort((a, b) => a.localeCompare(b))
}
