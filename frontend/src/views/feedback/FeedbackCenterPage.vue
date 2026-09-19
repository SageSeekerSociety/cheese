<script setup lang="ts">
import type { FeedbackTab } from '@/stores/feedback'

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
defineOptions({ name: 'FeedbackCenterPage' })

const store = useFeedbackStore()
const router = useRouter()

const TABS: { value: FeedbackTab; label: string }[] = [
  { value: 'all', label: '全部' },
  { value: 'hot', label: '热门' },
  { value: 'active', label: '处理中' },
  { value: 'resolved', label: '已解决' },
]

const tabs = computed(() => TABS.map((tab) => ({ ...tab, count: store.tabCounts[tab.value] })))

// 挂载时走一遍加载。**Tab 上的计数不跟着骨架走**：它和列表读的是同一份数据，
// 而这里只有一份「在不在路上」的状态；真接口来了，两个计数会一起到，那时把它们
// 一起换成骨架才是对的。现在把它们清空反而会在挑 Tab 的那一行造成一次宽度跳动。
onMounted(() => {
  void store.load()
})

const showSubmitted = ref(false)
function onSubmitted(id: string) {
  showSubmitted.value = true
  void router.push(`/feedback/${id}`)
}

/** 空列表有两种：一条都搜不着（有筛选），和一条都没有（没筛选）。这两句话不一样，
 *  「暂无反馈」配一个「清除筛选」的按钮会让人以为清掉筛选就有东西了。 */
const hasFilter = computed(() => !!store.query.trim() || store.tab !== 'all')

function clearFilters() {
  store.query = ''
  store.tab = 'all'
}
</script>

<template>
  <div class="fb-page">
    <div class="fb-page__inner page-container">
      <header class="fb-head">
        <h1 class="t-page-title">反馈中心</h1>
        <v-spacer />
        <!-- 原型身份开关。真接上权限之后这里要整块删掉（见 stores/feedback.ts 的
             注释）：它是给人看两套界面用的，不是权限门。 -->
        <div class="fb-role">
          <span class="t-eyebrow">原型身份</span>
          <v-btn-toggle
            :model-value="store.role"
            mandatory
            density="compact"
            variant="outlined"
            divided
            @update:model-value="(v: string) => store.setRole(v as 'user' | 'admin')"
          >
            <v-btn value="user" size="x-small">用户</v-btn>
            <v-btn value="admin" size="x-small">管理员</v-btn>
          </v-btn-toggle>
          <v-btn v-if="store.role === 'admin'" variant="text" color="secondary" size="small" to="/admin/feedback">
            管理后台
          </v-btn>
        </div>
      </header>

      <div class="fb-toolbar">
        <v-text-field
          v-model="store.query"
          autocomplete="off"
          placeholder="搜索反馈"
          prepend-inner-icon="mdi-magnify"
          variant="outlined"
          density="compact"
          hide-details
          clearable
          class="fb-search"
        />
        <v-btn color="primary" prepend-icon="mdi-plus" @click="store.openSubmit()">提交反馈</v-btn>
      </div>

      <v-tabs v-model="store.tab" density="comfortable" color="primary" class="fb-tabs">
        <v-tab v-for="tab in tabs" :key="tab.value" :value="tab.value">
          {{ tab.label }}
          <span class="fb-tab-count">{{ tab.count }}</span>
        </v-tab>
      </v-tabs>

      <!-- 骨架**不放进 .fb-list**：那一层是 gap 8 的 flex 列，而骨架的行自带 8px
           下边距（它得能单独用在任何地方），两处一叠就是 16px，到货那一刻列表会
           往上收一截 —— 骨架存在的意义正是不让这件事发生。 -->
      <LoadingSkeleton v-if="store.loading" variant="feedback" :rows="6" />

      <div v-else class="fb-list">
        <FeedbackCard
          v-for="item in store.visibleItems"
          :key="item.id"
          :item="item"
          @open="router.push(`/feedback/${item.id}`)"
        />
        <div v-if="!store.visibleItems.length" class="fb-empty">
          <v-icon size="28" class="mb-2">mdi-comment-search-outline</v-icon>
          <div class="t-body">{{ hasFilter ? '暂无匹配的反馈' : '暂无反馈' }}</div>
          <v-btn v-if="hasFilter" variant="text" color="secondary" size="small" @click="clearFilters">清除筛选</v-btn>
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
.fb-role {
  display: flex;
  align-items: center;
  gap: 8px;
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
