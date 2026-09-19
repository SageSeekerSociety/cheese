<script setup lang="ts">
import type { FeedbackComment, FeedbackTimelineEntry } from '@/lib/feedbackMock'

import { computed } from 'vue'

import { STATUS_META } from '@/lib/feedbackMock'
import { relTime } from '@/lib/relTime'

// 评论布局 **乙：一条流**。
//
// 状态推进和评论**混在一条竖线上**，按时间排。今天这两样是分开的：状态在右边那栏
// （进展），评论在正文里。分开的代价是「三天前有人说这个已经排期了，然后才有人问
// 什么时候上线」这种先后关系，要来回看两处才能拼出来。
//
// **选了它的话，右栏那张「进展」卡就该撤掉** —— 同一份 timeline 同时出现在两处，
// 迟早会有一处忘了跟着改（详情页现在就是这么处理的，见 FeedbackDetailPage.vue）。
//
// 形状上的两个决定：
//
//   1. 状态事件和评论**靠形状区分，不靠颜色**：状态是一颗实心点（颜色取
//      STATUS_META 的三件套，和卡片上的状态词同一个来源），评论是一颗空心圈。
//      一条全是彩色点的竖线会让人以为每个点都是状态。
//   2. 回复仍然平铺（这一版没有层级），所以照旧补一句「回复 X」。
const props = defineProps<{ comments: FeedbackComment[]; timeline: FeedbackTimelineEntry[] }>()

type Row =
  | { key: string; at: string; type: 'status'; entry: FeedbackTimelineEntry }
  | { key: string; at: string; type: 'comment'; comment: FeedbackComment }

/** 时间都是 ISO 串，字典序就是时间序 —— 不需要再 parse 成 Date 来比。 */
const rows = computed<Row[]>(() => {
  const out: Row[] = [
    ...props.timeline.map((entry, i) => ({ key: `s${i}`, at: entry.at, type: 'status' as const, entry })),
    ...props.comments.map((comment) => ({
      key: comment.id,
      at: comment.createdAt,
      type: 'comment' as const,
      comment,
    })),
  ]
  return out.sort((a, b) => a.at.localeCompare(b.at))
})

const authorOf = computed(() => new Map(props.comments.map((c) => [c.id, c.author])))
</script>

<template>
  <div v-if="rows.length" class="fb-rail">
    <div v-for="row in rows" :key="row.key" class="fb-rail__row">
      <span
        v-if="row.type === 'status'"
        class="fb-rail__dot fb-rail__dot--status"
        :style="{ '--fb-dot': STATUS_META[row.entry.status].dot }"
      />
      <span v-else class="fb-rail__dot fb-rail__dot--say" />

      <div v-if="row.type === 'status'" class="fb-rail__head">
        <span class="fb-rail__verb">{{ STATUS_META[row.entry.status].label }}</span>
        <span v-if="row.entry.by" class="t-meta">{{ row.entry.by }}</span>
        <span class="t-meta">{{ relTime(row.entry.at) }}</span>
      </div>

      <div v-else class="fb-rail__say">
        <div class="fb-rail__head">
          <span class="fb-rail__author">{{ row.comment.author }}</span>
          <span v-if="row.comment.byAgent" class="chip-neutral">AI 队友</span>
          <span v-if="row.comment.parentId" class="t-meta">
            回复 {{ authorOf.get(row.comment.parentId) ?? '已删除的评论' }}
          </span>
          <span class="t-meta">{{ relTime(row.comment.createdAt) }}</span>
        </div>
        <p class="t-body fb-rail__body">{{ row.comment.body }}</p>
      </div>
    </div>
  </div>
  <div v-else class="t-body c-faint mb-3">暂无评论</div>
</template>

<style scoped>
.fb-rail {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding-left: 24px;
  margin-bottom: 24px;
}
/* 那根竖线。上下各留 8px，不让它探出第一颗和最后一颗点 —— 探出去的那一截会读成
   「下面还有」。 */
.fb-rail::before {
  position: absolute;
  top: 8px;
  bottom: 8px;
  left: 4px;
  width: 1px;
  background: var(--line);
  content: '';
}
.fb-rail__row {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
/* 点坐在竖线的正中间：线在 4px 处、宽 1px，所以点占 0–10px 时中线是 5px，
   和线的 4.5px 差半个像素，屏幕上看不出来。 */
.fb-rail__dot {
  position: absolute;
  top: 5px;
  left: -24px;
  box-sizing: border-box;
  width: 10px;
  height: 10px;
  border-radius: 50%;
}
.fb-rail__dot--status {
  background: var(--fb-dot);
}
/* 评论是空心圈：底色用画布色把线遮断，否则线会从圈里穿过去。 */
.fb-rail__dot--say {
  background: var(--canvas);
  border: 2px solid var(--line-2);
}
.fb-rail__head {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}
/* 状态词和作者名是同一种东西：这一行的主语。两者共用一条规则，省得哪天改了一个
   忘了另一个，那两行就会不一样粗。 */
.fb-rail__verb,
.fb-rail__author {
  font-size: 13px;
  font-weight: 600;
  color: var(--ink);
}
.fb-rail__say {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.fb-rail__body {
  margin: 0;
  white-space: pre-wrap;
}
</style>
