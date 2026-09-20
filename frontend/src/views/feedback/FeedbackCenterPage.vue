<script setup lang="ts">
import type { FeedbackTab as TabName } from '@/stores/feedback'

import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import LoadingSkeleton from '@/components/common/LoadingSkeleton.vue'
import FeedbackCard from '@/components/feedback/FeedbackCard.vue'
import SubmitFeedbackDrawer from '@/components/feedback/SubmitFeedbackDrawer.vue'
import { useFeedbackStore } from '@/stores/feedback'

// 反馈中心首页 (/feedback)。
//
// 它是一个**独立完整页面**：不套工作区那套左侧话题导航，也不进任何项目上下文。
// 反馈说的是平台本身，跟「我现在在哪个项目里」没有关系。
//
// 页面结构刻意只有三层（标题行 / Tab / 列表）。多一层筛选条就会让「我要找的那条
// 在哪」变成一个需要读三处才能回答的问题，而反馈中心的全部价值就是让人**快速找到
// 已经有人提过的同一件事**，然后支持它，而不是再提一条。
//
// 上一轮这里有一个「原型身份」开关（user / admin），那是给人看两套界面的道具。现在
// 权限在服务端：`store.isAdmin` 读 `GET /feedback/meta`，管理后台的入口跟着它出现
// 或者不出现 —— 客户端不再猜，也不是拨一下就能看见的。
defineOptions({ name: 'FeedbackCenterPage' })

const store = useFeedbackStore()
const router = useRouter()

/** 栏位的中文名。**有哪些栏位**来自服务端（`meta.tabs`），这里只负责把它们叫成
 *  人话；服务端多出一个栏位时标成它自己的名字，而不是不显示（少一个 Tab 比多一个
 *  写着生词的好，因为少的那一个没有任何地方提示它存在过）。 */
const TAB_LABELS: Record<string, string> = {
  all: '全部',
  hot: '热门',
  active: '处理中',
  resolved: '已解决',
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

const showSubmitted = ref(false)
function onSubmitted(id: string) {
  showSubmitted.value = true
  void router.push(`/feedback/${id}`)
}

/** 空列表有三种，说的话不一样：「一条都没有」「筛选之后没有」「没拉到」。
 *  把它们合成一句「暂无反馈」的话，最后一种会看着像平台真的没有反馈。 */
const hasFilter = computed(() => !!store.query.trim() || store.tab !== 'all')

function clearFilters() {
  store.tab = 'all'
  if (store.query) void store.clearQuery()
  else void store.loadList()
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
        <!-- 数据关系与架构关系那两张图。放在这里而不是页脚：它是给人核对实现用的，
             不是「相关链接」，藏在页脚就没人会点。 -->
        <v-btn variant="text" color="secondary" size="small" to="/design/feedback"> 数据与架构 </v-btn>
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
        <v-btn color="primary" prepend-icon="mdi-plus" @click="store.openSubmit()">提交反馈</v-btn>
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

      <!-- 骨架**不放进 .fb-list**：那一层是 gap 8 的 flex 列，而骨架的行自带 8px
           下边距（它得能单独用在任何地方），两处一叠就是 16px，到货那一刻列表会
           往上收一截 —— 骨架存在的意义正是不让这件事发生。 -->
      <!-- 列表**非空**时的失败也要画出来。以前 error 只在下面那块「一条也没有」里
           渲染，于是从卡片上点「支持」失败（已解决的条目回 412）时页面上什么都不动：
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

      <LoadingSkeleton v-if="store.loading" variant="feedback" :rows="6" />

      <div v-else class="fb-list">
        <FeedbackCard
          v-for="item in store.items"
          :key="item.id"
          :item="item"
          @open="router.push(`/feedback/${item.id}`)"
        />
        <div v-if="!store.items.length" class="fb-empty">
          <v-icon size="28" class="mb-2">
            {{ store.error ? 'mdi-alert-circle-outline' : 'mdi-comment-search-outline' }}
          </v-icon>
          <div class="t-body">
            {{ store.error ? store.error : hasFilter ? '暂无匹配的反馈' : '暂无反馈' }}
          </div>
          <v-btn v-if="store.error" variant="text" color="secondary" size="small" @click="store.loadList()">
            重试
          </v-btn>
          <v-btn v-else-if="hasFilter" variant="text" color="secondary" size="small" @click="clearFilters">
            清除筛选
          </v-btn>
        </div>
      </div>

      <!-- 页脚在加载时先不画：它是「读完了、下面是空的」这句话的一部分，
           跟着骨架一起出现等于提前说了还没到的话。 -->
      <p v-if="!store.loading" class="t-meta fb-foot">
        公开反馈所有人可见；提交时选「私密」的只有你和管理员能看到，别人搜不到、也拿不到链接
      </p>
    </div>

    <SubmitFeedbackDrawer @submitted="onSubmitted" />

    <v-snackbar v-model="showSubmitted" color="success" :timeout="4000">反馈已提交</v-snackbar>
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
  gap: 8px;
}
.fb-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 48px 0;
  color: var(--faint);
}
.fb-foot {
  margin: 24px 0 0;
  line-height: 1.7;
}
</style>
