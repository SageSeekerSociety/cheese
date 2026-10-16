// 管理后台的反馈队列（`/admin/queue`，§4.1）**逻辑**的那一半：光标、详情的防抖与
// 自动已读、分诊与撤销、键盘、地址里带过来的问法、以及页面自己那四个状态。
//
// 分家的理由和 `useAdminStats` / `useAdminModels` 是同一个形状：这一页原先一份
// `<script setup>` 七百多行，取数和画法长在一起，于是页头、工具行、四态块都拿不出来
// 单独看 —— 挂在预览站里得先起假后端、把整页拉起来。现在三件事各归各位：
//
//   - 判断与接线 → 这里（页面只吃返回值，把动作报回来）；
//   - 画 → `views/admin/AdminQueuePageView.vue` 与 `components/admin/queue/*.vue`，
//     只吃 props、只往上发事件；
//   - 顶层容器 → `views/admin/AdminQueuePage.vue` 自己（它只把这里的东西转给 View）。
//
// **三件事留在这一层，不往组件里掉**（和拆之前同一个分工）：
//
//   1. **问法**（栏位 / 关键词 / 三个日期窗口）在 store 里，这里只负责把它们画出来、
//      把人的动作报回去。队列本体（`AdminQueueList` / `AdminFeedbackTable`）只认
//      `items`，拿不到写入口 —— 分诊键要落 store，所以键在这一层分发（§8 的「列表」档）。
//   2. **「手上这一条是谁」**。光标、详情、已读、撤销条都挂在同一个 id 上，所以这个
//      判断只能有一份，也就是这里。`AdminQueueDetail` 的注释把同一个理由写了一遍。
//      「指派给谁」那个搜索框的**词**也挂在这儿：详情一关就得清掉（关的时候人还在
//      输一个字的一半，那条待发的请求也不该再打出去），而「详情还开着没有」是这一层
//      的判断 —— 放在选择器里的话，它看不见详情是什么时候关的。
//   3. **四态**。加载/空/错误/筛空里，「错误」和「筛空」是页面才知道的两件事：卡片
//      只知道「我手上一条都没有」。所以两个列表组件都留了 `empty` 插槽，内容由页面
//      填 —— 填的那一件（`AdminQueueEmpty`）只认这里算出来的 `copy` / `state`。
//
// **队列与总表共用一份 `adminItems`**（§13 C-10）：切视图不重新取数。代价是两个视图
// 的排序/筛选状态互相覆盖，不做的原因写在规格里 —— 多一份列表就多一次
// `/admin/feedback` 请求。
//
// 工具行上两组控件说的是两件事，位置分组和文案都在重复这一点：栏位 / 搜索 / 日期窗口
// chip 是**服务端的问法**（换了重拉），状态页签是**在手上这一页里筛**（服务端没有按
// 状态查的参数）—— 页签右侧那句「只筛这一页」把这个口径写在面上，完整句进 `title`。
// 三个日期窗口各有可见的一颗 chip（`windowChips`）：看板 KPI 深链带着 `?since=7d`
// 进来时它是唯一的筛选指示；值在 store 里存原文、发请求时才折成日期
// （`lib/feedbackWindows.ts` —— 后端的参数是 datetime，'7d' 原样发出去是 422）。
//
// 这一份是**组合根**：跨模块的接线都在这儿 —— 谁跟谁说话、什么时候把值转给页面。五块
// 各自成篇的判断拆在 `adminQueue/` 下：手上这一页与四态（`board.ts`）、光标与详情
// （`detail.ts`）、分诊与撤销（`triage.ts`）、键盘（`keyboard.ts`）、地址与日期窗口
// （`route.ts`）。返回的那一包和拆之前一模一样，页面与单测都不用动。
import type { FeedbackStatus } from '@/cx_types'
import type { AdminQueueDeps, QueueView } from './adminQueue/types'

import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { useAdminCandidateSearch } from '@/composables/useAdminCandidateSearch'

import { useAdminQueueBoard } from './adminQueue/board'
import { useAdminQueueDetail } from './adminQueue/detail'
import { useAdminQueueKeyboard } from './adminQueue/keyboard'
import { useAdminQueueRoute } from './adminQueue/route'
import { useAdminQueueTriage } from './adminQueue/triage'

import { useFeedbackStore } from '@/stores/feedback'

export type { AdminQueueDeps, QueueView, QueueWindowKey } from './adminQueue/types'

/** 搜索两个字起（§12 第 6 条：搜索会打到无索引的四列上）。一个字不发请求，也不清掉
 *  上一次的结果 —— 列表停在那儿比闪一次「没有匹配」好。 */
const QUERY_MIN_CHARS = 2

const WIDE_QUERY = '(min-width: 1280px)'

export function useAdminQueue(deps: AdminQueueDeps) {
  const store = useFeedbackStore()

  /* ---- 状态 ---- */

  const view = ref<QueueView>('list')
  /** 「全部」不是状态，是「不过滤」。它和另外四个同住一行是因为它们互斥。 */
  const statusTab = ref<FeedbackStatus | 'all'>('all')
  /** 当前行。`null` = 还没选过任何一行 —— **默认不替人选**：一进来就把第一条画成「当前」
   *  等于替人表态，而且宽屏那一档会顺手把未读游标推走。 */
  const cursorId = ref<string | null>(null)
  /** 详情开着没有。≥1280 是整页接管（§4.4），<1280 是那个 520px 抽屉。 */
  const detailOpen = ref(false)
  /** 分诊面板展开着没有。宽屏默认展开（那里有一整栏），窄屏默认收起（收起时才是一行
   *  摘要，抽屉里那点高度要留给正文）。`Esc` 的「一次只退一层」退的就是它。 */
  const triageOpen = ref(true)

  /** 输入框里的字。**它和 `store.adminQuery` 不是同一个东西**：后者是「真的发出去过」
   *  的那一个词，前者是手上正在打的。分开的理由见 `QUERY_MIN_CHARS` 和 `clearFilters`。 */
  const draft = ref(store.adminQuery)

  /** 「指派给谁」那个框的字与候选。两份都在这儿，因为**关详情时要把词清掉**（见文件
   *  开头第 2 条）；清词那一下由下面那个 `watch(detailOpen)` 统一接住 —— `Esc`、返回
   *  键、抽屉外面的遮罩都走 `detailOpen`，不各写一遍。 */
  const {
    search: assigneeSearch,
    candidates: assigneeCandidates,
    searching: assigneeSearching,
  } = useAdminCandidateSearch()

  watch(detailOpen, (open) => {
    if (!open) assigneeSearch.value = ''
  })

  const media =
    typeof window !== 'undefined' && typeof window.matchMedia === 'function' ? window.matchMedia(WIDE_QUERY) : null
  const isWide = ref(media ? media.matches : true)

  /* ---- 接线 ---- */

  const board = useAdminQueueBoard({ store, view, statusTab, cursorId })

  const detail = useAdminQueueDetail({
    store,
    isWide,
    cursorId,
    detailOpen,
    visible: board.visible,
    cursorIndex: board.cursorIndex,
  })

  const triage = useAdminQueueTriage({
    store,
    current: board.current,
    items: board.items,
    detailOpen,
    pageDeps: deps,
  })

  const keyboard = useAdminQueueKeyboard({
    store,
    pageDeps: deps,
    detailOpen,
    triageOpen,
    current: board.current,
    undo: triage.undo,
    step: detail.step,
    undoTriage: triage.undoTriage,
    markCurrentRead: triage.markCurrentRead,
    focusAssignee: triage.focusAssignee,
    writeStatus: triage.writeStatus,
  })

  const routeApi = useAdminQueueRoute({ store, statusTab, draft, pageDeps: deps })

  /* ---- 四态上的动作 ---- */

  function reload() {
    void store.loadAdmin()
  }

  function runAction() {
    if (board.state.value === 'error') reload()
    else if (board.state.value === 'filtered') routeApi.clearFilters()
  }

  /* ---- 子件报回来的那几档状态 ---- */

  /** 视图、搜索草稿、状态页签、撤销条、详情那两档开关：都是**这一层**的状态，子件只报
   *  「人做了什么」。写入口收成函数而不是让页面各处直接赋值，理由同 `setDetailOpen` ——
   *  同一个状态被两处写时，两处说的话必须在一条路上。 */
  function setView(next: QueueView) {
    view.value = next
  }

  function setDraft(next: string) {
    draft.value = next
  }

  function setStatusTab(next: FeedbackStatus | 'all') {
    statusTab.value = next
  }

  /** 详情那两档状态的写入口。宽屏的整页接管和窄屏的抽屉是**两个容器、一份状态**，
   *  所以这里给一个入口而不是让页面各处直接赋值 —— 两份容器说的话走同一条路。 */
  function setDetailOpen(open: boolean) {
    detailOpen.value = open
  }

  function closeDetail() {
    detailOpen.value = false
  }

  function setTriageOpen(open: boolean) {
    triageOpen.value = open
  }

  /* ---- 生命周期 ---- */

  watch(draft, (value) => {
    // 已经有过的那个词不再问一遍 —— `clearFilters` 的一串动作会路过这里一次。
    if (value === store.adminQuery) return
    const q = value.trim()
    if (q.length > 0 && q.length < QUERY_MIN_CHARS) return
    store.setAdminQuery(value)
  })

  /** 光标得落在一行**真存在**的行上：筛到别的页签、或者刚才那一条被写走了（改了安全问题
   *  就从公开栏挪进安全栏）。落不回来时回到第一行，而不是留着一个不存在的 id —— 那会让
   *  `Enter` 打开一条已经不在手上的反馈。 */
  watch(board.visible, (list) => {
    if (!list.length) {
      cursorId.value = null
      detail.clearDetailTimers()
      return
    }
    if (list.some((item) => item.id === cursorId.value)) return
    cursorId.value = list[0].id
    detail.armDetail(list[0].id, false)
  })

  // 断点换档：分诊面板的默认值跟着换（宽屏展开、窄屏收起），详情那一份容器也跟着换。
  watch(isWide, (wide) => {
    triageOpen.value = wide
  })

  function onMediaChange() {
    isWide.value = media?.matches ?? true
  }

  onMounted(() => {
    media?.addEventListener('change', onMediaChange)
    triageOpen.value = isWide.value
    const loaded = routeApi.applyRouteQuery()
    if (!loaded) void store.loadAdmin()
    window.addEventListener('keydown', keyboard.onKeydown)
    window.addEventListener('pointerdown', detail.onPointerDown, true)
  })

  onBeforeUnmount(() => {
    media?.removeEventListener('change', onMediaChange)
    window.removeEventListener('keydown', keyboard.onKeydown)
    window.removeEventListener('pointerdown', detail.onPointerDown, true)
    detail.clearDetailTimers()
    triage.clearFlash()
  })

  /* ---- 页面读的那些 ---- */

  /** 队列脚那一条要不要说「在搜索的结果里」—— 页面自己只拿这个布尔。 */
  const scoped = computed(() => !!store.adminQuery.trim())

  return {
    // 详情
    isWide,
    detailOpen,
    setDetailOpen,
    closeDetail,
    triageOpen,
    setTriageOpen,
    detail: computed(() => store.detail),
    detailLoading: computed(() => store.detailLoading),
    detailError: board.detailError,
    detailWriteError: triage.detailWriteError,
    /** 服务端 meta 那份状态梯子，转给详情画分诊面板。 */
    statuses: computed(() => store.meta?.statuses),
    onDetailTriage: triage.onDetailTriage,
    assign: triage.assign,
    setPriority: triage.setPriority,
    setSecurity: triage.setSecurity,
    addNote: triage.addNote,
    // 「指派给谁」的候选搜索
    assigneeSearch,
    setAssigneeSearch: (v: string) => {
      assigneeSearch.value = v
    },
    assigneeCandidates,
    assigneeSearching,
    // 页头
    unread: board.unread,
    markCurrentRead: triage.markCurrentRead,
    reload,
    view,
    setView,
    // 工具行
    adminTab: computed(() => store.adminTab),
    laneOptions: board.laneOptions,
    selectLane: routeApi.selectLane,
    draft,
    setDraft,
    windowChips: routeApi.windowChips,
    clearWindow: routeApi.clearWindow,
    statusTab,
    setStatusTab,
    tabOptions: board.tabOptions,
    // 列表
    visible: board.visible,
    cursorId,
    cursorIndex: board.cursorIndex,
    showSkeleton: board.showSkeleton,
    showStatusWord: board.showStatusWord,
    onActiveIndex: detail.onActiveIndex,
    onTableActivate: detail.onTableActivate,
    openItem: detail.openItem,
    advance: triage.advance,
    // 四态
    state: board.state,
    copy: board.copy,
    listError: board.listError,
    runAction,
    // 脚
    scoped,
    hasPrev: computed(() => store.adminHasPrev),
    hasNext: computed(() => store.adminHasNext),
    prev: () => store.adminPrev(),
    next: () => store.adminNext(),
    // 撤销
    undo: triage.undo,
    undoTriage: triage.undoTriage,
    dismissUndo: triage.dismissUndo,
  }
}
