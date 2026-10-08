// 「改动」那一格左边那棵树：把工作区的一串路径折成一层层文件夹，再摊成能画的行。
//
// 单独放在这里而不是留在一个 computed 里，是因为这件事**没有组件**：给它一串路径
// 和一个 diff，它给回一串行。谁画的都行 —— 现在画的是 `ChangesFileTree.vue`，那一
// 格自己拿它算右半边；测试里也可以直接喂一串路径断言行的形状。
import type { WorkspaceFile } from '../cx_types'
import type { FileDiff } from './diff'

/** 树上的一行：文件夹，或者一份文件。 */
export interface FileRow {
  type: 'dir' | 'file'
  path: string // full relative path (dir or file)
  name: string // last segment, what we display
  depth: number
  /** 已经格式化好的字节数（`120 B`）：画的那一半不该再知道怎么数文件大小。 */
  size: string
  /** Set when this topic's branch touches the file — the marker on the row. */
  diff?: FileDiff
}

export function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / (1024 * 1024)).toFixed(1)} MB`
}

export interface FileRowsInput {
  /** 这一格该显示哪些文件：只显示这一支改过的，还是整个工作区。 */
  files: WorkspaceFile[]
  /** 路径 → 它自己的那一段 diff。改过的文件才在里面。 */
  diffByPath: Map<string, FileDiff>
  /** 改动清单默认一律摊开；人收起的那几个文件夹。 */
  collapsedDirs: Set<string>
}

/**
 * 折成树再摊成行：文件夹在前、每层按名字排，只有展开的文件夹贡献它的子树。
 * 全展开的顺序 = 读者的阅读顺序，所以这里不是排序问题而是形状问题。
 */
export function buildFileRows({ files, diffByPath, collapsedDirs }: FileRowsInput): FileRow[] {
  interface DirNode {
    dirs: Map<string, DirNode>
    files: WorkspaceFile[]
  }
  const root: DirNode = { dirs: new Map(), files: [] }
  for (const f of files) {
    const parts = f.path.split('/')
    let node = root
    for (const part of parts.slice(0, -1)) {
      let child = node.dirs.get(part)
      if (!child) {
        child = { dirs: new Map(), files: [] }
        node.dirs.set(part, child)
      }
      node = child
    }
    node.files.push(f)
  }
  const rows: FileRow[] = []
  const walk = (node: DirNode, prefix: string, depth: number) => {
    for (const name of [...node.dirs.keys()].sort((a, b) => a.localeCompare(b))) {
      const path = prefix ? `${prefix}/${name}` : name
      rows.push({ type: 'dir', path, name, depth, size: '' })
      // A changed-files list is a checklist, so it is open unless folded by hand.
      if (!collapsedDirs.has(path)) {
        walk(node.dirs.get(name)!, path, depth + 1)
      }
    }
    const sorted = [...node.files].sort((a, b) => a.path.localeCompare(b.path))
    for (const f of sorted) {
      rows.push({
        type: 'file',
        path: f.path,
        name: f.path.split('/').pop() ?? f.path,
        depth,
        size: fmtBytes(f.bytes),
        diff: diffByPath.get(f.path),
      })
    }
  }
  walk(root, '', 0)
  return rows
}
