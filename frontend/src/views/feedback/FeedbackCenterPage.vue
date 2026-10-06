<script setup lang="ts">
import type { FeedbackKind, FeedbackStatus } from '@/cx_types'
import type { FeedbackTab as TabName } from '@/stores/feedback'

import { computed, onMounted, ref, watch } from 'vue'

import FeedbackCenterPageView from './FeedbackCenterPageView.vue'

import BaseButton from '@/components/base/BaseButton.vue'
import { kindLabel, statusLabel } from '@/components/feedback/feedbackLabels'
import FeedbackPageShell from '@/components/feedback/FeedbackPageShell.vue'
import { t } from '@/i18n'
import { allStatuses } from '@/lib/feedbackMeta'
import { useFeedbackStore } from '@/stores/feedback'

// 反馈中心首页 (/feedback) 的**容器那一半**：取数、筛选、跳转、栏位都留在这里，正文在
// 同目录的 `FeedbackCenterPageView.vue`（只吃 props、只发事件）。这样这一页单独渲染时
// 不需要后端、路由或 store —— 场景棘轮要的就是那件事。
//
// 页头那副壳（`FeedbackPageShell` + 标题 + 动作）留在这一半，不在视图里：
// `scroll.spec.ts` 钉着这一页的根节点就是那个壳（全站约定「滚动由每一页自己领」，壳
// 是领它的那一层）—— 壳在容器里，那条不变量照旧成立。
//
// 它是一个**独立完整页面**：不套工作区那套左侧话题导航，也不进任何项目上下文。
// 反馈说的是平台本身，跟「我现在在哪个项目里」没有关系。
defineOptions({ name: 'FeedbackCenterPage' })

const store = useFeedbackStore()

/* ---- 搜索 ----
 *
 * 绑法照旧：**store 才是那一份**（`setQuery` 会防抖之后重新拉一页）。输入框自己不留
 * 一格状态，否则「清除筛选」把 store 里那句清掉之后，框里还留着上一次打进去的词。 */
function onSearch(value: string) {
  store.setQuery(value)
}

/* ---- 四个筛选 ---------------------------------------------------------------
 *
 * 控件的值各自有一格本地状态，**store 才是发请求那一份**：`setFilter` 会重新拉一页，
 * 因为筛选在服务端（本地筛是第二份实现，两份漂开的表现是「翻页之后筛选悄悄失效」）。
 */
const kind = ref<FeedbackKind | null>(store.filterKind)
const status = ref<FeedbackStatus | null>(store.filterStatus)
const days = ref<number | null>(store.filterDays)
const author = ref(store.filterAuthor)

/** 三个离散栏位的取值。**「不限」不是一个值，是「这一栏没加条件」**，所以它的 value
 *  是 null —— 服务端收到 null 就是不按这一栏筛。 */
const kindOptions = computed(() => [
  { value: null, title: t('feedback.center.filter.any') },
  ...(store.meta?.kinds ?? ['bug', 'suggestion', 'other']).map((value) => ({
    value,
    title: kindLabel(value),
  })),
])
const statusOptions = computed(() => [
  { value: null, title: t('feedback.center.filter.any') },
  ...allStatuses(store.meta?.statuses).map((value) => ({ value, title: statusLabel(value) })),
])
const dayOptions = computed(() => [
  { value: null, title: t('feedback.center.filter.any') },
  { value: 1, title: t('feedback.center.filter.days24') },
  { value: 7, title: t('feedback.center.filter.days7') },
  { value: 30, title: t('feedback.center.filter.days30') },
])

function setKind(value: FeedbackKind | null) {
  kind.value = value
  store.setFilter({ kind: value })
}

function setStatus(value: FeedbackStatus | null) {
  status.value = value
  store.setFilter({ status: value })
}

function setDays(value: number | null) {
  days.value = value
  store.setFilter({ days: value })
}

/** 作者那一栏是打字，按防抖提交 —— 每敲一个字发一次请求，和搜索框一样的毛病。
 *  药丸是离散的一次选择，那三个不防抖。 */
let authorTimer: ReturnType<typeof setTimeout> | null = null
watch(author, (value) => {
  // **`?? ''` 留着的理由和以前不一样了**：手画的输入框只会给出字符串，但这一格还会
  // 被别处写（`clearFilters` 和「清除作者」那颗叉），而它当初是从 `v-text-field` 的
  // `clearable` 那里学到的教训 —— 清除按钮把 model 置成 `null` 而不是空串。少一句
  // 判空，清一次就在 watch 里抛 `Cannot read properties of null`，而**筛选清不掉**、
  // 界面还停在原样。
  const text = value ?? ''
  if (authorTimer) clearTimeout(authorTimer)
  authorTimer = setTimeout(() => {
    if (text.trim() !== store.filterAuthor.trim()) store.setFilter({ author: text })
  }, 300)
})

/** 栏位的名字来自词表。**键写成字面量的表**，不拼字符串：i18n 的闸门
 *  （`src/i18n/catalog.spec.ts`）照源码里的字面量认「这个键有人用」，拼出来的键既不算
 *  一次调用、那四个叶子又会被判成「没有任何文件引用」。 */
// 「已完成」而不是某一级状态的名字：这一栏装的是**收尾的两级**（已修复 + 已上线），
// 只写其中任一个，另一级都会在点进去之前看起来像丢了。`active` 反过来是
// 精确的 —— 那一栏现在只剩「处理中」，已收录还没人接手，不算活跃。
const TAB_KEYS: Record<string, string> = {
  all: 'feedback.center.tab.all',
  hot: 'feedback.center.tab.hot',
  active: 'feedback.center.tab.active',
  resolved: 'feedback.center.tab.resolved',
}
/** 服务端多出一个栏位时标成它自己的名字，而不是不显示（少一个 Tab 比多一个写着生词
 *  的好，因为少的那一个没有任何地方提示它存在过）。 */
const labelOf = (tab: string) => (TAB_KEYS[tab] ? t(TAB_KEYS[tab]) : tab)

const tabs = computed(() =>
  (store.meta?.tabs ?? ['all', 'hot', 'active', 'resolved']).map((value) => ({
    value: value as TabName,
    label: labelOf(value),
    count: store.tabCounts[value as TabName] ?? 0,
  }))
)

// 挂载时：词表 + 第一页。两个并发发出去 —— 列表不依赖词表（它有本地兜底的那份），
// 串行只会让首屏多等一个来回。
onMounted(() => {
  void store.loadMeta()
  void store.loadList()
})

/** 空列表有四种，说的话不一样：「没拉到」「搜索没结果」「筛选之后没有」「一条都没有」。
 *  把它们合成一句「暂无反馈」的话，前三种都会看着像平台真的没有反馈 —— 而「没有」正是
 *  这条渠道最不该说错的一句话。
 *
 *  四选一的判据是「为什么会空」，不是「有没有筛选」：拉挂了的时候筛选是一个还不成立
 *  的前提，所以失败排在最前。⚠️ 三句「没有」**必须分开**（§9.4）：搜索只覆盖标题、
 *  摘要、正文、提交人四个字段，用户在标签里找一条查不到时，缺的正是「我搜的东西本来就
 *  不在里面」这句话；而按类型筛空的人需要的是「换一个筛选条件」，不是「换一个关键词」。 */
const hasQuery = computed(() => !!store.query.trim())
/** 栏位或那四个筛选收窄了。**问 store 的 `hasFilters()`，不在这里再数一遍** ——
 *  那四个筛选的定义在 store 里（就是发请求的那一份），在这里重写一份等于同一件事有
 *  两个答案，而漂开的样子是「筛选明明生效了、空态却说平台一条反馈都没有」。 */
const hasNarrowing = computed(() => store.tab !== 'all' || store.hasFilters())

const emptyState = computed(() => {
  if (store.error) {
    return {
      icon: 'mdi-alert-circle-outline',
      tone: 'error' as const,
      title: t('feedback.center.error.title'),
      desc: t('feedback.center.error.desc'),
      action: t('feedback.center.error.retry'),
    }
  }
  if (hasQuery.value) {
    return {
      icon: 'mdi-comment-search-outline',
      title: t('feedback.center.search.title'),
      desc: t('feedback.center.search.desc'),
    }
  }
  if (hasNarrowing.value) {
    return {
      icon: 'mdi-filter-variant-remove',
      title: t('feedback.center.filtered.title'),
      desc: t('feedback.center.filtered.desc'),
      action: t('feedback.center.filtered.clear'),
    }
  }
  return {
    icon: 'mdi-comment-outline',
    title: t('feedback.center.empty.title'),
    desc: t('feedback.center.empty.desc'),
  }
})

/** 「热门」那一栏的规则说明那句话。三个数由服务端随 `meta` 发下来，这里只把它们说成
 *  人话；不在「热门」那一栏时是 null（视图据此 `v-if`）。 */
const hotNote = computed(() =>
  store.tab === 'hot'
    ? t('feedback.center.hot.note', {
        halfLife: store.hotHalfLifeDays,
        threshold: store.hotThreshold,
        minItems: store.hotMinItems,
      })
    : null
)

/** 空态那颗按钮：拉挂了就重拉，否则是「清除筛选」。两件事都在这颗按钮上，判据就是
 *  「这一次为什么空」的第一支（拉挂了）—— 和空态那句话说的是同一件事。 */
function onEmptyAction() {
  if (store.error) {
    void store.loadList()
    return
  }
  clearFilters()
}

/** 一次清干净：栏位、搜索词、以及那四个筛选。
 *
 *  **空态那颗按钮和筛选条那颗调的是同一个** —— 分两个的话，被筛选筛空的人按
 *  「清除筛选」会发现那四个控件还挂在上面，而列表已经变回全部了：屏幕上说一套、
 *  控件说另一套。
 *
 *  控件那几格也要一起回默认值：`store.clearFilters()` 清的是**发请求的那一份**，
 *  而控件绑的是本地 ref —— 只清 store 的话，药丸还停在选中的那一颗上。
 */
function clearFilters() {
  kind.value = null
  status.value = null
  days.value = null
  author.value = ''
  store.clearFilters()
}
</script>

<template>
  <FeedbackPageShell :title="t('feedback.center.title')">
    <template #actions>
      <!-- 这里原先还有一个「数据与架构」的入口，通向 /design/feedback。删了：那是
           给人核对实现用的页面，挂在产品里用户会当成功能点进去；两张图现在在
           docs/topics/feedback-前端原型.md 里。 -->
      <!-- 「我的反馈」对**所有人**都在（包括没登录的访客，他去了会看到空列表）。
           它不发请求问「我是谁」：清单的边界在服务端，这一页只是那一摞的门。 -->
      <BaseButton size="sm" to="/feedback/mine">
        {{ t('feedback.center.mine') }}
      </BaseButton>
      <!-- 管理后台的入口**只在服务端说我是管理员时出现**。上一轮这里是一个可以拨的
           开关；现在拨不动了，因为拨的其实是「我能不能看见别人的私密反馈」这件事，
           而那件事只能由服务端答。 -->
      <BaseButton v-if="store.isAdmin" kind="secondary" size="sm" to="/admin/feedback">
        {{ t('feedback.center.admin') }}
      </BaseButton>
      <!-- 提交走**独立页面**（`/feedback/new`），不是就地开一个浮层。这一颗只是那一页
           的门：草稿由那一页自己准备（`SubmitFeedbackForm` 挂载时调 `openSubmit`），
           这里不再调一次 —— 两处都调的话，第二次会把刚捞回来的草稿重判一遍。 -->
      <BaseButton kind="primary" prepend-icon="mdi-plus" :to="{ name: 'FeedbackSubmit' }">
        {{ t('feedback.center.submit') }}
      </BaseButton>
    </template>

    <FeedbackCenterPageView
      :query="store.query"
      :tab="store.tab"
      :tabs="tabs"
      :kind-options="kindOptions"
      :status-options="statusOptions"
      :day-options="dayOptions"
      :kind="kind"
      :status="status"
      :days="days"
      :author="author"
      :has-filters="store.hasFilters()"
      :hot-note="hotNote"
      :loading="store.loading"
      :items="store.items"
      :has-more="store.listHasMore"
      :loading-more="store.loadingMore"
      :total="store.total"
      :error="store.error"
      :empty="emptyState"
      @search="onSearch"
      @set-tab="store.setTab($event as TabName)"
      @set-kind="setKind"
      @set-status="setStatus"
      @set-days="setDays"
      @update:author="author = $event"
      @clear-filters="clearFilters"
      @empty-action="onEmptyAction"
      @dismiss="store.clearError()"
      @more="store.loadMoreList()"
      @support="store.toggleSupport($event)"
    />
  </FeedbackPageShell>
</template>
