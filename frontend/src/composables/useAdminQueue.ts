// 管理后台的反馈队列（`/admin/queue`，§4.1）**逻辑**的那一半：光标、详情的防抖与
// 自动已读、分诊与撤销、键盘、地址里带过来的问法、以及页面自己那四个状态。
//
// 分家的理由和 `useAdminDashboard` / `useAdminModels` 是同一个形状：这一页原先一份
// `<script setup>` 七百多行，取数和画法长在一起，于是页头、工具行、四态块都拿不出来
// 单独看 —— 挂在预览站里得先起假后端、把整页拉起来。现在三件事各归各位：
//
//   - 判断与接线 → 这里（页面只吃返回值，把动作报回来）；
//   - 画 → `components/admin/queue/*.vue`，只吃 props、只往上发事件；
//   - 顶层容器与两个列表的挂法 → 页面自己。
//
// **三件事留在这一层，不往组件里掉**（和拆之前同一个分工）：
//
//   1. **问法**（栏位 / 关键词 / 三个日期窗口）在 store 里，这里只负责把它们画出来、
//      把人的动作报回去。队列本体（`AdminQueueList` / `AdminFeedbackTable`）只认
//      `items`，拿不到写入口 —— 分诊键要落 store，所以键在这一层分发（§8 的「列表」档）。
//   2. **「手上这一条是谁」**。光标、详情、已读、撤销条都挂在同一个 id 上，所以这个
//      判断只能有一份，也就是这里。`AdminQueueDetail` 的注释把同一个理由写了一遍。
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
import type AdminQueueDetail from '@/components/admin/AdminQueueDetail.vue'
import type { FeedbackCard, FeedbackStatus } from '@/cx_types'
import type { AdminTab } from '@/stores/feedback'

import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'

import { allStatuses, statusMeta } from '@/lib/feedbackMeta'
import { relativeDays } from '@/lib/feedbackWindows'
import { useFeedbackStore } from '@/stores/feedback'

/** 分段控件的两个档。**档位的名字由页面这一层定**：`view` 是它的状态，而两档画成什么
 *  样子是 `AdminQueueHeader` 的事（它自己那张 `VIEWS` 表，值就是这个类型）。 */
export type QueueView = 'list' | 'table'

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

/** 队列的四个**栏位**（服务端的 `ADMIN_TABS`）。写成常量是因为 store 只导出了类型、
 *  没导出这份元组。顺序就是控件上的顺序：最常用的「公开」在最前，「安全」最靠里。
 *
 *  这四个词在这一页上是**服务端的问法**（`GET /admin/feedback?tab=`），不是客户端的筛选
 *  —— 和工具行右边那排状态页签不是一回事：状态页签在**已经拿到的那一页里**筛，栏位换的
 *  是去要哪一批数据。所以两者在控件上必须长得不一样（收在一段底色里的分段控件 vs 裸的
 *  药丸），否则同一行里两排一模一样的东西说着两件不同的事。 */
const ADMIN_TABS: AdminTab[] = ['public', 'private', 'agent', 'security']

/** 地址里那三个窗口键。和 store 的三个字段一一对应，但存的是**键名**：chip 上画的、
 *  地址里摘的都是它。导出是因为 `AdminQueueToolbar` 报回来的那一颗 chip 也要说清是
 *  哪一个（三个 setter 各是一件事，`key` 是它们的名字）。 */
export type QueueWindowKey = 'since' | 'resolved_since' | 'deployed_since'

/** 搜索框住在 `AdminQueueToolbar` 里，而 `/` 键和 chip 清空都把焦点送进去。搜索框的
 *  位置是**页面**的事（那一件只吃 props），所以页面把那件事包成一个回调交给这一层。 */
export interface AdminQueueDeps {
  focusSearch: () => void
}

export function useAdminQueue(deps: AdminQueueDeps) {
  const store = useFeedbackStore()
  const { t } = useI18n()
  const route = useRoute()
  const router = useRouter()

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

  /** 撤销条：栈深 1（§9.6）。`from` 是改之前的那一格 —— 撤销就是把它写回去。 */
  const undo = ref<{ id: string; from: FeedbackStatus; message: string } | null>(null)

  /** 输入框里的字。**它和 `store.adminQuery` 不是同一个东西**：后者是「真的发出去过」
   *  的那一个词，前者是手上正在打的。分开的理由见 `QUERY_MIN_CHARS` 和 `clearFilters`。 */
  const draft = ref(store.adminQuery)

  const detailRef = ref<InstanceType<typeof AdminQueueDetail> | null>(null)

  const media =
    typeof window !== 'undefined' && typeof window.matchMedia === 'function' ? window.matchMedia(WIDE_QUERY) : null
  const isWide = ref(media ? media.matches : true)

  /* ---- 手上这一页 ---- */

  const tabs = computed<(FeedbackStatus | 'all')[]>(() => ['all', ...allStatuses(store.meta?.statuses)])
  const items = computed(() => store.adminItems)

  /** 状态页签那一排（`AdminTabs`）要的形状：`{ value, label }`。文案口径和原来那颗裸
   *  药丸逐字一致 —— `all` 走 `feedback.queue.tab.all`，其余走 `statusMeta().label`
   *  （那一份是呈现词表，不从 i18n 取，见 `lib/feedbackMeta.ts` 文件头）。 */
  const tabOptions = computed(() =>
    tabs.value.map((tab) => ({
      value: tab,
      label: tab === 'all' ? t('feedback.queue.tab.all') : statusMeta(tab).label,
    }))
  )

  /** 四个栏位各自的名字。**写成一张字面量表，不拼键** —— i18n 的闸门
   *  （`src/i18n/catalog.spec.ts`）是照源码里的字面量认「这个键有人用」的，拼出来的键
   *  既不算调用、真叶子又会被判成没人引用。 */
  const laneOptions = computed<{ value: AdminTab; label: string }[]>(() => [
    { value: 'public', label: t('feedback.queue.lane.public') },
    { value: 'private', label: t('feedback.queue.lane.private') },
    { value: 'agent', label: t('feedback.queue.lane.agent') },
    { value: 'security', label: t('feedback.queue.lane.security') },
  ])

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

  const current = computed<FeedbackCard | null>(() =>
    cursorIndex.value >= 0 ? visible.value[cursorIndex.value] : null
  )

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
    // `!== null`：`null` 才算没失败，空串是「失败了但服务端没给话」。用真值判会把这种
    // 失败落进「暂无反馈」，把接口挂掉画成平台是空的。
    if (listError.value !== null) return 'error'
    if (items.value.length) return 'filtered'
    return hasFilter.value ? 'filtered' : 'empty'
  })

  /** 四态文案。**键名逐字写全**，不做 `` t(`${ns}.error.title`) `` 那种拼接：i18n 闸门
   *  是按源码里的字面量扫引用的，拼出来的键在它眼里等于没人用（`catalog.spec.ts`）。
   *
   *  出错那两态的说明行放**服务端原话**，不放「检查网络后重试。」这种固定话：失败未必是
   *  网络（没权限、限流、后端 500 都长这样），吞掉原因就等于把人往错的方向支。服务端没
   *  给话时不画那一行（空说明行只占地方），标题和重试照旧 —— 失败本身不能因此不报。 */
  const copy = computed(() => {
    if (state.value === 'error') {
      return view.value === 'list'
        ? {
            title: t('feedback.queue.error.title'),
            desc: listError.value || undefined,
            action: t('feedback.queue.error.retry'),
          }
        : {
            title: t('feedback.table.error.title'),
            desc: listError.value || undefined,
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
    // 按钮写着「清除筛选」，地址里留着三键的话 F5 会把人送回筛空态。
    dropQueryKeys(['since', 'resolved_since', 'deployed_since'])
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
      deps.focusSearch()
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
    if (status && allStatuses(store.meta?.statuses).includes(status as FeedbackStatus))
      statusTab.value = status as FeedbackStatus

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

  /** 切栏位。**和地址同步**，理由有两条：
   *
   *   * 刷新之后还停在刚才那一栏。这一页的栏位是**服务端的问法**，换一栏就是换一批数据；
   *     地址里不带它的话，F5 一下人就被悄悄送回「公开」，而他刚才看的可能是私密那一栏。
   *   * 链接发得出去。`/admin/feedback?tab=private` 这条旧书签本来就认（见 `ADMIN_TABS`），
   *     而这一页现在自己也写得出同样的地址 —— 一份事实、一个来源，不会出现「控件上是私密、
   *     地址上是空的」这种两个答案。
   *
   *  用 `replace` 不是 `push`：切栏位不是「去别的地方」，是在同一页里换一个问题，回退键
   *  该回到来处，而不是在四个栏位之间一步步倒。`public` 那一档**不写进地址**（不写就是
   *  它，默认值不占位）。 */
  function selectLane(lane: AdminTab) {
    if (store.adminTab === lane) return
    store.setAdminTab(lane)
    const query = { ...route.query }
    if (lane === 'public') delete query.tab
    else query.tab = lane
    void router.replace({ query })
  }

  /* ---- 日期窗口 chips ---- */

  /** chip 上窗口值的画法：相对窗口（'7d'）画成「近 7 天」，绝对日期原样。store 里存的
   *  就是深链原文（见 `lib/feedbackWindows.ts`），所以这里只看形状、不用管它从哪来。 */
  function windowValueLabel(raw: string): string {
    const days = relativeDays(raw)
    return days === null ? raw : t('feedback.queue.window.days', { n: days })
  }

  /** 每个生效的窗口一颗 chip。**键名逐字写全**（同 `laneOptions` 那条理由：i18n 闸门按
   *  源码字面量认键，拼出来的键它看不见）。 */
  const windowChips = computed(() => {
    const chips: { key: QueueWindowKey; text: string; clearAria: string }[] = []
    if (store.adminSince)
      chips.push({
        key: 'since',
        text: t('feedback.queue.window.since', { v: windowValueLabel(store.adminSince) }),
        clearAria: '',
      })
    if (store.adminResolvedSince)
      chips.push({
        key: 'resolved_since',
        text: t('feedback.queue.window.resolved', { v: windowValueLabel(store.adminResolvedSince) }),
        clearAria: '',
      })
    if (store.adminDeployedSince)
      chips.push({
        key: 'deployed_since',
        text: t('feedback.queue.window.deployed', { v: windowValueLabel(store.adminDeployedSince) }),
        clearAria: '',
      })
    for (const c of chips) c.clearAria = t('feedback.queue.window.clear', { chip: c.text })
    return chips
  })

  /** 把几个键从地址里摘掉。没有那几键时不发 `replace` —— 清筛选不该在历史里留一条
   *  什么都没改的地址。 */
  function dropQueryKeys(keys: string[]) {
    const query = { ...route.query }
    let changed = false
    for (const k of keys) {
      if (k in query) {
        delete query[k]
        changed = true
      }
    }
    if (changed) void router.replace({ query })
  }

  /* ---- 子件报回来的那几档状态 ---- */

  /** 视图、搜索草稿、状态页签、撤销条：都是**这一层**的状态，子件只报「人做了什么」。
   *  写入口收成函数而不是让页面各处直接赋值，理由同 `setDetailOpen` —— 同一个状态被
   *  两处写时，两处说的话必须在一条路上。 */
  function setView(next: QueueView) {
    view.value = next
  }

  function setDraft(next: string) {
    draft.value = next
  }

  function setStatusTab(next: FeedbackStatus | 'all') {
    statusTab.value = next
  }

  function dismissUndo() {
    undo.value = null
  }

  /** 清一个窗口 = 改 store（setter 自己重拉）+ 把地址里的那一键摘掉 —— 和 `selectLane`
   *  写回 tab 是同一条规矩：一份事实一个来源，F5 不该复活人刚亲手清掉的筛选。 */
  function clearWindow(key: QueueWindowKey) {
    if (key === 'since') store.setAdminSince(null)
    else if (key === 'resolved_since') store.setAdminResolvedSince(null)
    else store.setAdminDeployedSince(null)
    dropQueryKeys([key])
    // chip 没了焦点不能落空：送到旁边的搜索框（鼠标点的不出焦点环，:focus-visible 管这件事）。
    void nextTick(() => deps.focusSearch())
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

  /* ---- 页面读的那些 ---- */

  /** 队列脚那一条要不要说「在搜索的结果里」—— 页面自己只拿这个布尔。 */
  const scoped = computed(() => !!store.adminQuery.trim())

  return {
    // 详情
    detailRef,
    isWide,
    detailOpen,
    setDetailOpen,
    closeDetail,
    triageOpen,
    setTriageOpen,
    detail: computed(() => store.detail),
    detailLoading: computed(() => store.detailLoading),
    detailError,
    onDetailTriage,
    // 页头
    unread,
    markCurrentRead,
    reload,
    view,
    setView,
    // 工具行
    adminTab: computed(() => store.adminTab),
    laneOptions,
    selectLane,
    draft,
    setDraft,
    windowChips,
    clearWindow,
    statusTab,
    setStatusTab,
    tabOptions,
    // 列表
    visible,
    cursorId,
    cursorIndex,
    showSkeleton,
    showStatusWord,
    onActiveIndex,
    onTableActivate,
    openItem,
    advance,
    // 四态
    state,
    copy,
    listError,
    runAction,
    // 脚
    scoped,
    hasPrev: computed(() => store.adminHasPrev),
    hasNext: computed(() => store.adminHasNext),
    prev: () => store.adminPrev(),
    next: () => store.adminNext(),
    // 撤销
    undo,
    undoTriage,
    dismissUndo,
  }
}
