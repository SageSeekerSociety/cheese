// The rows of a task's file tree: the copy's files and its empty folders,
// folded into folders, folders first and each level by name. A folder is
// shown open only when it is in `open`.
import type { WorkspaceFile } from '../cx_types'

export interface TaskFileRow {
  type: 'dir' | 'file'
  path: string
  name: string
  depth: number
}

interface Node {
  dirs: Map<string, Node>
  files: string[]
}

export function taskFileRows(files: WorkspaceFile[], dirs: string[], open: ReadonlySet<string>): TaskFileRow[] {
  const root: Node = { dirs: new Map(), files: [] }
  const folder = (parts: string[]) => {
    let node = root
    for (const part of parts) {
      let child = node.dirs.get(part)
      if (!child) {
        child = { dirs: new Map(), files: [] }
        node.dirs.set(part, child)
      }
      node = child
    }
    return node
  }
  for (const f of files) {
    const parts = f.path.split('/')
    folder(parts.slice(0, -1)).files.push(f.path)
  }
  for (const d of dirs) folder(d.split('/'))
  const rows: TaskFileRow[] = []
  const walk = (node: Node, prefix: string, depth: number) => {
    for (const name of [...node.dirs.keys()].sort((a, b) => a.localeCompare(b))) {
      const path = prefix ? `${prefix}/${name}` : name
      rows.push({ type: 'dir', path, name, depth })
      if (open.has(path)) walk(node.dirs.get(name)!, path, depth + 1)
    }
    for (const path of [...node.files].sort((a, b) => a.localeCompare(b))) {
      rows.push({ type: 'file', path, name: path.slice(path.lastIndexOf('/') + 1), depth })
    }
  }
  walk(root, '', 0)
  return rows
}

/** The folders that lead to `path`. */
export function foldersOf(path: string): string[] {
  const parts = path.split('/').slice(0, -1)
  return parts.map((_, i) => parts.slice(0, i + 1).join('/'))
}
