// 队列页的**分诊**那一半：状态的推与撤、详情里的四样写操作、以及它们的失败话。
//
// 分诊键落在 store，撤销条（栈深 1）是页面级的一份，所以这类写操作都收在这里，
// 由组合根转给页面。拆出来的理由见 `../useAdminQueue.ts` 的文件头。
import type { ComputedRef, Ref } from 'vue'
import type { FeedbackCard, FeedbackPriority, FeedbackStatus } from '@/cx_types'
import type { AdminQueueDeps, AdminQueueStore } from './types'

import { computed, nextTick, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import { allStatuses, statusMeta } from '@/lib/feedbackMeta'

/** 无效按键的闪底（§8 的备注：从已上线不可回退，按键无效并闪底 0.2s）。 */
const FLASH_MS = 200

/** 这一份要的：`store`、当前行、手上这一页、详情开关（`A` 键要先开详情），以及页面报
 *  回来的两个焦点回调（`A` 键的落点由 View 定）。 */
export interface AdminQueueTriageDeps {
  store: AdminQueueStore
  current: ComputedRef<FeedbackCard | null>
  items: ComputedRef<FeedbackCard[]>
  detailOpen: Ref<boolean>
  pageDeps: AdminQueueDeps
}

/** 分诊与撤销，以及详情的四样写操作。 */
export function useAdminQueueTriage(deps: AdminQueueTriageDeps) {
  const { store, current, items, detailOpen, pageDeps } = deps
  const { t } = useI18n()

  /* ---- 详情里的另外四样写操作 ---- */

  /** 指派 / 优先级 / 安全问题 / 内部备注。它们是详情那一层报上来的事件，落 store 在这儿 ——
   *  这四样**没有撤销条**（撤销是给单键误触的保险，而这四样都要先点开面板、再选一个值），
   *  所以不必像 `writeStatus` 那样往后传一层。 */
  function assign(id: string, handle: string | null) {
    void store.assign(id, handle)
  }

  function setPriority(id: string, priority: FeedbackPriority) {
    void store.setPriority(id, priority)
  }

  function setSecurity(id: string, security: boolean) {
    void store.setSecurity(id, security)
  }

  function addNote(id: string, body: string) {
    void store.addNote(id, body)
  }

  /** 写操作失败时服务端那句原话，画在详情底部那一条 alert 上。 */
  const detailWriteError = computed(() => store.error ?? '')

  /* ---- 无效按键的闪底 ---- */

  /** 无效按键的闪底。**先摘再挂**：同一个类名连着加两次，第二次不会重启动效，而人看到
   *  的是「第一次按没反应」。 */
  let flashTimer: ReturnType<typeof setTimeout> | undefined
  function flashRow(id: string) {
    const row = document.getElementById(`fbrow-${id}`) ?? document.getElementById(`ftrow-${id}`)
    if (!row) return
    row.classList.remove('qflash')
    void requestAnimationFrame(() => row.classList.add('qflash'))
    clearTimeout(flashTimer)
    flashTimer = setTimeout(() => row.classList.remove('qflash'), FLASH_MS)
  }

  function clearFlash() {
    clearTimeout(flashTimer)
  }

  /* ---- 分诊 ---- */

  /** 撤销条：栈深 1（§9.6）。`from` 是改之前的那一格 —— 撤销就是把它写回去。 */
  const undo = ref<{ id: string; from: FeedbackStatus; message: string } | null>(null)

  function statusOf(id: string): FeedbackStatus | null {
    if (store.detail?.id === id) return store.detail.status
    return items.value.find((item) => item.id === id)?.status ?? null
  }

  /** 在全部状态里的位置（梯子四级，「不修复」排最后）。合法性只按它判：目标必须**在当前
   *  位置之后**，所以「从已上线、不修复不可回退」是它的特例而不是额外规则 —— 回退和原地
   *  下键都会落进同一个分支。 */
  function rank(status: FeedbackStatus): number {
    return allStatuses(store.meta?.statuses).indexOf(status)
  }

  async function writeStatus(id: string, to: FeedbackStatus, byKey: boolean) {
    const from = statusOf(id)
    if (!from) return
    if (rank(to) <= rank(from)) {
      // 一颗没有反应的按键和「这个键坏了」长得一模一样，所以无效也要出声。
      if (byKey) flashRow(id)
      return
    }
    await store.setStatus(id, to)
    // 写失败时 `store.error` 是原话，而撤销条会变成一句假话（那条根本没改）。队列行上
    // 那一颗按钮走的是同一条路，只是它没有按键可闪。
    if (store.error) return
    undo.value = { id, from, message: t('feedback.undo.message', { status: statusMeta(to).label }) }
  }

  /** 行右侧那颗按钮的下一格。和 `AdminQueueRow` 里的表同一份 —— 它推的就是这一格。 */
  const NEXT: Record<FeedbackStatus, FeedbackStatus | null> = {
    received: 'in_progress',
    in_progress: 'resolved',
    resolved: 'deployed',
    deployed: null,
    declined: null,
  }

  function advance(id: string) {
    const from = statusOf(id)
    const to = from ? NEXT[from] : null
    if (to) void writeStatus(id, to, false)
  }

  /** 撤销条上的「撤销」。写回去的那一次不再产生新的撤销条 —— 否则 `U` 会套娃。 */
  function undoTriage() {
    const last = undo.value
    undo.value = null
    if (last) void store.setStatus(last.id, last.from)
  }

  function dismissUndo() {
    undo.value = null
  }

  /** 详情里按了那颗主按钮（`triage` 事件）。撤销条是页面级的栈，所以这条路必须经过这里。 */
  function onDetailTriage(to: FeedbackStatus) {
    const id = current.value?.id
    if (id) void writeStatus(id, to, false)
  }

  /** `M` 键和页头那个「标为已读」按钮走的是**手动**这一条：每次真发请求。
   *
   *  以前这里调的是 `markReadOnce(current.id)`（页面自己用的那个「同一条只推一次」的
   *  版本），后果是打开一条停留超过 800ms 之后再按 `M`，那一按被去重集合早退成静默
   *  空操作 —— 未读数不减、徽标不动、也没有任何提示，按的人以为键坏了。另外它先看
   *  `current`（当前光标那一行），所以在筛空了的列表上，那个「已读」按钮同样是按不动
   *  的：按钮只在 `unread > 0` 时出现，而「有没有未读」和「光标停在哪一行」是两件事。 */
  function markCurrentRead() {
    void store.markRead()
  }

  /** `A`：把焦点送进「指派」那个 combobox（§8）。**哪一个实例上有一颗这样的框**归
   *  View 管（宽屏在正文里、窄屏在抽屉里），所以落点由 View 报回来。 */
  async function focusAssignee() {
    if (!detailOpen.value) {
      if (!current.value) return
      detailOpen.value = true
      await nextTick()
    }
    pageDeps.focusAssignee()
  }

  return {
    undo,
    detailWriteError,
    onDetailTriage,
    assign,
    setPriority,
    setSecurity,
    addNote,
    statusOf,
    writeStatus,
    advance,
    undoTriage,
    dismissUndo,
    markCurrentRead,
    focusAssignee,
    clearFlash,
  }
}
