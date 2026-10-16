// 「改动」这一格左边那棵文件树：这一支碰过哪些文件、哪些文件夹被收起来了、按路径
// 打开一份文件时把它的祖先文件夹重新展开。
import type { ComputedRef, Ref } from 'vue'
import type { WorkspaceFile } from '../../cx_types'
import type { FileDiff } from '../../lib/diff'

import { computed, ref } from 'vue'

export interface FileTreeDeps {
  /** 这一支改到的文件，已经按每个文件一段切好。 */
  fileDiffs: ComputedRef<FileDiff[]>
  /** 工作区里现在还有的文件：树上每个文件的字节数从这里补。 */
  files: Ref<WorkspaceFile[]>
}

/** 文件树的状态：折叠、定位，和树上真正列出来的那几行。 */
export function usePanelFileTree(deps: FileTreeDeps) {
  const { fileDiffs, files } = deps

  // 文件树: 「这一支碰过哪些文件、工作区里现在还有哪些」在这里定，折成一层层文件夹
  // 那件事在 lib/changesTree.ts（纯函数，没有组件）。
  // 改动清单是一份要逐个看完的清单，文件夹默认都开着；收起的那几个记在这里。
  const collapsedDirs = ref(new Set<string>())
  function toggleDir(path: string) {
    const next = new Set(collapsedDirs.value)
    if (next.has(path)) next.delete(path)
    else next.add(path)
    collapsedDirs.value = next
  }
  // 定位: when a file is opened by path (e.g. clicking a <&path> chip in chat or
  // the doc), reopen every ancestor folder so the tree shows where it lives. 树
  // 自己负责把那一行滚进视野——那是画的事，它听 revealTick。
  const revealTick = ref(0)
  function revealInTree(path: string) {
    const parts = path.split('/')
    if (parts.length > 1) {
      const next = new Set(collapsedDirs.value)
      let prefix = ''
      for (const part of parts.slice(0, -1)) {
        prefix = prefix ? `${prefix}/${part}` : part
        next.delete(prefix)
      }
      collapsedDirs.value = next
    }
    revealTick.value += 1
  }
  // 树上列的是这一支改到的文件。改动清单里可能有工作区已经没有的文件（这一支删掉了
  // 它）——那也是要审阅的一条，所以清单按 diff 列，不按工作区。
  const treeFiles = computed<WorkspaceFile[]>(
    () =>
      fileDiffs.value.map((d) => ({
        path: d.path,
        bytes: files.value.find((f) => f.path === d.path)?.bytes ?? 0,
      })) as WorkspaceFile[]
  )

  return { collapsedDirs, toggleDir, revealTick, revealInTree, treeFiles }
}
