<script setup lang="ts">
import type { FeedbackKind, FeedbackStatus } from '@/cx_types'
import type { FeedbackTab as TabName } from '@/stores/feedback'

import { computed, onMounted, ref, watch } from 'vue'

import LoadingSkeleton from '@/components/common/LoadingSkeleton.vue'
import FeedbackCard from '@/components/feedback/FeedbackCard.vue'
import { t } from '@/i18n'
import { KIND_LABEL, STATUS_META } from '@/lib/feedbackMeta'
import { useFeedbackStore } from '@/stores/feedback'

// 反馈中心首页 (/feedback)。
//
// 它是一个**独立完整页面**：不套工作区那套左侧话题导航，也不进任何项目上下文。
// 反馈说的是平台本身，跟「我现在在哪个项目里」没有关系。
//
// 页面结构是三层（标题行 / Tab / 列表）**加上一排筛选**。
//
// 那一排是后加的，而加它之前这里写着一句「刻意只有三层」—— 当时的理由是「筛选条会让
// 『我要找的那条在哪』变成要读三处才能回答的问题」。那句话在列表还短的时候是对的，
// 现在不成立了：**只靠搜索找不到东西的时候，缺的不是更多字符，是另一个问题** ——
// 谁提的、哪一类、什么时候。四个筛选各自答的是这些，而它们和搜索一样是**服务端**的
// 筛选（本地再筛一遍就是第二份实现）。
//
// 它们默认都不生效（全部「不限」），所以这一页第一眼的样子没变 —— 多出来的是一排
// 控件，不是一层常态的过滤。
//
// 上一轮这里有一个「原型身份」开关（user / admin），那是给人看两套界面的道具。现在
// 权限在服务端：`store.isAdmin` 读 `GET /feedback/meta`，管理后台的入口跟着它出现
// 或者不出现 —— 客户端不再猜，也不是拨一下就能看见的。
defineOptions({ name: 'FeedbackCenterPage' })

const store = useFeedbackStore()

/* ---- 四个筛选 ---------------------------------------------------------------
 *
 * 控件的值各自有一格本地状态，**store 才是发请求那一份**：`setFilter` 会重新拉一页，
 * 因为筛选在服务端（本地筛是第二份实现，两份漂开的表现是「翻页之后筛选悄悄失效」）。
 *
 * 选项里的名词一律来自共享的表（`KIND_LABEL`、`STATUS_META`、`store.statusLadder`）：
 * 这一页再写一遍「Bug / 建议 / 其他」就是同一个事实的第二份拷贝，而它的漂开方式是
 * 「列表里叫建议、筛选框里叫功能请求」。
 */
const kind = ref<FeedbackKind | null>(store.filterKind)
const status = ref<FeedbackStatus | null>(store.filterStatus)
const days = ref<number | null>(store.filterDays)
const author = ref(store.filterAuthor)

const kindOptions = computed(() => [
  { value: null, title: '不限' },
  ...(store.meta?.kinds ?? ['bug', 'suggestion', 'other']).map((value) => ({
    value,
    title: KIND_LABEL[value],
  })),
])
const statusOptions = computed(() => [
  { value: null, title: '不限' },
  ...store.statusLadder.map((value) => ({ value, title: STATUS_META[value].label })),
])
const dayOptions = [
  { value: null, title: '不限' },
  { value: 1, title: '24 小时' },
  { value: 7, title: '7 天' },
  { value: 30, title: '30 天' },
]

/** 作者那一栏是打字，按防抖提交 —— 每敲一个字发一次请求，和搜索框一样的毛病。
 *  下拉是离散的一次选择，那三个不防抖。 */
let authorTimer: ReturnType<typeof setTimeout> | null = null
watch(author, (value) => {
  // **`?? ''` 不是防御性编程，是这一栏真的会变成 null**：`v-text-field` 的
  // `clearable` 那颗 × 走的是 Vuetify 的 `onClear`，它把 model 置成 `null` 而不是
  // 空串（`VTextField.js` 的 `model.value = null`）。少了这一句，点一下 × 就在
  // watch 里抛 `Cannot read properties of null`，而**筛选清不掉**、界面还停在原样。
  const text = value ?? ''
  if (authorTimer) clearTimeout(authorTimer)
  authorTimer = setTimeout(() => {
    if (text.trim() !== store.filterAuthor.trim()) store.setFilter({ author: text })
  }, 300)
})

/** 栏位的中文名。**有哪些栏位**来自服务端（`meta.tabs`），这里只负责把它们叫成
 *  人话；服务端多出一个栏位时标成它自己的名字，而不是不显示（少一个 Tab 比多一个
 *  写着生词的好，因为少的那一个没有任何地方提示它存在过）。 */
// 「已完成」而不是某一级状态的名字：这一栏装的是**收尾的两级**（已修复 + 已上线），
// 只写其中任一个，另一级都会在点进去之前看起来像丢了。`active` 反过来是
// 精确的 —— 那一栏现在只剩「处理中」，「已收录」还没人接手，不算活跃。
const TAB_LABELS: Record<string, string> = {
  all: '全部',
  hot: '热门',
  active: '处理中',
  resolved: '已完成',
}
const labelOf = (tab: string) => TAB_LABELS[tab] ?? tab

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

/** 空列表有三种，说的话不一样：「一条都没有」「筛选之后没有」「没拉到」。
 *  把它们合成一句「暂无反馈」的话，最后一种会看着像平台真的没有反馈。 */
const hasFilter = computed(() => !!store.query.trim() || store.tab !== 'all')

/** 三选一。文案本身在 i18n 目录里（两种语言逐条对齐），这里只做**选择** ——
 *  判据是「为什么会空」，不是「有没有筛选」：拉挂了的时候筛选是一个还不成立的前提，
 *  所以失败排在最前。
 *  ⚠️ 搜索无结果和一条都没有**必须是两句**（§9.4）：搜索只覆盖标题、摘要、正文、
 *  提交人四个字段，用户在标签里找一条查不到时，缺的正是「我搜的东西本来就不在里面」
 *  这句话 —— 合成一句「暂无反馈」，他会以为那条反馈被删了。 */
const emptyState = computed(() => {
  if (store.error) {
    return {
      icon: 'mdi-alert-circle-outline',
      title: t('feedback.center.error.title'),
      desc: t('feedback.center.error.desc'),
    }
  }
  if (hasFilter.value) {
    return {
      icon: 'mdi-comment-search-outline',
      title: t('feedback.center.search.title'),
      desc: t('feedback.center.search.desc'),
    }
  }
  return {
    icon: 'mdi-comment-outline',
    title: t('feedback.center.empty.title'),
    desc: t('feedback.center.empty.desc'),
  }
})

/** 一次清干净：栏位、搜索词、以及那四个筛选。
 *
 *  **空态那颗按钮和筛选条那颗调的是同一个** —— 分两个的话，被筛选筛空的人按
 *  「清除筛选」会发现那四个控件还挂在上面，而列表已经变回全部了：屏幕上说一套、
 *  控件说另一套。
 *
 *  控件那几格也要一起回默认值：`store.clearFilters()` 清的是**发请求的那一份**，
 *  而控件绑的是本地 ref —— 只清 store 的话，数字还显示着上一次的选择。
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
  <!-- `fill-height overflow-y-auto` 不是装饰，是这一页能不能滚的全部。common.scss
       把 html/body/#app 定成固定高度 + `overflow: hidden`，滚动由每一页自己领
       （CalendarView / MemberView / MarketView 都是这么写的）。少了这两个类，内容
       一旦比窗口高，下半截就被外面那层 `overflow-hidden` 裁掉，而且**没有任何元素
       可滚** —— 1280×600 的窗口里反馈中心少 117px，列表最后几条再也够不着。 -->
  <div class="fb-page fill-height overflow-y-auto">
    <div class="fb-page__inner page-container">
      <header class="fb-head">
        <h1 class="t-page-title">反馈中心</h1>
        <v-spacer />
        <!-- 这里原先还有一个「数据与架构」的入口，通向 /design/feedback。删了：那是
             给人核对实现用的页面，挂在产品里用户会当成功能点进去；两张图现在在
             docs/topics/feedback-前端原型.md 里。 -->
        <!-- 「我的反馈」对**所有人**都在（包括没登录的访客，他去了会看到空列表）。
             它不发请求问「我是谁」：清单的边界在服务端，这一页只是那一摞的门。 -->
        <v-btn variant="text" color="secondary" size="small" to="/feedback/mine"> 我的反馈 </v-btn>
        <!-- 管理后台的入口**只在服务端说我是管理员时出现**。上一轮这里是一个可以拨的
             开关；现在拨不动了，因为拨的其实是「我能不能看见别人的私密反馈」这件事，
             而那件事只能由服务端答。 -->
        <v-btn v-if="store.isAdmin" variant="outlined" color="secondary" size="small" to="/admin/feedback">
          管理后台
        </v-btn>
      </header>

      <div class="fb-toolbar">
        <v-text-field
          :model-value="store.query"
          autocomplete="off"
          placeholder="搜索反馈"
          prepend-inner-icon="mdi-magnify"
          variant="outlined"
          density="compact"
          hide-details
          clearable
          class="fb-search"
          @update:model-value="(v: string) => store.setQuery(v ?? '')"
        />
        <!-- 提交走**独立页面**（`/feedback/new`），不是就地开一个浮层。这一颗只是那一页
             的门：草稿由那一页自己准备（`SubmitFeedbackForm` 挂载时调 `openSubmit`），
             这里不再调一次 —— 两处都调的话，第二次会把刚捞回来的草稿重判一遍。 -->
        <v-btn color="primary" prepend-icon="mdi-plus" :to="{ name: 'FeedbackSubmit' }">提交反馈</v-btn>
      </div>

      <!-- Tab 的切换走一个 action，不直接绑 `store.tab`：栏位是**服务端**的筛选，
          改了的下一件事必然是重新拉一页，绑赋值就把那一步留在模板外面了。 -->
      <v-tabs
        :model-value="store.tab"
        density="comfortable"
        color="primary"
        class="fb-tabs"
        @update:model-value="store.setTab($event as TabName)"
      >
        <v-tab v-for="tab in tabs" :key="tab.value" :value="tab.value">
          {{ tab.label }}
          <span class="fb-tab-count">{{ tab.count }}</span>
        </v-tab>
      </v-tabs>

      <!-- 四个筛选。**默认全部「不限」**，所以这一页第一眼的样子没变。它们加在栏位
           之上（栏位说「哪一栏」，这四个说「那一栏里哪些」），而且和搜索一样在服务端
           执行 —— 本地再筛一遍就是第二份实现。 -->
      <div class="fb-filters">
        <v-select
          v-model="kind"
          :items="kindOptions"
          density="compact"
          variant="outlined"
          hide-details
          class="fb-filter"
          autocomplete="off"
          aria-label="类型"
          @update:model-value="store.setFilter({ kind })"
        />
        <v-select
          v-model="status"
          :items="statusOptions"
          density="compact"
          variant="outlined"
          hide-details
          class="fb-filter"
          autocomplete="off"
          aria-label="状态"
          @update:model-value="store.setFilter({ status })"
        />
        <v-text-field
          v-model="author"
          placeholder="作者"
          density="compact"
          variant="outlined"
          hide-details
          autocomplete="off"
          class="fb-filter"
          aria-label="作者"
          clearable
        />
        <v-select
          v-model="days"
          :items="dayOptions"
          density="compact"
          variant="outlined"
          hide-details
          class="fb-filter"
          autocomplete="off"
          aria-label="时间"
          @update:model-value="store.setFilter({ days })"
        />
        <!-- 「清除筛选」只在真有筛选时出现：常驻的话它是一颗平时没有任何作用的
             按钮，而这一排里已经有四个控件了。 -->
        <v-btn
          v-if="store.hasFilters()"
          variant="text"
          color="secondary"
          size="small"
          class="fb-filters__clear"
          @click="clearFilters"
        >
          清除筛选
        </v-btn>
      </div>

      <!-- 「热门」凭什么这么排，是这一页唯一一处读者猜不出来的规则：它看着像按支持数
           排，其实是按「支持数按半衰期折过的分数」排 —— 一条二十个支持的老反馈排在一条
           今天刚爆的上面，不解释一句就只是「这个排序坏了」。
           三个数都由服务端随 `meta` 发下来，这里只负责把它们说成人话：前端自己再算一遍
           的话（哪怕只是把 2 写死在这句话里），改阈值就要改两处，而两处漂开的表现是
           「说明和实际排序对不上」，页面上看不出任何异常。
           加载中不藏它：这句话说的是这一栏的规则，不是这一栏的结果，跟着骨架一起闪一下
           反倒是多一次闪动。 -->
      <p v-if="store.tab === 'hot'" class="t-meta fb-hot-note">
        按支持数算，但越新的越算数：今天 2 个支持，和
        {{ store.hotHalfLifeDays }} 天前的 4 个一样重。够 {{ store.hotThreshold }} 分、又还没办完的 进这一栏；够线的不足
        {{ store.hotMinItems }} 条时按分数补齐，所以刚开板也不会空着。
      </p>

      <!-- 骨架**不放进 .fb-list**：那一层是 gap 8 的 flex 列，而骨架的行自带 8px
           下边距（它得能单独用在任何地方），两处一叠就是 16px，到货那一刻列表会
           往上收一截 —— 骨架存在的意义正是不让这件事发生。 -->
      <!-- 列表**非空**时的失败也要画出来。以前 error 只在下面那块「一条也没有」里
           渲染，于是从卡片上点「支持」失败（已办完的条目回 412）时页面上什么都不动：
           按钮按得下去、数字不变、一句话也没有 —— 和「这个按钮坏了」长得一模一样。
           列表为空时下面那块画同一句话（并且带重试），这里不重复画。 -->
      <v-alert
        v-if="store.error && store.items.length"
        type="warning"
        variant="tonal"
        density="compact"
        closable
        class="mb-3"
        @click:close="store.clearError()"
      >
        {{ store.error }}
      </v-alert>

      <!-- 骨架画 3 张卡（§9.4），不是 6 行：这一页的「一行」是一张 132px 的卡，
           6 张会把首屏占满、下面一半全是灰块，而第一屏真正该看见的是内容。 -->
      <LoadingSkeleton v-if="store.loading" variant="feedback" :rows="3" />

      <div v-else class="fb-list">
        <!-- 打开详情那条链接在卡片自己身上（`router-link`），这里不再接一个
             `@open` 去 push —— 那就又回到「只有鼠标够得着」了，见 FeedbackCard.vue。 -->
        <FeedbackCard v-for="item in store.items" :key="item.id" :item="item" />
        <div v-if="!store.items.length" class="fb-empty">
          <v-icon size="28" class="fb-empty__icon">{{ emptyState.icon }}</v-icon>
          <div class="fb-empty__title">{{ emptyState.title }}</div>
          <p class="fb-empty__desc">{{ emptyState.desc }}</p>
          <!-- 服务端那句话照直画出来：上面那句说的是「这类事现在是什么样」，
               这一句说的是「这一次为什么没成」—— 两句不是一回事，少一句就只剩
               「检查网络后重试」，而失败可能压根不是网络（比如没有权限）。 -->
          <p v-if="store.error" class="fb-empty__raw t-meta-read">{{ store.error }}</p>
          <v-btn
            v-if="store.error"
            variant="text"
            color="secondary"
            size="small"
            class="fb-empty__action"
            @click="store.loadList()"
          >
            重试
          </v-btn>
          <v-btn
            v-else-if="hasFilter"
            variant="text"
            color="secondary"
            size="small"
            class="fb-empty__action"
            @click="clearFilters"
          >
            清除筛选
          </v-btn>
        </div>

        <!-- 翻页那一行只在**真的还有下一页**时出现（`listHasMore` 比的是手上条数和
             服务端报的总数）。到底了不画「已到底」：那一行字只是在告诉读者「这个按钮
             你按不了了」，而没按过的人看到它只会以为自己漏看了什么。 -->
        <div v-if="store.listHasMore" class="fb-more">
          <v-btn
            variant="outlined"
            color="secondary"
            size="small"
            :loading="store.loadingMore"
            @click="store.loadMoreList()"
          >
            加载更多
          </v-btn>
          <span class="t-meta"> 已显示 {{ store.items.length }} / 共 {{ store.total }} 条 </span>
        </div>
      </div>

      <!-- 页脚在加载时先不画：它是「读完了、下面是空的」这句话的一部分，
           跟着骨架一起出现等于提前说了还没到的话。
           这句话说的是**在这个页面上提交**会发生什么，而这条路提出来的反馈没有房间来源
           （`topic_id` 只有 accept 端点解得出），所以不提房间那一档——详情页和卡片上的
           那句说的是一条已经存在的行，它可能有房间，两处不一样是对的。 -->
      <p v-if="!store.loading" class="t-meta fb-foot">
        公开反馈所有人可见；提交时选「私密」的只有你和平台管理员能看到，别人搜不到、也拿不到链接
      </p>
    </div>
  </div>
</template>

<style scoped>
.fb-page {
  /* 左右 16 是窄屏的页边距：容器本身居中且有 max-width，宽屏上真正撑开版面的是
     page-container，不是这 16px。 */
  padding: 24px 16px 48px;
}
.fb-page__inner {
  margin: 0 auto;
}
.fb-head {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 12px;
  margin-bottom: 16px;
}
.fb-toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 8px;
}
.fb-search {
  max-width: 360px;
}
/* 四个筛选一排。窄屏折行 —— 四个控件加清除按钮在 360px 上放不下，硬挤会变成每个
   都窄到读不出值。 */
.fb-filters {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
}

/* 每个下拉一个固定宽度：`v-select` 默认会撑满，四个撑满会把这一行挤成四行。 */
.fb-filter {
  flex: 0 0 140px;
  max-width: 140px;
}

.fb-filters__clear {
  flex: 0 0 auto;
}

.fb-tabs {
  margin-bottom: 12px;
  border-bottom: 1px solid var(--line);
}
.fb-tab-count {
  margin-left: 6px;
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--faint);
}
.fb-list {
  display: flex;
  flex-direction: column;
  /* 卡片之间 16px（§4.3）：8px 的时候相邻两张卡的下沿和上沿只差一线之隔，
     而这两张卡的描边本来就一样重，一屏十几张看上去像一整块被横线划开的表 ——
     卡片是「一条一条」的，行距得让这件事看得见。 */
  gap: 16px;
}
.fb-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  max-width: 320px;
  margin: 0 auto;
  padding: 48px 0;
  gap: 8px;
}
/* 空态那一块的字号分两档（§9.2）：主文案 15/--lh-15/600/--ink 是「现在这样」，
   副文案 13/--lh-13/--muted 是「接下来怎么办」。别把两句话并成一句 —— 并了之后
   要么主文案被副文案拖成一条说明，要么副文案被抬成标题，两种都读不出主次。
   这一块整体不用 --faint：它是要人读的（AA 4.5:1），--faint 在浅色主题下
   四种底色上都到不了 3:1（见 style.css 的 .t-meta-read）。 */
.fb-empty__icon {
  color: var(--muted);
}
.fb-empty__title {
  font-size: 15px;
  font-weight: 600;
  line-height: var(--lh-15);
  color: var(--ink);
  text-align: center;
}
.fb-empty__desc {
  margin: 0;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
  text-align: center;
}
.fb-empty__raw {
  margin: 0;
  text-align: center;
}
.fb-empty__action {
  margin-top: 4px;
}
/* 热门那一栏的规则说明：Tab 那条底线之后 12px，往下和列表也是 12px。
   不给底色、不加图标 —— 它是这一栏的注脚，不是警告，抬成一块提示框会让人以为
   热门这一栏出了什么问题。 */
.fb-hot-note {
  margin: 0 0 12px;
  line-height: var(--lh-14-loose);
}
/* 翻页那一行：按钮和计数在同一条中线上，两者之间 12px。 */
.fb-more {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 12px;
  padding-top: 4px;
}
.fb-foot {
  margin: 24px 0 0;
  /* 用 token 而不是 1.7：这一档的领值只有 --lh-* 这一份来源，手写的倍数在两个
     主题、两种语言里都不会跟着别处一起调。 */
  line-height: var(--lh-14-loose);
}
</style>
