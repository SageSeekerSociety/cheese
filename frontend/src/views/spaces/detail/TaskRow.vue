<script setup lang="ts">
// 题目列表里的一行。左边是这道题是什么：标题、简介、谁发的、话题；右边是它现在怎样：
// 状态、几个人在做、什么时候截止。整行点进题目页，列表的筛选跟着带过去。
//
// 我手上有一份的题，这一行说我这一份：标题旁是我的状态（在做、已过期……），日期是我自己的提交截止，
// 右边不写题目对别人开着还是截止。别的题说题目自己：右边是题目的状态，日期说清是申请截止还是截止。
import type { UserRefTarget } from '@/lib/userRef'
import type { Task } from '@/types'

import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import dayjs from 'dayjs'

import { listDeadline, myClaimState, taskState } from '@/utils/tasks'

import { splitOrigin } from '@/views/spaces/model'

const props = defineProps<{
  task: Task
  /** 整行点去哪。题目页地址由容器算好传进来（带着列表的筛选，从题目页返回时列表还是原来那样）。 */
  to: UserRefTarget
}>()

const { t } = useI18n()

/** 我这一份的状态（`myClaimState`）；没领过是 null。 */
const mine = computed(() => myClaimState(props.task))
/** 题目自己的状态（`taskState`），和题目页标题旁那一枚是同一个；我手上有一份的题不写。 */
const state = computed(() => (mine.value ? null : taskState(props.task)))
const summary = computed(() => splitOrigin(props.task.intro ?? '').summary)
const publisher = computed(() => props.task.creator.nickname || props.task.creator.username)
const date = (at: number) => dayjs(at).format(t('tasks.page.dateFormat'))

/** 标签文案。`joined` 只算没被拒的申请（个人或所在团队的）；`joinedTeams` 不看审核，所以不拿它判。 */
const MINE_KEY = {
  applied: 'spaces.detail.tasks.row.applied',
  doing: 'spaces.detail.tasks.row.mine',
  submitted: 'spaces.detail.tasks.row.submitted',
  passed: 'spaces.detail.tasks.row.passed',
  expired: 'spaces.detail.tasks.row.expired',
  ended: 'spaces.detail.tasks.row.ended',
} as const

/** 参与的是人还是队，按题目的形式说：团队题数的是队伍。 */
const claimed = computed(() => {
  const n = props.task.participants.approved
  const limit = props.task.participantLimit
  const teams = props.task.submitterType === 'TEAM'
  if (limit > 0)
    return t(teams ? 'spaces.detail.tasks.row.claimedTeamsOf' : 'spaces.detail.tasks.row.claimedOf', { n, limit })
  return t(teams ? 'spaces.detail.tasks.row.claimedTeams' : 'spaces.detail.tasks.row.claimed', { n })
})

/** 截止那一格的文案：说清是哪一个截止（Zhifei 2026-10-26）。 */
const DEADLINE_KEY = {
  apply: 'spaces.detail.tasks.row.applyDeadline',
  submit: 'spaces.detail.tasks.row.submitDeadline',
  task: 'spaces.detail.tasks.row.deadline',
} as const

const deadline = computed(() => {
  const { kind, at } = listDeadline(props.task)
  return at == null ? t('spaces.detail.tasks.row.noDeadline') : t(DEADLINE_KEY[kind], { date: date(at) })
})
</script>

<template>
  <router-link :to="to" class="tr">
    <div class="tr__main">
      <div class="tr__title">
        <span class="tr__name">{{ task.name }}</span>
        <span v-if="mine" class="tr__mine">{{ t(MINE_KEY[mine]) }}</span>
      </div>
      <p v-if="summary" class="tr__intro">{{ summary }}</p>
      <div class="tr__meta">
        <span>{{ t('spaces.detail.tasks.row.by', { name: publisher }) }}</span>
        <span>{{ date(task.createdAt) }}</span>
        <span v-for="topic in task.topics ?? []" :key="topic.id" class="tr__topic">{{ topic.name }}</span>
      </div>
    </div>
    <div class="tr__side">
      <span v-if="state" class="tr__state" :class="`tr__state--${state.tone}`">
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
