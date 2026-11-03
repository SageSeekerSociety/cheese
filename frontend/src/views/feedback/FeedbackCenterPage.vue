<script setup lang="ts">
import type { FeedbackKind, FeedbackStatus } from '@/cx_types'
import type { FeedbackTab as TabName } from '@/stores/feedback'

import { computed, onMounted } from 'vue'

import { useCreditOffers } from '@/composables/useCreditOffers'

import FeedbackCenterPageView from './FeedbackCenterPageView.vue'

import { t } from '@/i18n'
import { myHandle, myId } from '@/me'
import { useFeedbackStore } from '@/stores/feedback'

// 反馈中心首页 (/feedback)。
//
// 它是一个**独立完整页面**：不套工作区那套左侧话题导航，也不进任何项目上下文。
// 反馈说的是平台本身，跟「我现在在哪个项目里」没有关系。
//
// 版面（页头 / 栏位页签 / 筛选条 / 列表）和那几层共用件（`FeedbackPageShell`、
// `AdminTabs`、`FeedbackList` + `AdminEmptyState`）都在同目录的
// `FeedbackCenterPageView.vue` 里。
//
// ## 这一半只做三件事：读地址、取数、发请求
//
// 词表、列表、栏位、四个筛选、支持，全都通过 store 读写；画面只收 props、只 emit。
// 所以那一半可以单独摆进组件预览站看，不必先起后端（见 docs/manual/dev/scenes.md）。
// **筛选和栏位是服务端参数**：这里改一次就重新拉一页，本地再筛一遍就是第二份实现，
// 两份漂开的表现是「翻页之后筛选悄悄失效」，页面上看不出异常。
//
// 权限在服务端：`isAdmin` 读 `GET /feedback/meta`，管理后台的入口跟着它出现或者不
// 出现 —— 客户端不再猜，也不是拨一下就能看见的。
defineOptions({ name: 'FeedbackCenterPage' })

const store = useFeedbackStore()
const rewards = useCreditOffers()
function loadRewards() {
  if (myId()) void rewards.load()
}

/** 栏位的名字来自词表。**键写成字面量的表**，不拼字符串：i18n 的闸门
 *  （`src/i18n/catalog.spec.ts`）照源码里的字面量认「这个键有人用」，拼出来的键既不算
 *  一次调用、那四个叶子又会被判成「没有任何文件引用」。 */
// 「已完成」而不是某一级状态的名字：这一栏装的是**收尾的三级**（已修复 + 已上线 +
// 不修复），只写其中任一个，别的都会在点进去之前看起来像丢了。`active` 整栏删了
// （2026-10-28）—— 它和状态药丸里的「处理中」是同一个切口，那个轴现在只剩药丸。
const TAB_KEYS: Record<string, string> = {
  all: 'feedback.center.tab.all',
  hot: 'feedback.center.tab.hot',
  resolved: 'feedback.center.tab.resolved',
}
/** 服务端多出一个栏位时标成它自己的名字，而不是不显示（少一个 Tab 比多一个写着生词
 *  的好，因为少的那一个没有任何地方提示它存在过）。 */
const labelOf = (tab: string) => (TAB_KEYS[tab] ? t(TAB_KEYS[tab]) : tab)

const tabs = computed(() =>
  (store.meta?.tabs ?? ['all', 'hot', 'resolved']).map((value) => ({
    value: value as TabName,
    label: labelOf(value),
    count: store.tabCounts[value as TabName] ?? 0,
  }))
)

/* ---- 搜索 ----
 *
 * 绑法照旧：**store 才是那一份**（`setQuery` 会防抖之后重新拉一页）。输入框自己不留
 * 一格状态，否则「清除筛选」把 store 里那句清掉之后，框里还留着上一次打进去的词。 */
function onSearch(query: string) {
  store.setQuery(query)
}

/* ---- 四个筛选（那一排控件）
 *
 * 值本身住在画面里（它绑的是控件），发请求那一份在 store：这里收到画面报上来的取值，
 * 转手交给 `setFilter`（它会重新拉一页）。作者那一栏的防抖在画面里做完了，这里直接发。 */
function setKind(value: FeedbackKind | null) {
  store.setFilter({ kind: value })
}

function setStatus(value: FeedbackStatus | null) {
  store.setFilter({ status: value })
}

function setDays(value: number | null) {
  store.setFilter({ days: value })
}

function setAuthor(value: string) {
  store.setFilter({ author: value })
}

/** 一次清干净：栏位、搜索词、以及那四个筛选。画面那几格自己回默认值，这里清 store
 *  那一份（就是发请求的那一份）。 */
function clearFilters() {
  store.clearFilters()
}

// 挂载时：词表 + 第一页。两个并发发出去 —— 列表不依赖词表（它有本地兜底的那份），
// 串行只会让首屏多等一个来回。
onMounted(() => {
  void store.loadMeta()
  void store.loadList()
  loadRewards()
})
</script>

<template>
  <FeedbackCenterPageView
    :is-admin="store.isAdmin"
    :personal-team-handle="myHandle()"
    :tab="store.tab"
    :tabs="tabs"
    :meta="store.meta"
    :query="store.query"
    :filter-kind="store.filterKind"
    :filter-status="store.filterStatus"
    :filter-days="store.filterDays"
    :filter-author="store.filterAuthor"
    :has-filters="store.hasFilters()"
    :items="store.items"
    :total="store.total"
    :loading="store.loading"
    :loading-more="store.loadingMore"
    :list-has-more="store.listHasMore"
    :error="store.error"
    :hot-half-life-days="store.hotHalfLifeDays"
    :hot-threshold="store.hotThreshold"
    :hot-min-items="store.hotMinItems"
    :beta="rewards.beta.value"
    :rewards="rewards.rewards.value"
    :rewards-error="rewards.error.value"
    @retry-rewards="loadRewards"
    @search="onSearch"
    @set-kind="setKind"
    @set-status="setStatus"
    @set-days="setDays"
    @set-author="setAuthor"
    @clear-filters="clearFilters"
    @set-tab="store.setTab($event)"
    @retry="store.loadList()"
    @more="store.loadMoreList()"
    @dismiss-error="store.clearError()"
    @support="store.toggleSupport($event)"
  />
</template>
