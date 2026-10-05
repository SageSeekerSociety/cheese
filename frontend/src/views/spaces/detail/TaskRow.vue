<script setup lang="ts">
// 题目列表里的一行。左边是这道题是什么：标题、简介、谁发的、话题；右边是它现在怎样：
// 状态、领了几个人、什么时候截止。整行点进题目页，列表的筛选跟着带过去。
import type { UserRefTarget } from '@/lib/userRef'
import type { Task } from '@/types'

import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import dayjs from 'dayjs'

import { taskState } from '@/utils/tasks'

import { splitOrigin } from '@/views/spaces/model'

const props = defineProps<{
  task: Task
  /** 整行点去哪。题目页地址由容器算好传进来（带着列表的筛选，从题目页返回时列表还是原来那样）。 */
  to: UserRefTarget
}>()

const { t } = useI18n()

const state = computed(() => taskState(props.task))
const summary = computed(() => splitOrigin(props.task.intro ?? '').summary)
const publisher = computed(() => props.task.creator.nickname || props.task.creator.username)
const date = (at: number) => dayjs(at).format(t('tasks.page.dateFormat'))

const mine = computed(() => !!props.task.joined || !!props.task.joinedTeams?.length)

const claimed = computed(() =>
  props.task.participantLimit > 0
    ? t('spaces.detail.tasks.row.claimedOf', { n: props.task.participants.total, limit: props.task.participantLimit })
    : t('spaces.detail.tasks.row.claimed', { n: props.task.participants.total })
)

const deadline = computed(() =>
  props.task.deadline == null
    ? t('spaces.detail.tasks.row.noDeadline')
    : t('spaces.detail.tasks.row.deadline', { date: date(props.task.deadline) })
)
</script>

<template>
  <router-link :to="to" class="tr">
    <div class="tr__main">
      <div class="tr__title">
        <span class="tr__name">{{ task.name }}</span>
        <span v-if="mine" class="tr__mine">{{ t('spaces.detail.tasks.row.mine') }}</span>
      </div>
      <p v-if="summary" class="tr__intro">{{ summary }}</p>
      <div class="tr__meta">
        <span>{{ t('spaces.detail.tasks.row.by', { name: publisher }) }}</span>
        <span>{{ date(task.createdAt) }}</span>
        <span v-for="topic in task.topics ?? []" :key="topic.id" class="tr__topic">{{ topic.name }}</span>
      </div>
    </div>
    <div class="tr__side">
      <span class="tr__state" :class="`tr__state--${state.tone}`">
        <span class="tr__dot" aria-hidden="true" />{{ t(`tasks.page.state.${state.key}`) }}
      </span>
      <span class="t-num">{{ claimed }}</span>
      <span>{{ deadline }}</span>
    </div>
  </router-link>
</template>

<style scoped>
.tr {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 160px;
  gap: 24px;
  padding: 14px 8px;
  border-bottom: 1px solid var(--line);
  color: inherit;
  text-decoration: none;
  transition: background-color var(--dur-quick) var(--ease-standard);
}
.tr:hover {
  background: var(--fill);
}
.tr:focus-visible {
  outline: 2px solid var(--focus-ring);
  outline-offset: -2px;
}
.tr__main {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}
.tr__title {
  display: flex;
  gap: 8px;
  align-items: baseline;
  min-width: 0;
}
.tr__name {
  overflow: hidden;
  color: var(--ink);
  font-size: 15px;
  font-weight: 600;
  line-height: var(--lh-15);
  text-overflow: ellipsis;
  white-space: nowrap;
}
.tr__mine {
  flex: none;
  padding: 0 6px;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-sm);
  color: var(--muted);
  font-size: 12px;
  line-height: var(--lh-12);
}
.tr__intro {
  display: -webkit-box;
  margin: 0;
  overflow: hidden;
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}
.tr__meta {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 6px;
  align-items: center;
  color: var(--muted);
  font-size: 12px;
  line-height: var(--lh-12);
}
.tr__meta > span:nth-child(2)::before {
  margin-right: 6px;
  content: '·';
}
.tr__topic {
  padding: 0 6px;
  border-radius: var(--radius-sm);
  background: var(--fill-2);
}
.tr__side {
  display: flex;
  flex-direction: column;
  gap: 2px;
  align-items: flex-end;
  justify-content: center;
  color: var(--muted);
  font-size: 12px;
  line-height: var(--lh-12);
  white-space: nowrap;
}
.tr__state {
  display: inline-flex;
  gap: 6px;
  align-items: center;
  font-size: 13px;
  line-height: var(--lh-13);
}
.tr__dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--faint);
}
.tr__state--ok {
  color: var(--ok-ink);
}
.tr__state--ok .tr__dot {
  background: var(--ok);
}
.tr__state--danger {
  color: var(--danger-ink);
}
.tr__state--danger .tr__dot {
  background: var(--danger);
}

/* 手机上右边那一栏落到下面，排成一行。 */
@media (max-width: 599px) {
  .tr {
    grid-template-columns: minmax(0, 1fr);
    gap: 8px;
  }
  .tr__side {
    flex-direction: row;
    flex-wrap: wrap;
    gap: 4px 12px;
    align-items: center;
    justify-content: flex-start;
  }
}
</style>
