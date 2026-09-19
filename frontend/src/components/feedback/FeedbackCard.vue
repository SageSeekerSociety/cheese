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
// 看哪一条的依据。所以它在卡片最左边、是这张卡上唯一一个带底色的按钮，其余全是文字。
//
// 「已支持」用**中性色**（tonal + secondary），不用琥珀：一屏里琥珀只给唯一的主操作
// （这一页是「提交反馈」），支持是一个可反复切换的状态，它变琥珀会让主操作不再是唯一
// 那个显眼的东西（docs/design-system.md §0）。状态本身有三个不依赖颜色的信号：实心
// 的拇指图标、文字、以及计数变 --ink。
//
// 整张卡可以点开，支持按钮要 `@click.stop` —— 少了那个 stop，点「支持」会顺手把
// 详情页也打开。
//
// **私密反馈**（只可能是自己提的，别人的根本不在这个列表里）走另一套：那一条不该
// 让人知道它存在，所以它没有支持按钮 —— 支持是公开表态，它决定「热门」怎么排、
// 管理员先看哪条。锁图标占着支持那一列的位置，是为了不让这张卡的正文比上下每
// 一张都左移 40px：那看起来像排版坏了，而不是「这一条不一样」。
const props = defineProps<{ item: FeedbackItem }>()
const emit = defineEmits<{ (e: 'open', id: string): void }>()

const store = useFeedbackStore()

const commentCount = computed(() => props.item.comments.length)
/** 「已解决」的反馈在列表里不再喊人支持：它已经做完了。 */
const supportable = computed(() => props.item.status !== 'resolved')
const isPrivate = computed(() => props.item.visibility === 'private')
const PRIVATE_HINT = '私密反馈：只有你和管理员能看到，其他人看不到它'
</script>

<template>
  <v-card class="fb-card" @click="emit('open', item.id)">
    <div class="fb-card__support">
      <template v-if="isPrivate">
        <v-icon size="18" class="fb-card__lock" :title="PRIVATE_HINT" :aria-label="PRIVATE_HINT"
          >mdi-lock-outline</v-icon
        >
      </template>
      <template v-else>
        <v-btn
          icon
          size="small"
          :variant="item.supportedByMe ? 'tonal' : 'outlined'"
          color="secondary"
          :disabled="!supportable"
          :aria-label="item.supportedByMe ? '取消支持' : '支持这个反馈'"
          :title="supportable ? (item.supportedByMe ? '取消支持' : '支持') : '已解决，无需再支持'"
          @click.stop="store.toggleSupport(item.id)"
        >
          <v-icon size="18">{{ item.supportedByMe ? 'mdi-thumb-up' : 'mdi-thumb-up-outline' }}</v-icon>
        </v-btn>
        <span class="fb-card__count" :class="{ 'c-muted': !item.supportedByMe }">{{ item.supports }}</span>
      </template>
    </div>

    <div class="fb-card__body">
      <div class="d-flex align-center flex-wrap ga-2 mb-1">
        <span class="fb-card__title">{{ item.title }}</span>
        <FeedbackStatusChip :status="item.status" />
      </div>
      <p class="fb-card__summary">{{ item.summary }}</p>
      <div class="d-flex align-center flex-wrap ga-3">
        <span class="t-meta">{{ item.author }} · {{ relTime(item.createdAt) }}</span>
        <!-- 中性色，不是警告色：私密是一个事实（这条只有我能看见），不是一件
             需要被纠正的事。写在作者名之后，因为它回答的正是「这条谁看得见」，
             和旁边的「谁提的」是同一类信息。 -->
        <span v-if="isPrivate" class="chip-neutral" :title="PRIVATE_HINT">
          <v-icon size="12">mdi-lock-outline</v-icon>私密
        </span>
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
/* 这里**不写** border-radius：VCard 的默认 `rounded="xl"`（24px）走的是
   `.rounded-xl { border-radius: 24px !important }`，scoped 里写的 12px 压不过它，
   写了只是死代码（曾经就有一行这样的）。改圆角得改 plugins/vuetify.ts 的默认值，
   那是全仓 v-card 的事，不在这个组件里做。 */
.fb-card {
  display: flex;
  gap: 12px;
  padding: 16px;
  cursor: pointer;
}
/* hover 只换底色和描边色，不位移：列表一屏十几行，每行抬 2px 会看成整列在跳
   （docs/design-system.md §9.1）。 */
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
/* 锁占的是支持按钮那一格，所以它得跟着那一格居中。--faint 是刻意的：它是「这里
   没有按钮」的说明，不是提示，比正文更轻才对。 */
.fb-card__lock {
  color: var(--faint);
}
.fb-card__count {
  font-family: var(--font-mono);
  font-size: 12px;
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
  font-size: 13px;
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
