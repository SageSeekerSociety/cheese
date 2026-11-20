<script setup lang="ts">
import type { FeedbackCard as FeedbackCardItem, FeedbackKind, FeedbackMeta, FeedbackStatus } from '@/cx_types'
import type { FeedbackTab as TabName } from '@/stores/feedback'
import type { BetaCredits, CreditRewards } from '@/types/creditOffers'

import { computed, ref, watch } from 'vue'

import { useNavigation } from '@/composables/useNavigation'

import AdminEmptyState from '@/components/admin/AdminEmptyState.vue'
import AdminTabs from '@/components/admin/AdminTabs.vue'
import BaseButton from '@/components/base/BaseButton.vue'
import NavLink from '@/components/common/NavLink.vue'
import FeedbackCard from '@/components/feedback/FeedbackCard.vue'
import FeedbackErrorBanner from '@/components/feedback/FeedbackErrorBanner.vue'
import { kindLabel, statusLabel } from '@/components/feedback/feedbackLabels'
import FeedbackList from '@/components/feedback/FeedbackList.vue'
import FeedbackPageShell from '@/components/feedback/FeedbackPageShell.vue'
import { t } from '@/i18n'
import { statusesForTab } from '@/lib/feedbackMeta'

// 反馈中心首页 (/feedback) 的**画面那一半**。
//
// 它是一个**独立完整页面**：不套工作区那套左侧话题导航，也不进任何项目上下文。
// 反馈说的是平台本身，跟「我现在在哪个项目里」没有关系。
//
// 版面是四层：页头（标题 + 动作）/ 栏位页签 / 筛选条 / 列表。这四层**由三个共用件
// 拼出来**，不再各画一遍：
//
//   * `FeedbackPageShell` —— 滚动容器、页边距、内容宽度、页头。四个反馈页以前各自
//     逐字抄了一份 `.fb-page` / `.fb-head`，抄错一处的表现是「那一页有一截内容永远
//     够不着」，而屏幕上没有任何东西看起来坏了。
//   * `AdminTabs` —— 栏位。它是管理控制台同一排页签（下划线 + 计数），两个地方说的
//     是同一件事（换一栏看），不该长得不一样。
//   * `FeedbackList` + `AdminEmptyState` —— 一张面 + 头发丝分隔的行，四种「没有」共用
//     一副骨架。
//
// ## 这一半一行取数都没有
//
// 词表、列表、筛选、栏位、支持全都在容器 `FeedbackCenterPage.vue` 里（它调 store、
// 读地址）。这里只收 props、只发事件：列表从 `items` 来，筛选取值从 `filterKind` /
// `filterStatus` / `filterDays` / `filterAuthor` 来，改筛选、切栏位、翻页、重试、点支持
// 各报一个事件上去。所以这一页**能摆进组件预览站单独看**，不必先起后端。
//
// ## 筛选条为什么是这一排药丸
//
// 上一版是四个 `v-select` / `v-text-field` 横排：outlined 的输入框在 1440 下四个各自
// 撑满一格、字比列表还大，而它们要回答的三个问题（谁提的、哪一类、什么时候）各自
// 只有三五个取值 —— 值得一次点开下拉的只有「作者」那一栏，而它是打字。所以现在：
//
//   * 取值离散的三栏（类型 / 状态 / 时间）就地排成**一排药丸**，选中哪一颗一眼看得见，
//     一次点击就换（下拉要点两下）。默认那颗是「不限」，也就是「这一栏没加条件」。
//   * 「作者」留一个**手画的输入框**（和搜索框同一副骨架），带一颗清除的叉。
//
// 分组、药丸、搜索框都写在本文件里，但配色和尺寸走令牌，和管理控制台的工具栏是同一套
// （`--fill` 的药丸、`--surface` + `--line` 的输入框、`--focus-ring` 的焦点环）。
//
// ## 筛选在服务端
//
// 四个筛选和搜索一样是**服务端**的筛选（本地再筛一遍就是第二份实现）—— 容器里改筛选
// 会重新拉一页。它们默认都不生效（全部「不限」），所以这一页第一眼的样子没变：多出来的
// 是一排控件，不是一层常态的过滤。
//
// 权限在服务端：`isAdmin` 读的是 `GET /feedback/meta`，管理后台的入口跟着它出现或者
// 不出现 —— 客户端不再猜，也不是拨一下就能看见的。
defineOptions({ name: 'FeedbackCenterPageView' })

const props = defineProps<{
  /** 服务端说这个读者是管理员：管理后台的入口跟着它出现。 */
  isAdmin: boolean
  /** 当前栏位。栏位是**服务端**的筛选，切了就要重新拉一页（容器发请求）。 */
  tab: TabName
  /** 四个栏位的页签（名字与计数）。名字来自词表、计数来自服务端，都在容器里算好。 */
  tabs: { value: TabName; label: string; count: number }[]
  /** 服务端的词表；还没到、或者没有时用它自己的兜底取值。 */
  meta: FeedbackMeta | null
  /** 搜索词（服务端参数）。 */
  query: string
  /** 四个筛选当前的取值（服务端参数）。 */
  filterKind: FeedbackKind | null
  filterStatus: FeedbackStatus | null
  filterDays: number | null
  filterAuthor: string
  /** 栏位或那四个筛选收窄了没有。判据在容器里（就是发请求的那一份）。 */
  hasFilters: boolean
  items: FeedbackCardItem[]
  total: number
  loading: boolean
  loadingMore: boolean
  listHasMore: boolean
  /** 服务端的原话；`null` 表示没失败。 */
  error: string | null
  /** 「热门」那一栏排序规则的三个数，服务端随 `meta` 发下来。 */
  hotHalfLifeDays: number
  hotThreshold: number
  hotMinItems: number
  personalTeamHandle?: string
  beta?: BetaCredits | null
  rewards?: CreditRewards | null
  rewardsError?: string | null
}>()

const emit = defineEmits<{
  /** 搜索词变了（在 store 里会防抖之后重新拉一页）。 */
  search: [query: string]
  /** 三个离散栏位各自选中了一颗药丸。`null` = 这一栏没加条件。 */
  'set-kind': [value: FeedbackKind | null]
  'set-status': [value: FeedbackStatus | null]
  'set-days': [value: number | null]
  /** 作者那一栏打了字（容器防抖之后提交）。 */
  'set-author': [value: string]
  /** 清掉栏位、搜索词和那四个筛选。 */
  'clear-filters': []
  'set-tab': [tab: TabName]
  /** 拉挂了之后那颗「重试」。 */
  retry: []
  /** 列表尾巴上那颗「加载更多」。 */
  more: []
  'dismiss-error': []
  /** 某一行点了支持 / 取消支持。请求在容器里发。 */
  support: [id: string]
  'retry-rewards': []
}>()

const navigation = useNavigation()
const showRules = ref(false)
watch(
  () => navigation?.route?.hash,
  (hash) => {
    if (hash === '#rewards') showRules.value = true
  },
  { immediate: true }
)

/* ---- 搜索 ----
 *
 * 绑法照旧：**容器/store 才是那一份**（输入框不留一格状态，否则「清除筛选」把那一句
 * 清掉之后，框里还留着上一次打进去的词）。 */
function onSearch(event: Event) {
  emit('search', (event.target as HTMLInputElement).value)
}

/* ---- 四个筛选 ---------------------------------------------------------------
 *
 * 控件的值各自有一格本地状态，**容器/store 才是发请求那一份**：改筛选要重新拉一页，
 * 因为筛选在服务端（本地筛是第二份实现，两份漂开的表现是「翻页之后筛选悄悄失效」）。
 */
const kind = ref<FeedbackKind | null>(props.filterKind)
const status = ref<FeedbackStatus | null>(props.filterStatus)
const days = ref<number | null>(props.filterDays)
const author = ref(props.filterAuthor)

/** 三个离散栏位的取值。**「不限」不是一个值，是「这一栏没加条件」**，所以它的 value
 *  是 null —— 服务端收到 null 就是不按这一栏筛。 */
const kindOptions = computed(() => [
  { value: null as FeedbackKind | null, title: t('feedback.center.filter.any') },
  ...(props.meta?.kinds ?? ['bug', 'suggestion', 'other']).map((value) => ({
    value,
    title: kindLabel(value),
  })),
])
/** 状态药丸只列**当前栏位里真能出现**的状态：已完成那一栏只装三种结局，收起的
 *  那两颗（已收录、处理中）选中必然空。判据与栏位规则的同一份在 `statusesForTab`。 */
const statusOptions = computed(() => [
  { value: null as FeedbackStatus | null, title: t('feedback.center.filter.any') },
  ...statusesForTab(props.tab, props.meta?.statuses).map((value) => ({ value, title: statusLabel(value) })),
])

/** 换栏位时，原先选中的状态如果在新栏位里不可能出现，就回到「不限」。
 *
 *  发请求那一份由容器清（`store.setTab` 同一次请求里清掉，不然会先拉一次空页）；
 *  这里只负责把控件那一格跟上 —— 否则药丸已经不在选项里，`status` 却还记着它，
 *  切回别的栏位时它会凭空亮起来。 */
watch(
  () => props.filterStatus,
  (value) => {
    if (value !== status.value) status.value = value
  }
)
const dayOptions = computed(() => [
  { value: null as number | null, title: t('feedback.center.filter.any') },
  { value: 1, title: t('feedback.center.filter.days24') },
  { value: 7, title: t('feedback.center.filter.days7') },
  { value: 30, title: t('feedback.center.filter.days30') },
])

function setKind(value: FeedbackKind | null) {
  kind.value = value
  emit('set-kind', value)
}

function setStatus(value: FeedbackStatus | null) {
  status.value = value
  emit('set-status', value)
}

function setDays(value: number | null) {
  days.value = value
  emit('set-days', value)
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
    if (text.trim() !== props.filterAuthor.trim()) emit('set-author', text)
  }, 300)
})

/** 空列表有四种，说的话不一样：「没拉到」「搜索没结果」「筛选之后没有」「一条都没有」。
 *  把它们合成一句「暂无反馈」的话，前三种都会看着像平台真的没有反馈 —— 而「没有」正是
 *  这条渠道最不该说错的一句话。
 *
 *  四选一的判据是「为什么会空」，不是「有没有筛选」：拉挂了的时候筛选是一个还不成立
 *  的前提，所以失败排在最前。⚠️ 三句「没有」**必须分开**（§9.4）：搜索只覆盖标题、
 *  摘要、正文、提交人四个字段，用户在标签里找一条查不到时，缺的正是「我搜的东西本来就
 *  不在里面」这句话；而按类型筛空的人需要的是「换一个筛选条件」，不是「换一个关键词」。 */
const hasQuery = computed(() => !!props.query.trim())
/** 「那一排筛选」在收窄。**栏位本身不算**：点进一个空栏位时人一个筛选都没加，
 *  说成「没有符合条件的反馈」等于替他编了一句他没做过的筛选（见下面第三句）。 */
const isFiltered = computed(() => props.hasFilters)

type EmptyState = {
  icon: string
  title: string
  desc: string
  tone?: 'neutral' | 'error'
  action?: string
  /** 空态里那颗按钮要按的是哪一件事。文案和动作分开写，是因为同一颗位置上有两件
   *  不同的事（重试 / 清除筛选），而它们各自对应哪一句在下面那四支里已经定死了。 */
  act?: 'retry' | 'clear'
}

const emptyState = computed<EmptyState>(() => {
  if (props.error) {
    return {
      icon: 'mdi-alert-circle-outline',
      tone: 'error',
      title: t('feedback.center.error.title'),
      desc: t('feedback.center.error.desc'),
      action: t('feedback.center.error.retry'),
      act: 'retry',
    }
  }
  if (hasQuery.value) {
    return {
      icon: 'mdi-comment-search-outline',
      title: t('feedback.center.search.title'),
      desc: t('feedback.center.search.desc'),
    }
  }
  if (isFiltered.value) {
    return {
      icon: 'mdi-filter-variant-remove',
      title: t('feedback.center.filtered.title'),
      desc: t('feedback.center.filtered.desc'),
      action: t('feedback.center.filtered.clear'),
      act: 'clear',
    }
  }
  // 这一栏自己没有东西，而人一个筛选都没加。这一句和最后那句必须分开：这一句能做的
  // 事是「换一栏」，最后那句说的是「平台一条都没有」—— 点进「已完成」遇到前者的次数
  // 远多于后者。把它们合成「没有符合条件的反馈」，人会去找一个他从没加过的筛选。
  if (props.tab !== 'all') {
    return {
      icon: 'mdi-filter-variant-remove',
      title: t('feedback.center.tabEmpty.title'),
      desc: t('feedback.center.tabEmpty.desc'),
      action: t('feedback.center.tabEmpty.action'),
      act: 'clear',
    }
  }
  return {
    icon: 'mdi-comment-outline',
    title: t('feedback.center.empty.title'),
    desc: t('feedback.center.empty.desc'),
  }
})

function onEmptyAction() {
  if (emptyState.value.act === 'retry') {
    emit('retry')
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
 *  控件那几格也要一起回默认值：容器清的是**发请求的那一份**，而控件绑的是本地 ref ——
 *  只清容器的话，药丸还停在选中的那一颗上。
 */
function clearFilters() {
  kind.value = null
  status.value = null
  days.value = null
  author.value = ''
  emit('clear-filters')
}
</script>

<template>
  <FeedbackPageShell :title="t('feedback.center.title')" wide>
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
      <BaseButton v-if="isAdmin" kind="secondary" size="sm" to="/admin/feedback">
        {{ t('feedback.center.admin') }}
      </BaseButton>
      <!-- 提交走**独立页面**（`/feedback/new`），不是就地开一个浮层。这一颗只是那一页
           的门：草稿由那一页自己准备（`SubmitFeedbackForm` 挂载时调 `openSubmit`），
           这里不再调一次 —— 两处都调的话，第二次会把刚捞回来的草稿重判一遍。 -->
      <BaseButton kind="primary" prepend-icon="mdi-plus" :to="{ name: 'FeedbackSubmit' }">
        {{ t('feedback.center.submit') }}
      </BaseButton>
    </template>

    <section id="rewards" class="fb-rewards">
      <div class="fb-rewards__lead">
        <v-icon icon="mdi-gift-outline" size="22" />
        <div>
          <h2>{{ t('usage.rewards.activityTitle') }}</h2>
          <p>{{ t('usage.rewards.activityLead', { n: beta?.feedback_credits ?? 100 }) }}</p>
        </div>
        <button
          type="button"
          class="fb-rules-toggle"
          :aria-expanded="showRules"
          aria-controls="reward-rules"
          @click="showRules = !showRules"
        >
          {{ t('usage.rewards.rules') }}<v-icon :icon="showRules ? 'mdi-chevron-up' : 'mdi-chevron-down'" size="16" />
        </button>
      </div>
      <div v-if="showRules" id="reward-rules" class="fb-rewards__rules">
        <p>{{ t('usage.rewards.betaRule', { n: beta?.credits ?? 500 }) }}</p>
        <p>{{ t('usage.rewards.feedbackRule', { n: beta?.feedback_credits ?? 100 }) }}</p>
        <NavLink
          :to="
            personalTeamHandle
              ? { name: 'TeamsDetailCredits', params: { handle: personalTeamHandle } }
              : { name: 'SignIn' }
          "
          >{{ t('usage.rewards.claimLink') }}</NavLink
        >
      </div>
    </section>

    <div class="fb-center-columns" :class="{ 'fb-center-columns--summary': rewards || rewardsError }">
      <div class="fb-center-list">
        <!-- 栏位切换走一个 action，不直接改那一格：栏位是**服务端**的筛选，改了的
         下一件事必然是重新拉一页，绑赋值就把那一步留在模板外面了。 -->
        <AdminTabs
          :model-value="tab"
          :options="tabs"
          :label="t('feedback.center.tab.label')"
          class="fb-tabs"
          @update:model-value="emit('set-tab', $event as TabName)"
        />

        <!-- 筛选条。默认全部「不限」，所以这一页第一眼的样子没变；那一排药丸说的是
         「这一栏里哪些」，栏位页签说的是「哪一栏」。 -->
        <div class="fb-tools">
          <div class="fb-search">
            <v-icon icon="mdi-magnify" size="16" class="fb-search__icon" aria-hidden="true" />
            <input
              :value="query"
              type="text"
              class="fb-search__input"
              :placeholder="t('feedback.center.search.placeholder')"
              :aria-label="t('feedback.center.search.placeholder')"
              autocomplete="off"
              spellcheck="false"
              @input="onSearch"
            />
          </div>

          <div class="fb-fgroup" role="radiogroup" :aria-label="t('feedback.center.filter.kind')">
            <span class="fb-fgroup__label">{{ t('feedback.center.filter.kind') }}</span>
            <button
              v-for="option in kindOptions"
              :key="String(option.value)"
              type="button"
              class="fb-pill"
              :class="{ 'fb-pill--on': kind === option.value }"
              role="radio"
              :aria-checked="kind === option.value"
              @click="setKind(option.value)"
            >
              {{ option.title }}
            </button>
          </div>

          <div class="fb-fgroup" role="radiogroup" :aria-label="t('feedback.center.filter.status')">
            <span class="fb-fgroup__label">{{ t('feedback.center.filter.status') }}</span>
            <button
              v-for="option in statusOptions"
              :key="String(option.value)"
              type="button"
              class="fb-pill"
              :class="{ 'fb-pill--on': status === option.value }"
              role="radio"
              :aria-checked="status === option.value"
              @click="setStatus(option.value)"
            >
              {{ option.title }}
            </button>
          </div>

          <div class="fb-fgroup" role="radiogroup" :aria-label="t('feedback.center.filter.days')">
            <span class="fb-fgroup__label">{{ t('feedback.center.filter.days') }}</span>
            <button
              v-for="option in dayOptions"
              :key="String(option.value)"
              type="button"
              class="fb-pill"
              :class="{ 'fb-pill--on': days === option.value }"
              role="radio"
              :aria-checked="days === option.value"
              @click="setDays(option.value)"
            >
              {{ option.title }}
            </button>
          </div>

          <!-- 「作者」是这一排里唯一要打字的：它的取值不是三五个，是一个 handle。
           清除的叉**只在框里有字时出现** —— 常驻的话它是一颗平时没有任何作用的按钮。 -->
          <div class="fb-fgroup">
            <!-- for 已关联下方原生 input；标签与含清除按钮的输入框分开，不重复嵌套控件。 -->
            <label class="fb-fgroup__label" for="fb-author">{{ t('feedback.center.filter.author') }}</label>
            <div class="fb-author">
              <input
                id="fb-author"
                v-model="author"
                type="text"
                class="fb-author__input"
                :placeholder="t('feedback.center.filter.authorPlaceholder')"
                autocomplete="off"
                spellcheck="false"
              />
              <button
                v-if="author"
                type="button"
                class="fb-author__x"
                :aria-label="t('feedback.center.filter.clear')"
                @click="author = ''"
              >
                <v-icon size="12" aria-hidden="true">mdi-close</v-icon>
              </button>
            </div>
          </div>

          <!-- 「清除筛选」只在真有筛选时出现：常驻的话它是一颗平时没有任何作用的按钮，
           而这一排里已经有五组控件了。 -->
          <button v-if="hasFilters" type="button" class="fb-clear" @click="clearFilters">
            {{ t('feedback.center.filter.clear') }}
          </button>
        </div>

        <!-- 「热门」凭什么这么排，是这一页唯一一处读者猜不出来的规则：它看着像按支持数
         排，其实是按「支持数按半衰期折过的分数」排 —— 一条二十个支持的老反馈排在一条
         今天刚爆的上面，不解释一句就只是「这个排序坏了」。
         三个数都由服务端随 `meta` 发下来，这里只负责把它们说成人话：前端自己再算一遍
         的话（哪怕只是把 2 写死在这句话里），改阈值就要改两处，而两处漂开的表现是
         「说明和实际排序对不上」，页面上看不出任何异常。
         加载中不藏它：这句话说的是这一栏的规则，不是这一栏的结果，跟着骨架一起闪一下
         反倒是多一次闪动。 -->
        <p v-if="tab === 'hot'" class="t-meta-read t-num fb-hot-note">
          {{
            t('feedback.center.hot.note', {
              halfLife: hotHalfLifeDays,
              threshold: hotThreshold,
              minItems: hotMinItems,
            })
          }}
        </p>

        <!-- 列表**非空**时的失败也要画出来。以前 error 只在下面那块「一条也没有」里
         渲染，于是从卡片上点「支持」失败（已办完的条目回 412）时页面上什么都不动：
         按钮按得下去、数字不变、一句话也没有 —— 和「这个按钮坏了」长得一模一样。
         列表为空时下面那块画同一句话（并且带重试），这里不重复画。 -->
        <FeedbackErrorBanner v-if="error && items.length" :message="error" @dismiss="emit('dismiss-error')" />

        <!-- 列表：一张面 + 头发丝分隔的行（`FeedbackList`）。骨架行数由它自己定（§9.4），
         这一页的「一行」是一条 60 来像素的反馈行，不是 132px 的卡。 -->
        <FeedbackList
          :loading="loading"
          :count="items.length"
          :has-more="listHasMore"
          :loading-more="loadingMore"
          :shown="items.length"
          :total="total"
          @more="emit('more')"
        >
          <!-- 打开详情那条链接在行自己身上（`router-link`），这里不再接一个 `@open`
           去 push —— 那就又回到「只有鼠标够得着」了，见 FeedbackCard.vue。
           点支持在卡片上报上来（带 id），请求由容器发给服务端。 -->
          <FeedbackCard v-for="item in items" :key="item.id" :item="item" @support="emit('support', $event)" />

          <template #empty>
            <AdminEmptyState
              :title="emptyState.title"
              :desc="emptyState.desc"
              :icon="emptyState.icon"
              :tone="emptyState.tone"
              :action="emptyState.action"
              @action="onEmptyAction"
            >
              <!-- 服务端那句话照直画出来：上面那句说的是「这类事现在是什么样」，
               这一句说的是「这一次为什么没成」—— 两句不是一回事，少一句就只剩
               「检查网络后重试」，而失败可能压根不是网络（比如没有权限）。 -->
              <p v-if="error" class="fb-empty__raw t-meta-read">{{ error }}</p>
            </AdminEmptyState>
          </template>

          <!-- 页脚说的是**在这个页面上提交**会发生什么，而这条路提出来的反馈没有房间来源
           （`topic_id` 只有 accept 端点解得出），所以不提房间那一档——详情页和卡片上的
           那句说的是一条已经存在的行，它可能有房间，两处不一样是对的。 -->
          <template #foot>{{ t('feedback.center.foot') }}</template>
        </FeedbackList>
      </div>
      <aside v-if="rewards || rewardsError" class="fb-reward-summary">
        <h2>{{ t('usage.rewards.summaryTitle') }}</h2>
        <template v-if="rewards"
          ><dl>
            <div>
              <dt>{{ t('usage.rewards.awardedCount') }}</dt>
              <dd>{{ t('usage.rewards.count', { n: rewards.feedback_count }) }}</dd>
            </div>
            <div>
              <dt>{{ t('usage.rewards.awardedCredits') }}</dt>
              <dd>{{ t('usage.points', { n: rewards.feedback_credits }) }}</dd>
            </div>
          </dl>
          <p>{{ t('usage.rewards.summaryNote') }}</p>
          <NavLink
            :to="
              personalTeamHandle
                ? { name: 'TeamsDetailCredits', params: { handle: personalTeamHandle } }
                : { name: 'SignIn' }
            "
            >{{ t('usage.rewards.history') }}<v-icon icon="mdi-chevron-right" size="16" /></NavLink
        ></template>
        <template v-else
          ><p>{{ rewardsError }}</p>
          <BaseButton size="sm" @click="emit('retry-rewards')">{{ t('usage.retry') }}</BaseButton></template
        >
      </aside>
    </div>
  </FeedbackPageShell>
</template>

<style scoped>
.fb-rewards {
  padding: 16px 24px;
  margin-bottom: 24px;
  background: var(--accent-wash);
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
}
.fb-rewards__lead {
  display: flex;
  gap: 12px;
  align-items: center;
}
.fb-rewards__lead > .v-icon {
  color: var(--muted);
}
.fb-rewards h2,
.fb-reward-summary h2 {
  font-size: 15px;
  line-height: var(--lh-15);
  font-weight: 600;
  margin: 0;
}
.fb-rewards p,
.fb-reward-summary p {
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
  margin: 8px 0 0;
}
.fb-rules-toggle {
  display: inline-flex;
  gap: 4px;
  align-items: center;
  flex: none;
  margin-left: auto;
  min-height: 32px;
  color: var(--text);
  font-size: 13px;
  line-height: var(--lh-13);
}
.fb-rewards__rules {
  padding-top: 16px;
  margin-top: 16px;
  border-top: 1px solid var(--line);
}
.fb-rewards__rules :deep(a),
.fb-reward-summary :deep(a) {
  display: inline-flex;
  align-items: center;
  color: var(--text);
  font-size: 13px;
  line-height: var(--lh-13);
  margin-top: 12px;
}
.fb-center-columns {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  align-items: start;
  gap: 24px;
}
.fb-center-columns--summary {
  grid-template-columns: minmax(0, 1fr) 230px;
}
.fb-center-list {
  min-width: 0;
}
.fb-reward-summary {
  border-left: 1px solid var(--line);
  padding-left: 24px;
}
.fb-reward-summary dl {
  margin: 24px 0;
}
.fb-reward-summary dl > div {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 16px;
}
.fb-reward-summary dt {
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
}
.fb-reward-summary dd {
  margin: 0;
  color: var(--ink);
  font-size: 18px;
  line-height: var(--lh-18);
  font-variant-numeric: tabular-nums;
}
@media (max-width: 959.98px) {
  .fb-center-columns {
    grid-template-columns: minmax(0, 1fr);
  }
  .fb-reward-summary {
    border-left: 0;
    border-top: 1px solid var(--line);
    padding: 24px 0 0;
  }
}
@media (max-width: 599.98px) {
  .fb-rewards {
    padding: 16px;
  }
  .fb-rewards__lead {
    flex-wrap: wrap;
  }
  .fb-rewards__lead > div {
    flex: 1;
  }
  .fb-rules-toggle {
    min-height: 44px;
    margin-left: 34px;
  }
}
/* 栏位页签下面那条底线由 `AdminTabs` 自己画（选中项的 2px 琥珀下划线），这里只给它
   和下面一排控件之间的距离。 */
.fb-tabs {
  margin-bottom: 4px;
}
/* 筛选条：一组一组排，放不下就整组折行。组**自己不折行**（一颗药丸不该和它的同伴
   分开），所以窄屏上是一组一行。 */
.fb-tools {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px 16px;
  margin-bottom: 16px;
}
/* 搜索框：和管理控制台那把是同一副骨架（32px、`--surface` + `--line`、焦点换边框色
   而不是再套一圈 outline）。宽度收在 240 —— 再宽它也只是这一排里的一格。 */
.fb-search {
  display: flex;
  flex: 0 0 240px;
  align-items: center;
  gap: 8px;
  box-sizing: border-box;
  height: 32px;
  padding: 0 12px;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
}
.fb-search:focus-within {
  border-color: var(--focus-ring);
}
.fb-search__icon {
  color: var(--faint);
}
.fb-search__input {
  width: 100%;
  min-width: 0;
  height: 100%;
  padding: 0;
  color: var(--ink);
  font-family: inherit;
  font-size: 13px;
  line-height: var(--lh-13);
  background: transparent;
  border: 0;
  outline: none;
}
.fb-search__input::placeholder {
  color: var(--faint);
}
/* 一组筛选：一个 12px 的栏名 + 一排药丸。栏名是**说明**（它说的是右边那几颗是什么），
   自己不参与点选，所以用 --muted 而不是 --ink。 */
.fb-fgroup {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 4px 6px;
}
.fb-fgroup__label {
  margin-right: 2px;
  color: var(--muted);
  font-size: 12px;
  line-height: var(--lh-12);
}
/* 药丸：`--fill` 底、选中那颗抬到 `--surface` + 一圈描边（和管理端的「栏位」同一个
   控件、同一套颜色）。高 24 —— 它是这一排里最小的可点目标，再小就不好按了。 */
.fb-pill {
  height: 24px;
  padding: 0 10px;
  color: var(--muted);
  font-family: inherit;
  font-size: 12px;
  font-weight: 600;
  line-height: var(--lh-12);
  white-space: nowrap;
  background: var(--fill);
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition:
    background-color 0.12s ease,
    color 0.12s ease;
}
.fb-pill:hover {
  color: var(--text);
}
.fb-pill--on {
  color: var(--ink);
  background: var(--surface);
  border-color: var(--line-2);
}
/* 作者那一栏：一个 32px 的输入框，和搜索框同高同框（两者都是打字）。 */
.fb-author {
  display: flex;
  align-items: center;
  gap: 4px;
  box-sizing: border-box;
  height: 32px;
  padding: 0 6px 0 10px;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
}
.fb-author:focus-within {
  border-color: var(--focus-ring);
}
.fb-author__input {
  flex: 0 1 120px;
  min-width: 0;
  height: 100%;
  padding: 0;
  color: var(--ink);
  font-family: inherit;
  font-size: 13px;
  line-height: var(--lh-13);
  background: transparent;
  border: 0;
  outline: none;
}
.fb-author__input::placeholder {
  color: var(--faint);
}
.fb-author__x {
  display: inline-flex;
  flex: none;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  padding: 0;
  color: var(--faint);
  background: transparent;
  border: 0;
  border-radius: var(--radius-sm);
  cursor: pointer;
}
.fb-author__x:hover {
  color: var(--ink);
  background: var(--fill);
}
/* 「清除筛选」是一条文字按钮，不是药丸：它清的是**这一排全部**，比任何一颗药丸都大
   一档，画成同样的形状会让人以为它只清旁边那一组。 */
.fb-clear {
  height: 24px;
  padding: 0 8px;
  color: var(--muted);
  font-family: inherit;
  font-size: 12px;
  line-height: var(--lh-12);
  background: transparent;
  border: 0;
  border-radius: var(--radius-sm);
  cursor: pointer;
}
.fb-clear:hover {
  color: var(--ink);
  background: var(--fill);
}
/* 热门那一栏的规则说明：页签之后 12px，往下和列表也是 12px。
   不给底色、不加图标 —— 它是这一栏的注脚，不是警告，抬成一块提示框会让人以为
   热门这一栏出了什么问题。 */
.fb-hot-note {
  margin: 0 0 12px;
  line-height: var(--lh-14-loose);
}
/* 空态里那句服务端的原话。空态那副骨架（`AdminEmptyState`）把动作放在这一句上面，
   所以这里只补一点上边距。 */
.fb-empty__raw {
  margin: 8px 0 0;
}
/* 窄屏：搜索框占满一整行（它在这一排里是最常用的那一格），其余每组各占一行。
   药丸组自己在窄屏上折行 —— 英文的栏名和取值（"In progress" 那一档）比中文长一倍，
   不折的话 390 上会横向溢出去。 */
@media (max-width: 599.98px) {
  .fb-search {
    flex: 1 1 100%;
  }
  .fb-fgroup {
    flex: 1 1 100%;
  }
  .fb-author__input {
    flex: 1 1 auto;
  }
}
</style>
