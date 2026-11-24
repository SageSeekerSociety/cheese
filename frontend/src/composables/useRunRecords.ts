// 运行记录 of a task: the commands kept under a name in its project, and the shell commands its
// AI teammates ran in it (newest first, an older page on asking). Read when the person opens the
// list, never before: nobody sees it until then.
//
// The list follows the task and the project it is shown for. A response that comes back after the
// task changed belongs to the old one and is dropped, so a row on screen always reruns in the task
// it was read from.
import type { NamedCommand, RanCommand } from '@/api'

import { ref, watch } from 'vue'

import { listNamedCommands, listRanCommands, nameCommand, removeNamedCommand } from '@/api'

export function useRunRecords(taskId: () => string | null | undefined, projectId: () => string | null | undefined) {
  const named = ref<NamedCommand[]>([])
  const ran = ref<RanCommand[]>([])
  const next = ref<string | null>(null)
  const loading = ref(false)
  const failed = ref(false)
  /** Naming a command did not go through. */
  const nameFailed = ref(false)
  let read = 0

  function clear() {
    read += 1
    named.value = []
    ran.value = []
    next.value = null
    loading.value = false
    failed.value = false
    nameFailed.value = false
  }
  watch([taskId, projectId], clear)

  /** Read the list again from the newest. */
  async function load() {
    const task = taskId()
    if (!task) return
    const project = projectId()
    const mine = ++read
    loading.value = true
    failed.value = false
    try {
      const [page, kept] = await Promise.all([
        listRanCommands(task),
        project ? listNamedCommands(project) : Promise.resolve([]),
      ])
      if (mine !== read) return
      ran.value = page.items
      next.value = page.next
      named.value = kept
    } catch {
      if (mine === read) failed.value = true
    } finally {
      if (mine === read) loading.value = false
    }
  }

  /** The page before the oldest one shown. */
  async function more() {
    const task = taskId()
    if (!task || !next.value || loading.value) return
    const mine = read
    loading.value = true
    try {
      const page = await listRanCommands(task, next.value)
      if (mine !== read) return
      ran.value = [...ran.value, ...page.items]
      next.value = page.next
    } catch {
      if (mine === read) failed.value = true
    } finally {
      if (mine === read) loading.value = false
    }
  }

  /** Keep `command` under `name` in the project; a name it already has now names this one. */
  async function name(label: string, command: string): Promise<boolean> {
    const project = projectId()
    if (!project) return false
    const mine = read
    nameFailed.value = false
    try {
      const row = await nameCommand(project, label, command)
      if (mine !== read) return false
      named.value = [...named.value.filter((one) => one.name !== row.name), row].sort((a, b) =>
        a.name.localeCompare(b.name)
      )
      return true
    } catch {
      if (mine === read) nameFailed.value = true
      return false
    }
  }

  async function remove(id: string) {
    const project = projectId()
    if (!project) return
    const mine = read
    try {
      await removeNamedCommand(project, id)
      if (mine === read) named.value = named.value.filter((one) => one.id !== id)
    } catch {
      if (mine === read) failed.value = true
    }
  }

  return { named, ran, next, loading, failed, nameFailed, load, more, name, remove }
}
