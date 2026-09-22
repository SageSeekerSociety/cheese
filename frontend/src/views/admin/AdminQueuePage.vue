<script setup lang="ts">
import type { FeedbackCard, FeedbackStatus } from '@/cx_types'
import type { AdminTab } from '@/stores/feedback'

import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'

import AdminFeedbackTable from '@/components/admin/AdminFeedbackTable.vue'
import AdminQueueDetail from '@/components/admin/AdminQueueDetail.vue'
import AdminQueueList from '@/components/admin/AdminQueueList.vue'
import UndoStrip from '@/components/admin/UndoStrip.vue'
import AdminFeedbackDetailDrawer from '@/components/feedback/AdminFeedbackDetailDrawer.vue'
import { statusMeta } from '@/lib/feedbackMeta'
import { useFeedbackStore } from '@/stores/feedback'

// 管理后台的反馈队列（`/admin/queue`，§4.1）。**这一页是这一轮的主屏** —— 管理侧的
// 全部日常动作都在这上面发生，详情、看板、成员都是它的支线。
//
// 三件事在这一层，不往组件里掉：
//
//   1. **问法**（栏位 / 关键词 / 三个日期窗口）在 store 里，这一层只负责把它们画出来、
//      把人的动作报回去。队列本体（`AdminQueueList` / `AdminFeedbackTable`）只认
//      `items`，拿不到写入口 —— 分诊键要落 store，所以键在这一层分发（§8 的「列表」档）。
//   2. **「手上这一条是谁」**。光标、详情、已读、撤销条都挂在同一个 id 上，所以这个
//      判断只能有一份，也就是这里。`AdminQueueDetail` 的注释把同一个理由写了一遍。
//   3. **四态**。加载/空/错误/筛空里，「错误」和「筛空」是页面才知道的两件事：卡片
//      只知道「我手上一条都没有」。所以两个列表组件都留了 `empty` 插槽，内容在这里。
//
// **队列与总表共用一份 `adminItems`**（§13 C-10）：切视图不重新取数。代价是两个视图
// 的排序/筛选状态互相覆盖，不做的原因写在规格里 —— 多一份列表就多一次
// `/admin/feedback` 请求。
//
// 已知偏差两条，都写在出现的地方：状态页签是**在手上这一页里筛**（服务端没有按状态查
// 的参数），三个日期窗口没有可见的筛选指示（看板点进来时带着它，这一层不画 chip）。
defineOptions({ name: 'AdminQueuePage' })

const store = useFeedbackStore()
const { t } = useI18n()
const route = useRoute()

/* ---- 常量 ---- */

/** 光标落定后多久去拉详情（F-06）。200ms 里按十次 `j` 只该产生一次请求 —— 中间的九次
 *  都在把这一颗定时器重排。 */
const DETAIL_DEBOUNCE_MS = 150

/** 详情真的画出来之后再等多久推已读游标（F-13）。800ms 是「人确实看了这一条」和
 *  「光标路过」之间的距离。 */
const READ_DELAY_MS = 800

/** 无效按键的闪底（§8 的备注：从已上线不可回退，按键无效并闪底 0.2s）。 */
const FLASH_MS = 200

/** 搜索两个字起（§12 第 6 条：搜索会打到无索引的四列上）。一个字不发请求，也不清掉
 *  上一次的结果 —— 列表停在那儿比闪一次「没有匹配」好。 */
const QUERY_MIN_CHARS = 2

/** 「指针刚刚点过队列」的时间窗。**行只报 `activate`，鼠标点击和 `j` 在这一条事件上
 *  长得一模一样**，而两者的意思不同：「点一行」是「打开它」，「按一下 `j`」只是「往下
 *  看一行」。窄屏上把后者也当成打开，就是每按一次 `j` 弹一次抽屉。所以靠时间窗分开。 */
const POINTER_WINDOW_MS = 400

const WIDE_QUERY = '(min-width: 1280px)'

/** 分诊键落在梯子的第几格（§8 表里的 `1` 进行中 / `2` 已解决 / `3` 已上线）。存的是
 *  **下标**而不是状态名：梯子由服务端给（`store.statusLadder`），下标才是那三条键的
 *  意思，名字会跟着服务端变。 */
const TRIAGE_SLOTS: Record<string, number> = { 1: 1, 2: 2, 3: 3 }

/** 旧的 `/admin/feedback?tab=...` 书签还认这几个值（服务端的 `ADMIN_TABS`）。写成常量
 *  是因为 store 只导出了类型，没导出这份元组。 */
const ADMIN_TABS: AdminTab[] = ['public', 'private', 'agent', 'security']

/* ---- 状态 ---- */

type View = 'list' | 'table'

/** 分段控件的两个档。写成常量是因为模板里那一段要在 `v-for` 上判等。 */
const VIEWS: View[] = ['list', 'table']

const view = ref<View>('list')
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

/** 撤销条：栈深 1（§9.6）。`from` 是改之前的那一格 —— 撤销就是把它写回去。 */
const undo = ref<{ id: string; from: FeedbackStatus; message: string } | null>(null)

/** 输入框里的字。**它和 `store.adminQuery` 不是同一个东西**：后者是「真的发出去过」
 *  的那一个词，前者是手上正在打的。分开的理由见 `QUERY_MIN_CHARS` 和 `clearFilters`。 */
const draft = ref(store.adminQuery)

const searchEl = ref<HTMLInputElement | null>(null)
const detailRef = ref<InstanceType<typeof AdminQueueDetail> | null>(null)

const media =
  typeof window !== 'undefined' && typeof window.matchMedia === 'function' ? window.matchMedia(WIDE_QUERY) : null
const isWide = ref(media ? media.matches : true)

/* ---- 手上这一页 ---- */

const tabs = computed<(FeedbackStatus | 'all')[]>(() => ['all', ...store.statusLadder])
const items = computed(() => store.adminItems)

/** 状态页签**在手上这一页里筛**（见文件末尾的代价）。`all` 那一档不复制数组：返回的
 *  就是 store 那一份，`v-for` 的 key 也因此不重算。 */
const visible = computed(() =>
  statusTab.value === 'all' ? items.value : items.value.filter((item) => item.status === statusTab.value)
)

/** 混合列表里要写状态字；单状态的页签下它是一列重复四遍的字，省掉（§5.2）。 */
const showStatusWord = computed(() => statusTab.value === 'all')

const cursorIndex = computed(() => {
  const at = visible.value.findIndex((item) => item.id === cursorId.value)
  // 找不到（还没选、或那一条被筛走了）时回到第一行：没有当前行的队列 Tab 不进来。
  return at >= 0 ? at : visible.value.length ? 0 : -1
})

const current = computed<FeedbackCard | null>(() => (cursorIndex.value >= 0 ? visible.value[cursorIndex.value] : null))

const unread = computed(() => store.counts.unread ?? 0)

/** 首次加载才给骨架。已经有内容时换骨架会让整页闪一下，那是比「旧内容多停半秒」更糟
 *  的手感 —— 所以 `store.adminLoading` 只在手上一条都没有时才算数。 */
const showSkeleton = computed(() => store.adminLoading && !items.value.length)

/** 队列自己的读失败。**列表空着才算**：手上还有一页旧数据时，一次失败的刷新不该把
 *  那一页换成一句错误 —— 它只是不新鲜了。 */
const listError = computed(() => (!items.value.length && !store.adminLoading ? store.error : null))

/** 详情拉失败的原话。**只在「我要的那条」回来之后才算数**：`store.error` 是共用的，
 *  一次失败的列表刷新不该在详情里画出一句读失败。 */
const detailError = computed(() =>
  store.detailId && store.detailId === current.value?.id && !store.detailLoading && !store.detail ? store.error : null
)

/** 有筛选条件吗。三个日期窗口也算 —— 看板的数字点进来时带的就是它们，那时结果空了要说
 *  「没有符合条件的反馈」而不是「还没有人提交反馈」。 */
const hasFilter = computed(
  () =>
    statusTab.value !== 'all' ||
    !!store.adminQuery.trim() ||
    !!(store.adminSince || store.adminResolvedSince || store.adminDeployedSince)
)

/** 四态里的第三态。空 = 一条都没有且没有任何筛选；筛空 = 有筛选但零命中。**两者必须分开**：
 *  「平台还没有反馈」和「你要找的那条被筛掉了」是两件事，前者会让人以为平台坏了。 */
const state = computed<'error' | 'filtered' | 'empty' | null>(() => {
  if (visible.value.length) return null
  if (listError.value) return 'error'
  if (items.value.length) return 'filtered'
  return hasFilter.value ? 'filtered' : 'empty'
})

/** 四态文案。**键名逐字写全**，不做 `` t(`${ns}.error.title`) `` 那种拼接：i18n 闸门
 *  是按源码里的字面量扫引用的，拼出来的键在它眼里等于没人用（`catalog.spec.ts`）。 */
const copy = computed(() => {
  if (state.value === 'error') {
    return view.value === 'list'
      ? {
          title: t('feedback.queue.error.title'),
          desc: t('feedback.queue.error.desc'),
          action: t('feedback.queue.error.retry'),
        }
      : {
          title: t('feedback.table.error.title'),
          desc: t('feedback.table.error.desc'),
          action: t('feedback.table.error.retry'),
        }
  }
  if (state.value === 'filtered') {
    return view.value === 'list'
      ? {
          title: t('feedback.queue.filtered.title'),
          desc: t('feedback.queue.filtered.desc'),
          action: t('feedback.queue.filtered.clear'),
        }
      : {
          title: t('feedback.table.filtered.title'),
          desc: t('feedback.table.filtered.desc'),
          action: t('feedback.table.filtered.clear'),
        }
  }
  return view.value === 'list'
    ? { title: t('feedback.queue.empty.title'), desc: t('feedback.queue.empty.desc'), action: '' }
    : { title: t('feedback.table.empty.title'), desc: t('feedback.table.empty.desc'), action: '' }
})

/* ---- 详情：防抖 + 自动已读 ---- */

let detailTimer: ReturnType<typeof setTimeout> | undefined
let readTimer: ReturnType<typeof setTimeout> | undefined
let armedId: string | null = null

function clearDetailTimers() {
  clearTimeout(detailTimer)
  clearTimeout(readTimer)
  detailTimer = undefined
  readTimer = undefined
}

/** 详情请求的**代次**：每次挪光标 +1。
 *
 *  150ms 防抖只把「同一段连按」并成一次请求，它挡不住的是**光标先动了、请求还没发**
 *  那一段时间：连按 `j` 时那支计时器每次都被重置，于是行上的选中、头上的编号一直在
 *  往前走，而详情区挂的还是最早那一条的正文 —— 那正是 F-06 要丢掉的「过期的东西」。
 *
 *  为什么不靠 store 里 `detailId` 那一条判据：它挡的是「响应比**新请求**晚到」，挡不住
 *  「响应比**光标**晚到」—— 后者发生在防抖窗口里，那期间新请求根本还没发出去，
 *  `detailId` 还是旧的，旧响应会被当成有效的收下。两件事都要有，缺一个就漏一种。 */
let detailSeq = 0

/** 光标记到哪一条上、`markRead` 要不要跟着推。**请求归请求，已读归已读**：光标路过
 *  一条不等于看过它，所以列表里按 `j` 走一趟不该把整个未读数清零。 */
function armDetail(id: string | null, markRead: boolean) {
  clearDetailTimers()
  armedId = id
  // 代次 +1：从那一条还在路上的请求，到下面 `.then()` 里那次自动已读，全部作废。
  const seq = ++detailSeq
  // **作废在挪光标这一刻就发生**，不等防抖。连按 `j` 时那支计时器每次都被重置，等它
  // 到期等于让详情区整段连按期间挂着上一条的正文，而左侧选中的行、头上的编号已经是
  // 新的了（F-06）。先画回骨架，说的是实话。
  store.invalidateDetail(id)
  if (!id) return
  detailTimer = setTimeout(() => {
    detailTimer = undefined
    if (seq !== detailSeq) return
    void store.loadAdminDetail(id).then(() => {
      if (seq !== detailSeq) return
      // 拉失败、或这期间人已经挪到别处：不算「看过」，游标不动。
      if (armedId !== id || store.detailId !== id || !store.detail) return
      if (!markRead) return
      readTimer = setTimeout(() => {
        readTimer = undefined
        if (armedId === id) void store.markReadOnce(id)
      }, READ_DELAY_MS)
    })
  }, DETAIL_DEBOUNCE_MS)
}

/* ---- 光标 ---- */

let pointerAt = 0

function pointerJustUsed(): boolean {
  return Date.now() - pointerAt < POINTER_WINDOW_MS
}

function select(id: string | null, opts: { open?: boolean; pointer?: boolean } = {}) {
  const changed = id !== cursorId.value
  cursorId.value = id
  // 宽屏那一档没有「选中但看不到」这回事（整页接管），所以光标一挪就等于进了详情；
  // 窄屏上抽屉要人明说才开，所以只有指针点的那一次和 `Enter` 才算。
  armDetail(id, !!id && (isWide.value ? changed || !!opts.open : !!opts.open || !!opts.pointer))
  if (!id) return
  if (opts.open || opts.pointer || (isWide.value && changed)) detailOpen.value = true
}

/** `j` / `k` / `H` 挪光标。挪完把焦点也带过去：不然连按 `H` 之后焦点还留在旧行上，
 *  下一颗键又落回旧行（`AdminQueueList` 的 `move` 是同一个理由）。 */
function step(delta: number, open = false) {
  const to = cursorIndex.value + delta
  if (to < 0 || to >= visible.value.length) return
  select(visible.value[to].id, { open })
  void nextTick(() => focusRow(visible.value[to].id))
}

/** 行元素按 id 找，不问组件要 ref：行 id 的约定（`fbrow-` / `ftrow-`）本来就是两个
 *  列表组件和这一层共用的（`aria-activedescendant` 也按它找）。 */
function focusRow(id: string) {
  const row = document.getElementById(`fbrow-${id}`) ?? document.getElementById(`ftrow-${id}`)
  row?.querySelector<HTMLElement>('.fbrow__link')?.focus()
}

/** 两个列表报上来的「光标挪到第几行」。指针刚点过就按「点开」处理，见 `POINTER_WINDOW_MS`。 */
function onActiveIndex(index: number) {
  const item = visible.value[index]
  if (item) select(item.id, { pointer: pointerJustUsed() })
}

/** 总表报上来的「选中这一行」。列表那边是 `update:activeIndex`（它按行号报），总表报
 *  的是 id —— 两者的形状不同是因为各自的光标不一样（roving tabindex vs 选中行）。 */
function onTableActivate(id: string) {
  select(id, { pointer: pointerJustUsed() })
}

/** `Enter`（两个列表都只在这一条路上发 `open`）。行上那颗按钮发的是 `advance` ——
 *  推状态和看详情是两件事，混成一个会让人按一下就改掉一条状态。 */
function openItem(id: string) {
  select(id, { open: true })
}

/** 详情里按了那颗主按钮（`triage` 事件）。**状态从 `triage` 走、别的写操作直接落 store**
 *  的那条分工写在 `AdminQueueDetail` 里；撤销条是页面级的栈，所以这条路必须经过这里。 */
function onDetailTriage(to: FeedbackStatus) {
  const id = current.value?.id
  if (id) void writeStatus(id, to, false)
}

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

/* ---- 分诊 ---- */

function statusOf(id: string): FeedbackStatus | null {
  if (store.detail?.id === id) return store.detail.status
  return items.value.find((item) => item.id === id)?.status ?? null
}

/** 梯子上的位置。合法性只按它判：目标必须**在当前位置之后**，所以「从已上线不可回退」
 *  是它的一个特例而不是一条额外规则 —— 回退和原地下键都会落进同一个分支。 */
function rank(status: FeedbackStatus): number {
  return store.statusLadder.indexOf(status)
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

/** `M` 键和页头那个「标记为已读」按钮走的是**手动**这一条：每次真发请求。
 *
 *  以前这里调的是 `markReadOnce(current.id)`（页面自己用的那个「同一条只推一次」的
 *  版本），后果是打开一条停留超过 800ms 之后再按 `M`，那一按被去重集合早退成静默
 *  空操作 —— 未读数不减、徽标不动、也没有任何提示，按的人以为键坏了。另外它先看
 *  `current`（当前光标那一行），所以在筛空了的列表上，那个「已读」按钮同样是按不动
 *  的：按钮只在 `unread > 0` 时出现，而「有没有未读」和「光标停在哪一行」是两件事。 */
function markCurrentRead() {
  void store.markRead()
}

/** `A`：把焦点送进「指派」那个 combobox（§8）。宽屏那一档那个实例上有 `focusAssignee`；
 *  抽屉里的那个没有转发它（`AdminFeedbackDetailDrawer` 的 emits 里没有这一条），所以
 *  退一步在 DOM 上找 —— 详情里只有这一颗 combobox。 */
async function focusAssignee() {
  if (!detailOpen.value) {
    if (!current.value) return
    detailOpen.value = true
    await nextTick()
  }
  if (detailRef.value?.focusAssignee) {
    detailRef.value.focusAssignee()
    return
  }
  document.querySelector<HTMLInputElement>('.v-navigation-drawer .v-field input')?.focus()
}

/* ---- 四态上的动作 ---- */

function reload() {
  void store.loadAdmin()
}

/** 「清除筛选」。关键词和状态页签，加上看板带进来的那三段窗口 —— 按钮写着「清除筛选」，
 *  就该把筛选清干净；只清一半的话，人点完还是一屏空的，会以为按钮坏了。
 *
 *  先改 `draft` 再调 `clearAdminQuery()`：watch 是异步的，等它醒过来时 `store.adminQuery`
 *  已经是空串了，那一条守卫会把它挡掉，于是只有一次请求。 */
function clearFilters() {
  statusTab.value = 'all'
  draft.value = ''
  store.clearAdminQuery()
  if (store.adminSince) store.setAdminSince(null)
  if (store.adminResolvedSince) store.setAdminResolvedSince(null)
  if (store.adminDeployedSince) store.setAdminDeployedSince(null)
}

function runAction() {
  if (state.value === 'error') reload()
  else if (state.value === 'filtered') clearFilters()
}

/* ---- 键盘 ---- */

function isTyping(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null
  if (!el || !el.tagName) return false
  return el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.tagName === 'SELECT' || el.isContentEditable
}

/** 焦点是不是落在**某一个自己收方向键的格子**里。
 *
 *  两个视图各有一套 `j`/`k` 处理（`AdminQueueList` / `AdminFeedbackTable`，roving
 *  tabindex 归它们管），页面这一层只在格子已经不在 DOM 里的时候接手 —— 宽屏打开
 *  详情时整块被 `v-if` 换掉，就是那一种。
 *
 *  认的是一个**两个格子都标了的属性**，不是类名。以前这里写的是 `.qlist`，于是
 *  表格视图（根类名是 `.aft`）整个漏在外面：窄屏打开抽屉、焦点在表格里时，表格的
 *  `@keydown` 和 window 上这一个同时命中，一次 `j` 前进两行、光标越过一行。两个
 *  视图写了两套类名，守卫只认了一套 —— 属性说的是「这一片子树自己收方向键」，
 *  新增第三个视图时标上它就有同样的待遇，改类名也不会再悄悄把守卫弄丢。 */
function gridOwnsArrows(): boolean {
  return !!document.activeElement?.closest('[data-owns-arrow-keys]')
}

/** `Esc` 逐层后退（§8）：一次只退一层，先分诊面板、再详情。 */
function onEscape() {
  if (!detailOpen.value) return
  if (triageOpen.value) {
    triageOpen.value = false
    return
  }
  detailOpen.value = false
}

/** 队列页这一层的键（§8 的「列表 / 详情」两档）：
 *
 *  - 分诊那一批（`1/2/3/H/U/A/M`）**不依赖抽屉开着**（F-12）—— 窄屏上键盘用户不该
 *    被困在抽屉里，所以它们挂在 window 上，焦点在哪儿都算。
 *  - `j`/`k` 在列表自己手里（roving tabindex），这里**只在详情打开、列表已经不在
 *    DOM 里的时候**接手：宽屏接管之后连按 `j` 能一条条往下看，正是 F-06 防抖存在的
 *    理由。列表还在时两边都收一次键，会一次跳两行。
 */
function onKeydown(event: KeyboardEvent) {
  if (event.metaKey || event.ctrlKey || event.altKey) return

  if (isTyping(event.target)) {
    // 输入框里的 `Esc` 先退焦点：不然在搜索框里按 `Esc` 会直接把详情关掉，而人的意思是
    // 「我不打了」。
    if (event.key === 'Escape') (event.target as HTMLElement).blur()
    return
  }

  if (event.key === '/') {
    event.preventDefault()
    searchEl.value?.focus()
    return
  }
  if (event.key === 'Escape') {
    onEscape()
    return
  }

  const detailHoldsFocus = detailOpen.value && !gridOwnsArrows()

  if (event.key === 'j' || event.key === 'ArrowDown') {
    if (!detailHoldsFocus) return
    event.preventDefault()
    step(1)
    return
  }
  if (event.key === 'k' || event.key === 'ArrowUp') {
    if (!detailHoldsFocus) return
    event.preventDefault()
    step(-1)
    return
  }

  if (event.key === 'h' || event.key === 'H') {
    // 「零步数」：状态不动，只往前挪一行 —— 扫一遍队列时不必每按一次都改点什么。
    if (!current.value) return
    event.preventDefault()
    step(1)
    return
  }
  if (event.key === 'u' || event.key === 'U') {
    if (!undo.value) return
    event.preventDefault()
    undoTriage()
    return
  }
  if (event.key === 'm' || event.key === 'M') {
    markCurrentRead()
    return
  }
  if (event.key === 'a' || event.key === 'A') {
    void focusAssignee()
    return
  }

  const slot = TRIAGE_SLOTS[event.key]
  if (slot !== undefined) {
    const to = store.statusLadder[slot]
    if (!to || !current.value) return
    event.preventDefault()
    void writeStatus(current.value.id, to, true)
  }
}

/* ---- 地址里带过来的问法 ---- */

/** 看板那几个数字点进来时带的就是这几个（§6.3），旧的 `/admin/feedback?tab=...` 书签
 *  带的也是。返回「有没有已经因此拉过一次」—— 设置器自己会重新取数，这一层就不必再
 *  补一枪，否则一次深链会连打三四个请求。
 *
 *  `status` 不是服务端的参数，它落在状态页签上（在手上这一页里筛）；`assigned` /
 *  `unread` / `hot` 这一层不认识，就不装作认识。 */
function applyRouteQuery(): boolean {
  const query = route.query
  let loaded = false

  const tab = typeof query.tab === 'string' ? (query.tab as AdminTab) : null
  if (tab && ADMIN_TABS.includes(tab) && store.adminTab !== tab) {
    store.setAdminTab(tab)
    loaded = true
  }

  const status = typeof query.status === 'string' ? query.status : null
  if (status && store.statusLadder.includes(status as FeedbackStatus)) statusTab.value = status as FeedbackStatus

  const windows: [string, string | null, (v: string | null) => void][] = [
    ['since', store.adminSince, (v) => store.setAdminSince(v)],
    ['resolved_since', store.adminResolvedSince, (v) => store.setAdminResolvedSince(v)],
    ['deployed_since', store.adminDeployedSince, (v) => store.setAdminDeployedSince(v)],
  ]
  for (const [key, current, set] of windows) {
    const value = typeof query[key] === 'string' ? (query[key] as string) : null
    if (value && current !== value) {
      set(value)
      loaded = true
    }
  }

  const search = typeof query.q === 'string' ? query.q : null
  if (search && search !== store.adminQuery) {
    draft.value = search
    store.setAdminQuery(search)
    loaded = true
  }

  return loaded
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
watch(visible, (list) => {
  if (!list.length) {
    cursorId.value = null
    clearDetailTimers()
    return
  }
  if (list.some((item) => item.id === cursorId.value)) return
  cursorId.value = list[0].id
  armDetail(list[0].id, false)
})

// 断点换档：分诊面板的默认值跟着换（宽屏展开、窄屏收起），详情那一份容器也跟着换。
watch(isWide, (wide) => {
  triageOpen.value = wide
})

function onMediaChange() {
  isWide.value = media?.matches ?? true
}

function onPointerDown() {
  pointerAt = Date.now()
}

onMounted(() => {
  media?.addEventListener('change', onMediaChange)
  triageOpen.value = isWide.value
  const loaded = applyRouteQuery()
  if (!loaded) void store.loadAdmin()
  window.addEventListener('keydown', onKeydown)
  window.addEventListener('pointerdown', onPointerDown, true)
})

onBeforeUnmount(() => {
  media?.removeEventListener('change', onMediaChange)
  window.removeEventListener('keydown', onKeydown)
  window.removeEventListener('pointerdown', onPointerDown, true)
  clearDetailTimers()
  clearTimeout(flashTimer)
})
</script>

<template>
  <div class="qpage">
    <!-- 宽屏的详情是**整页接管**（§4.4：200 导航 + 440 左栏 + 760 右栏）。队列行本身
         就要 1100px，两个并排在任何常见视口里都塞不下 —— 塞得下的那种宽度下，
         `AdminQueueDetail` 自己那条 1280 媒体查询又已经把它拆成两栏了。 -->
    <AdminQueueDetail
      v-if="isWide && detailOpen"
      ref="detailRef"
      :item="store.detail"
      :loading="store.detailLoading"
      :error="detailError"
      :triage-open="triageOpen"
      @close="detailOpen = false"
      @update:triage-open="triageOpen = $event"
      @triage="onDetailTriage"
    />

    <template v-else>
      <div class="qpage__inner">
        <header class="qpage__head">
          <h1 class="t-page-title qpage__title">{{ t('feedback.queue.label') }}</h1>

          <div class="qpage__head-tools">
            <!-- 未读数。F-13 修的就是它：这个数以前没有人清零，也没有一处模板读它。 -->
            <span v-if="unread > 0" class="qpage__badge t-num" aria-live="polite">
              {{ t('feedback.queue.unread', { n: unread }) }}
            </span>
            <v-btn
              v-if="unread > 0"
              variant="text"
              size="small"
              :title="t('notifications.common.markAsRead')"
              @click="markCurrentRead"
            >
              {{ t('notifications.common.markAsRead') }}
            </v-btn>

            <button
              type="button"
              class="qpage__icon-btn"
              :aria-label="t('feedback.queue.refresh')"
              :title="t('feedback.queue.refresh')"
              @click="reload"
            >
              <v-icon icon="mdi-refresh" size="16" aria-hidden="true" />
            </button>

            <!-- 视图切换（F-05）：24px 高，落在页头右上角。**切换不重新取数** —— 两个
                 视图读的是同一份 `adminItems`（§13 C-10）。 -->
            <div class="qpage__seg" role="group" :aria-label="t('feedback.queue.label')">
              <button
                v-for="option in VIEWS"
                :key="option"
                type="button"
                class="qpage__seg-btn"
                :class="{ 'qpage__seg-btn--on': view === option }"
                :aria-pressed="view === option"
                @click="view = option"
              >
                {{ option === 'list' ? t('feedback.queue.view.list') : t('feedback.queue.view.table') }}
              </button>
            </div>
          </div>
        </header>

        <!-- 工具行 48px：搜索 260px + 五个状态页签。 -->
        <div class="qpage__tools">
          <div class="qpage__search">
            <v-icon icon="mdi-magnify" size="16" class="qpage__search-icon" aria-hidden="true" />
            <input
              ref="searchEl"
              v-model="draft"
              type="text"
              class="qpage__search-input"
              :placeholder="t('feedback.queue.search.placeholder')"
              :aria-label="t('feedback.queue.search.placeholder')"
              autocomplete="off"
              spellcheck="false"
            />
          </div>

          <div class="qpage__tabs" role="radiogroup" :aria-label="t('feedback.queue.label')">
            <button
              v-for="tab in tabs"
              :key="tab"
              type="button"
              class="qpage__tab"
              :class="{ 'qpage__tab--on': statusTab === tab }"
              role="radio"
              :aria-checked="statusTab === tab"
              @click="statusTab = tab"
            >
              {{ tab === 'all' ? t('feedback.queue.tab.all') : statusMeta(tab).label }}
            </button>
          </div>
        </div>

        <!-- 队列与总表**同时只挂一个**（§8 末：视图切换时解绑），否则同一个 `j` 会被两个
             `role="grid"` 各收一次。 -->
        <AdminQueueList
          v-if="view === 'list'"
          :items="visible"
          :active-index="cursorIndex"
          :loading="showSkeleton"
          :show-status-word="showStatusWord"
          @update:active-index="onActiveIndex"
          @advance="advance"
          @open="openItem"
        >
          <template #empty>
            <div class="qpage__state" :title="state === 'error' && listError ? listError : undefined">
              <p class="qpage__state-title">{{ copy.title }}</p>
              <p class="qpage__state-desc">{{ copy.desc }}</p>
              <button v-if="copy.action" type="button" class="qpage__state-btn" @click="runAction">
                {{ copy.action }}
              </button>
            </div>
          </template>

          <template #foot>
            <span v-if="store.adminQuery.trim()" class="qpage__scope">{{ t('feedback.queue.search.scope') }}</span>
            <span class="qpage__foot-spacer" />
            <span class="qpage__foot-count t-num">{{ t('feedback.queue.foot.rows', { n: visible.length }) }}</span>
            <span v-if="!store.adminHasNext" class="qpage__foot-end">· {{ t('feedback.queue.foot.end') }}</span>
            <button v-if="store.adminHasPrev" type="button" class="qpage__pager" @click="store.adminPrev()">
              {{ t('feedback.queue.pager.prev') }}
            </button>
            <button v-if="store.adminHasNext" type="button" class="qpage__pager" @click="store.adminNext()">
              {{ t('feedback.queue.pager.next') }}
            </button>
          </template>
        </AdminQueueList>

        <AdminFeedbackTable
          v-else
          :items="visible"
          :active-id="cursorId"
          :loading="showSkeleton"
          @activate="onTableActivate"
          @open="openItem"
        >
          <template #empty>
            <div class="qpage__state" :title="state === 'error' && listError ? listError : undefined">
              <p class="qpage__state-title">{{ copy.title }}</p>
              <p class="qpage__state-desc">{{ copy.desc }}</p>
              <button v-if="copy.action" type="button" class="qpage__state-btn" @click="runAction">
                {{ copy.action }}
              </button>
            </div>
          </template>
        </AdminFeedbackTable>

        <!-- 列表脚 40px。总表没有脚（`AdminFeedbackTable` 的契约里没有这条插槽），所以
             翻页只在队列视图里有 —— 契约不在这里改，记在交付说明里。 -->
        <div v-if="view === 'table'" class="qpage__foot">
          <span v-if="store.adminQuery.trim()" class="qpage__scope">{{ t('feedback.queue.search.scope') }}</span>
          <span class="qpage__foot-spacer" />
          <span class="qpage__foot-count t-num">{{ t('feedback.queue.foot.rows', { n: visible.length }) }}</span>
          <span v-if="!store.adminHasNext" class="qpage__foot-end">· {{ t('feedback.queue.foot.end') }}</span>
          <button v-if="store.adminHasPrev" type="button" class="qpage__pager" @click="store.adminPrev()">
            {{ t('feedback.queue.pager.prev') }}
          </button>
          <button v-if="store.adminHasNext" type="button" class="qpage__pager" @click="store.adminNext()">
            {{ t('feedback.queue.pager.next') }}
          </button>
        </div>
      </div>
    </template>

    <!-- <1280：详情是那个 520px 的抽屉（§4.4「右栏变 520px 抽屉」）。它在两种宽度下
         都是同一个组件：`AdminQueueDetail` 自己那条 1280 媒体查询按**视口**分档，所以
         抽屉只在视口也小于 1280 时才会走到单栏形态 —— 这也正是它只在窄屏出现的理由。 -->
    <AdminFeedbackDetailDrawer
      v-if="!isWide"
      :open="detailOpen"
      :item="store.detail"
      :loading="store.detailLoading"
      :error="detailError"
      :triage-open="triageOpen"
      @update:open="detailOpen = $event"
      @update:triage-open="triageOpen = $event"
      @triage="onDetailTriage"
    />

    <UndoStrip v-if="undo" :message="undo.message" @undo="undoTriage" @dismiss="undo = null" />
  </div>
</template>

<style scoped>
.qpage {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  background: var(--canvas);
}

/* 内容列锁 1100（§4.1 的算式就是从它来的：16 + 4 + 12 + F + 16 + 116 + 16 + 88 + 16 +
   B + 20 = 1100）。居中而不是靠左：这一页的右边没有东西，靠左会让 1440 与 1920 两种
   视口下的行宽差出 480px。 */
.qpage__inner {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  width: 100%;
  max-width: var(--page-w-wide);
  min-height: 0;
  margin: 0 auto;
}

.qpage__head {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 12px;
  height: 56px;
  padding: 0 24px;
  border-bottom: 1px solid var(--line-2);
}

.qpage__title {
  flex: 1 1 auto;
  overflow: hidden;
  min-width: 0;
  margin: 0;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.qpage__head-tools {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 8px;
}

.qpage__badge {
  display: inline-flex;
  align-items: center;
  height: 20px;
  padding: 0 8px;
  background: var(--fill-2);
  border-top-left-radius: var(--radius-sm);
  border-top-right-radius: var(--radius-sm);
  border-bottom-right-radius: var(--radius-sm);
  border-bottom-left-radius: var(--radius-sm);
  color: var(--ink);
  font-size: 12px;
  font-weight: 600;
  line-height: var(--lh-12);
  white-space: nowrap;
}

.qpage__icon-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  padding: 0;
  background: transparent;
  border: 0;
  border-top-left-radius: var(--radius-sm);
  border-top-right-radius: var(--radius-sm);
  border-bottom-right-radius: var(--radius-sm);
  border-bottom-left-radius: var(--radius-sm);
  color: var(--muted);
  cursor: pointer;
  transition:
    background-color 0.12s ease,
    color 0.12s ease;
}

.qpage__icon-btn:hover {
  background: var(--fill);
  color: var(--text);
}

/* 分段控件：24px 高的轨道（§4.1 的 24px 是铁的），滑块**占满整个高度**，两端各留
   4px。早先写的是「24px 轨道 + 20px 滑块」，那让轨道上下各多出 2px 内边距 —— 2 不在
   间距的尺子上（§15 第 27 条）。滑块顶到边之后，那 2px 也就不存在了。
   选中态是**中性**的（`--surface` 底 + 1px `--line`），不是琥珀 —— 队列是全站唯一一处
   amber 数等于 0 的视图（§7.4）。 */
.qpage__seg {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 24px;
  padding: 0 4px;
  background: var(--fill);
  border-top-left-radius: var(--radius-sm);
  border-top-right-radius: var(--radius-sm);
  border-bottom-right-radius: var(--radius-sm);
  border-bottom-left-radius: var(--radius-sm);
}

.qpage__seg-btn {
  height: 24px;
  padding: 0 12px;
  background: transparent;
  border: 1px solid transparent;
  border-top-left-radius: var(--radius-sm);
  border-top-right-radius: var(--radius-sm);
  border-bottom-right-radius: var(--radius-sm);
  border-bottom-left-radius: var(--radius-sm);
  color: var(--muted);
  font-size: 12px;
  font-weight: 600;
  line-height: var(--lh-12);
  cursor: pointer;
  transition:
    background-color 0.12s ease,
    color 0.12s ease;
}

.qpage__seg-btn--on {
  background: var(--surface);
  border-color: var(--line);
  color: var(--ink);
}

.qpage__tools {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 16px;
  height: 48px;
  padding: 0 24px;
  border-bottom: 1px solid var(--line);
}

.qpage__search {
  display: flex;
  flex: 0 0 260px;
  align-items: center;
  gap: 8px;
  box-sizing: border-box;
  height: 32px;
  padding: 0 12px;
  background: var(--surface);
  border: 1px solid var(--line);
  border-top-left-radius: var(--radius-md);
  border-top-right-radius: var(--radius-md);
  border-bottom-right-radius: var(--radius-md);
  border-bottom-left-radius: var(--radius-md);
}

/* 焦点环跟着全局那一套走（`--focus-ring`，2px）。这里只换边框色：搜索框的框本身就是
   焦点指示的载体，再套一圈 outline 会在 32px 高的小方块外多出一层。 */
.qpage__search:focus-within {
  border-color: var(--focus-ring);
}

.qpage__search-icon {
  flex: 0 0 auto;
  color: var(--muted);
}

.qpage__search-input {
  flex: 1 1 auto;
  width: 100%;
  min-width: 0;
  padding: 0;
  background: transparent;
  border: 0;
  color: var(--ink);
  font-size: 13px;
  line-height: var(--lh-13);
  outline: none;
}

.qpage__search-input::placeholder {
  color: var(--muted);
}

.qpage__tabs {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 4px;
}

.qpage__tab {
  height: 28px;
  padding: 0 12px;
  background: transparent;
  border: 0;
  border-top-left-radius: var(--radius-md);
  border-top-right-radius: var(--radius-md);
  border-bottom-right-radius: var(--radius-md);
  border-bottom-left-radius: var(--radius-md);
  color: var(--muted);
  font-size: 13px;
  font-weight: 600;
  line-height: var(--lh-13);
  white-space: nowrap;
  cursor: pointer;
  transition:
    background-color 0.12s ease,
    color 0.12s ease;
}

.qpage__tab:hover {
  background: var(--fill);
  color: var(--text);
}

.qpage__tab--on,
.qpage__tab--on:hover {
  background: var(--fill-2);
  color: var(--ink);
}

/* 无效按键的闪底。0.2s（§7.7 的「出现 / 消失」那一档），中性色 —— 一次落空的按键
   不该借状态三连色里的任何一支说话。 */
.qpage :deep(.qflash) {
  background: var(--fill-2);
  transition: background-color 0.2s ease;
}

/* 四态块。外层那 96px 的顶距和 320px 的宽度由两个列表组件给（`.qlist__none-box` /
   `.aft__none`），所以这里只管字和按钮。 */
.qpage__state-title {
  margin: 0;
  color: var(--ink);
  font-size: 15px;
  font-weight: 600;
  line-height: var(--lh-15);
}

.qpage__state-desc {
  margin: 8px 0 0;
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
}

/* 24px 高（§9.1 的动作列）。 */
.qpage__state-btn {
  height: 24px;
  margin-top: 12px;
  padding: 0 12px;
  background: var(--surface);
  border: 1px solid var(--line);
  border-top-left-radius: var(--radius-sm);
  border-top-right-radius: var(--radius-sm);
  border-bottom-right-radius: var(--radius-sm);
  border-bottom-left-radius: var(--radius-sm);
  color: var(--text);
  font-size: 12px;
  font-weight: 600;
  line-height: var(--lh-12);
  cursor: pointer;
  transition: background-color 0.12s ease;
}

.qpage__state-btn:hover {
  background: var(--fill);
}

/* 列表脚的**内容**。队列那一份落在 `AdminQueueList` 的脚里（40px 和那条分隔线由它
   给），总表那一份没有插槽可用，所以整条脚在这里自己画一遍。 */
.qpage__foot {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 12px;
  box-sizing: border-box;
  min-height: 40px;
  padding: 0 20px;
  background: var(--surface);
  border: 1px solid var(--line);
  border-top: 0;
  border-top-left-radius: var(--radius-sm);
  border-top-right-radius: var(--radius-sm);
  border-bottom-right-radius: var(--radius-lg);
  border-bottom-left-radius: var(--radius-lg);
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
}

.qpage__scope {
  flex: 0 1 auto;
  overflow: hidden;
  min-width: 0;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.qpage__foot-spacer {
  flex: 1 1 auto;
}

.qpage__foot-count,
.qpage__foot-end {
  flex: 0 0 auto;
  white-space: nowrap;
}

.qpage__pager {
  flex: 0 0 auto;
  height: 24px;
  padding: 0 12px;
  background: transparent;
  border: 1px solid var(--line);
  border-top-left-radius: var(--radius-sm);
  border-top-right-radius: var(--radius-sm);
  border-bottom-right-radius: var(--radius-sm);
  border-bottom-left-radius: var(--radius-sm);
  color: var(--text);
  font-size: 12px;
  line-height: var(--lh-12);
  white-space: nowrap;
  cursor: pointer;
  transition: background-color 0.12s ease;
}

.qpage__pager:hover {
  background: var(--fill);
}

.qpage__foot-count {
  margin-left: 4px;
}
</style>
