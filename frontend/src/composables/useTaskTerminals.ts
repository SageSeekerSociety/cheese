// The person's 终端 tabs in a task: one per shell of theirs there.
//
// Which shells there are is the machine's to say, not this page's: the page asks on opening
// the task (`listTaskTerminals`) and shows a tab for each, so a page closed and opened again —
// or a second page of the same person — shows the same shells. Adding a tab gives it a new id;
// the shell starts when the tab first reaches the machine (`useTaskTerminal`). Closing a tab
// ends its shell.
import { ref, watch } from 'vue'

import { closeTaskTerminal, listTaskTerminals } from '@/api'

function newId(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(8))
  return 't' + Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')
}

/**
 * @param taskId the task, or null where there is none.
 * @param enabled whether this page offers terminals at all (a task, on a desktop layout, not in review).
 */
export function useTaskTerminals(taskId: () => string | null, enabled: () => boolean) {
  const ids = ref<string[]>([])
  let asked: string | null = null

  async function load(task: string) {
    asked = task
    try {
      const found = await listTaskTerminals(task)
      if (asked !== task) return
      const listed = found.terminals.map((shell) => shell.id)
      // A tab added while the list was on its way stays.
      ids.value = [...listed, ...ids.value.filter((id) => !listed.includes(id))]
    } catch {
      // Not listed is not none: the tabs added here stay, and the next visit asks again.
    }
  }

  watch(
    () => (enabled() ? taskId() : null),
    (task) => {
      ids.value = []
      asked = null
      if (task) void load(task)
    },
    { immediate: true }
  )

  /** A new tab, with a shell of its own once it reaches the machine. */
  function add(): string {
    const id = newId()
    ids.value = [...ids.value, id]
    return id
  }

  /** Close a tab and end its shell. */
  function close(id: string) {
    ids.value = ids.value.filter((known) => known !== id)
    const task = taskId()
    if (task) void closeTaskTerminal(task, id).catch(() => undefined)
  }

  return { ids, add, close }
}
