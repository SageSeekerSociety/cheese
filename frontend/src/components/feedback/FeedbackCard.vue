<script setup lang="ts">
import type { FeedbackItem } from '@/lib/feedbackMock'

import { computed } from 'vue'

import FeedbackStatusChip from './FeedbackStatusChip.vue'

import { SOURCE_LABEL } from '@/lib/feedbackMock'
import { relTime } from '@/lib/relTime'
import { useFeedbackStore } from '@/stores/feedback'

// 反馈中心列表里的一行。
//
// 左边那一列是**支持**，不是点赞：支持数决定排序（热门 Tab），也是管理员判断该先
// 看哪一条的依据。所以它在卡片最左边、是这张卡上唯一一个实心按钮，其余全是文字。
//
// 整张卡可以点开，支持按钮要 `@click.stop` —— 少了那个 stop，点「支持」会顺手把
// 详情页也打开。
const props = defineProps<{ item: FeedbackItem }>()
const emit = defineEmits<{ (e: 'open', id: string): void }>()

const store = useFeedbackStore()

const commentCount = computed(() => props.item.comments.length)
/** 「已解决」的反馈在列表里不再喊人支持：它已经做完了。 */
const supportable = computed(() => props.item.status !== 'resolved')
</script>

<template>
  <v-card class="fb-card" @click="emit('open', item.id)">
    <div class="fb-card__support">
      <v-btn
        icon
        size="small"
        variant="outlined"
        :color="item.supportedByMe ? 'primary' : 'secondary'"
        :disabled="!supportable"
        :aria-label="item.supportedByMe ? '取消支持' : '支持这个反馈'"
        :title="supportable ? (item.supportedByMe ? '取消支持' : '支持') : '已解决，无需再支持'"
        @click.stop="store.toggleSupport(item.id)"
      >
        <v-icon size="18">{{ item.supportedByMe ? 'mdi-thumb-up' : 'mdi-thumb-up-outline' }}</v-icon>
      </v-btn>
      <span class="fb-card__count" :class="{ 'c-muted': !item.supportedByMe }">{{ item.supports }}</span>
    </div>

    <div class="fb-card__body">
      <div class="d-flex align-center flex-wrap ga-2 mb-1">
        <span class="fb-card__title">{{ item.title }}</span>
        <FeedbackStatusChip :status="item.status" />
      </div>
      <p class="fb-card__summary">{{ item.summary }}</p>
      <div class="d-flex align-center flex-wrap ga-3">
        <span class="t-meta">{{ item.author }} · {{ relTime(item.createdAt) }}</span>
        <span class="t-meta d-inline-flex align-center ga-1">
          <v-icon size="13">mdi-comment-outline</v-icon>{{ commentCount }}
        </span>
        <span v-if="item.source === 'agent'" class="chip-neutral">
          <v-icon size="12">mdi-robot-outline</v-icon>{{ SOURCE_LABEL.agent }}
        </span>
        <span v-for="tag in item.tags" :key="tag" class="chip-neutral">{{ tag }}</span>
      </div>
    </div>
  </v-card>
</template>

<style scoped>
.fb-card {
  display: flex;
  gap: 12px;
  padding: 14px 16px;
  cursor: pointer;
  border-radius: var(--radius-lg);
}
.fb-card:hover {
  border-color: var(--line-2);
  background: var(--fill);
}
.fb-card__support {
  display: flex;
  flex: none;
  flex-direction: column;
  align-items: center;
  gap: 2px;
  padding-top: 2px;
}
.fb-card__count {
  font-family: var(--font-mono);
  font-size: 12.5px;
  color: var(--ink);
  font-variant-numeric: tabular-nums;
}
.fb-card__body {
  min-width: 0;
  flex: 1;
}
.fb-card__title {
  font-size: 15px;
  font-weight: 600;
  color: var(--ink);
  line-height: 1.4;
}
.fb-card__summary {
  margin: 0 0 8px;
  font-size: 13.5px;
  line-height: 1.6;
  color: var(--muted);
  /* 摘要只给两行：列表是用来扫的，一条把四行读完就没有列表的意义了。 */
  display: -webkit-box;
  -webkit-line-clamp: 2;
  line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
</style>
