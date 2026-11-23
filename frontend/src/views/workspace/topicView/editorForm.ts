// 任务页的两种摆法：「对话」（对话是主角，旁边按需开一块）和「编辑器」（文件是主角，左边找东西、
// 中间是打开的文件、下面是终端、右边是对话）。同一件任务、同一条页头，只是谁是主角不同；两种
// 摆法怎么摆在 roomLayout.ts。
//
// 选哪一种按任务记在这台设备上，默认是对话。只有桌面上、页面的几块由外壳并排摆开的时候才有得
// 选（`available`）：手机上、平板横放的浮层那一档永远是对话。
//
// 进编辑器时收起项目侧栏，离开时（换回对话、离开这一页、窗口窄到没得选）放回进来之前的样子；
// 在编辑器里自己把侧栏展开了，就照读者的来，离开时不再动它。
import type { ComputedRef, Ref } from 'vue'

import { computed, onActivated, onBeforeUnmount, onDeactivated, ref, watch } from 'vue'

import { useAppNav } from '@/components/common/Navigation/appNav'
import { storageGet, storageRemove, storageSet } from '@/lib/browserStorage'

export type TaskForm = 'chat' | 'editor'

const KEY = 'cheesex.taskForm.'

export function useTaskForm(opts: {
  taskId: () => string | null | undefined
  /** 这一刻有得选：桌面上，页面的几块由外壳并排摆开。 */
  available: () => boolean
}): {
  /** 读者给这件任务选的那一种（没得选的时候也记着）。 */
  chosen: Ref<TaskForm>
  /** 此刻摆的是编辑器。 */
  editor: ComputedRef<boolean>
  choose: (form: TaskForm) => void
} {
  const chosen = ref<TaskForm>('chat')
  watch(
    opts.taskId,
    (id) => {
      chosen.value = id && storageGet(KEY + id) === 'editor' ? 'editor' : 'chat'
    },
    { immediate: true }
  )
  function choose(form: TaskForm) {
    chosen.value = form
    const id = opts.taskId()
    if (!id) return
    if (form === 'editor') storageSet(KEY + id, form)
    else storageRemove(KEY + id)
  }

  const onScreen = ref(true)
  onActivated(() => (onScreen.value = true))
  onDeactivated(() => (onScreen.value = false))
  const editor = computed(() => chosen.value === 'editor' && opts.available())

  // ---- 项目侧栏 ----
  const nav = useAppNav()
  /** 侧栏是进编辑器时这里收起的，读者还没自己展开过。 */
  let foldedHere = false
  function putBack() {
    if (!foldedHere) return
    foldedHere = false
    nav?.unfoldSidebar.value?.()
  }
  // 布局区在页面之后才挂上：等这一轮渲染完再收，收得到。
  watch(
    () => editor.value && onScreen.value,
    (inEditor) => {
      if (!inEditor) return putBack()
      const fold = nav?.foldSidebar.value
      if (!fold || nav?.unfoldSidebar.value) return
      fold()
      foldedHere = true
    },
    { immediate: true, flush: 'post' }
  )
  // 读者自己把侧栏展开了：那是他的，离开时不再动它。
  watch(
    () => nav?.unfoldSidebar.value ?? null,
    (unfold) => {
      if (!unfold && nav?.foldSidebar.value) foldedHere = false
    }
  )
  onBeforeUnmount(putBack)

  return { chosen, editor, choose }
}
