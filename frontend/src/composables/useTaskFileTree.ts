// Which folders of a task's file tree are open, held once for the task's page:
// the tree beside the open files (the 对话 form's 文件 panel) and the tree in a
// column of its own (the 编辑器 form) are the same tree, so a folder opened in one
// is open in the other. Opening a file opens the folders it sits in and asks the
// tree to scroll it into view (`revealTick`).
import type { TaskFilesBundle } from './useTaskFiles'

import { ref, watch } from 'vue'

import { foldersOf } from '../lib/taskFileTree'

export function useTaskFileTree(files: Pick<TaskFilesBundle, 'openPath'>) {
  const open = ref<Set<string>>(new Set())
  /** Bumped when the file opened should be scrolled into view. */
  const revealTick = ref(0)

  watch(
    () => files.openPath.value,
    (path) => {
      if (!path) return
      const next = new Set(open.value)
      for (const dir of foldersOf(path)) next.add(dir)
      open.value = next
      revealTick.value += 1
    },
    { immediate: true }
  )

  function toggle(path: string) {
    const next = new Set(open.value)
    if (next.has(path)) next.delete(path)
    else next.add(path)
    open.value = next
  }
  function openFolder(path: string) {
    if (!open.value.has(path)) open.value = new Set(open.value).add(path)
  }
  function collapseAll() {
    open.value = new Set()
  }

  return { open, revealTick, toggle, openFolder, collapseAll }
}

export type TaskFileTreeState = ReturnType<typeof useTaskFileTree>
