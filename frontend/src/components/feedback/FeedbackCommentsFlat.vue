<script setup lang="ts">
import type { FeedbackComment } from '@/lib/feedbackMock'

import { computed } from 'vue'

import { relTime } from '@/lib/relTime'

// 评论布局 **甲：平铺**。
//
// 就是详情页一直在用的那个形状，这次单独拆成一个组件，好和另外两版并排比较。
// 它按时间从头到尾排一条线，**不表达回复关系**——所以有回复时得在作者后面补一句
// 「回复 X」，否则读的人不知道这条在接谁的话。
//
// 选它的理由：读一条反馈的讨论时，人是顺着时间读的，「谁先说的」几乎总是比「谁在
// 回答谁」更重要。代价也很直白：讨论一旦分叉成几条线，平铺会把它们搅在一起。
//
// 另外两版见 FeedbackCommentsRail.vue（状态与评论一条流）和
// FeedbackCommentsThread.vue（两层折叠）。**这一版是这三版里的默认**。
const props = defineProps<{ comments: FeedbackComment[] }>()

/** 「回复 X」要显示的是**被回复那条的作者**，不是 id——id 对人没有意义。 */
const authorOf = computed(() => new Map(props.comments.map((c) => [c.id, c.author])))
</script>

<template>
  <TransitionGroup v-if="comments.length" name="fb-say" tag="div" class="fb-say">
    <div v-for="c in comments" :key="c.id" class="fb-say__item">
      <div class="fb-say__head">
        <span class="fb-say__author">{{ c.author }}</span>
        <span v-if="c.byAgent" class="chip-neutral">AI 队友</span>
        <span v-if="c.parentId" class="t-meta">回复 {{ authorOf.get(c.parentId) ?? '已删除的评论' }}</span>
        <span class="t-meta">{{ relTime(c.createdAt) }}</span>
      </div>
      <p class="t-body fb-say__body">{{ c.body }}</p>
    </div>
  </TransitionGroup>
  <div v-else class="t-body c-faint mb-3">暂无评论</div>
</template>

<style scoped>
.fb-say {
  display: flex;
  flex-direction: column;
  gap: 16px;
  margin-bottom: 24px;
}
.fb-say__item {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.fb-say__head {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}
.fb-say__author {
  font-size: 13px;
  font-weight: 600;
  color: var(--ink);
}
.fb-say__body {
  margin: 0;
  white-space: pre-wrap;
}

/* 新评论淡入并上移 4px。**这是状态变化，不是入场装饰**：TransitionGroup 不加
   `appear`，所以初始渲染不播，只有刚发出去的那一条会动 —— 它标的是「这一条是新的」，
   而 docs/design-system.md §9.6 删掉的是页面的装饰性入场淡入，不是这个。 */
.fb-say-enter-active {
  transition:
    opacity 0.2s ease,
    transform 0.2s ease;
}
.fb-say-enter-from {
  opacity: 0;
  transform: translateY(4px);
}
</style>
