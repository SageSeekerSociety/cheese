<script setup lang="ts">
// 「我发布的」里的一行：和普通的题目行同一个外形，右边换成出题人关心的——审核到哪一步、
// 有几件事在等他。待批准的领取和待评审的提交都在题目页的「领取者」页签里处理。
import type { SpaceMyPublishedTask, SpaceTaskVisibilityStatus } from '@/network/api/spaces/types'

import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import dayjs from 'dayjs'

const props = defineProps<{
  task: SpaceMyPublishedTask
  spaceId: number
}>()

const { t } = useI18n()

const TONE: Record<SpaceTaskVisibilityStatus, 'ok' | 'muted' | 'danger'> = {
  PENDING_APPROVAL: 'muted',
  REJECTED: 'danger',
  APPROVED_HIDDEN: 'muted',
  APPROVED_VISIBLE: 'ok',
  ENDED: 'muted',
}

const date = (at: number) => dayjs(at).format(t('tasks.page.dateFormat'))
const params = computed(() => ({ spaceId: props.spaceId, taskId: props.task.taskId }))
const percent = (value: number) => `${Math.round((value || 0) * 100)}%`

const stats = computed(() => {
  const task = props.task
  return [
    t('spaces.detail.tasks.published.claimed', { n: task.participantCount }),
    t('spaces.detail.tasks.published.approved', { n: task.approvedParticipantCount }),
    t('spaces.detail.tasks.published.submitted', { n: task.submittedParticipantCount }),
    t('spaces.detail.tasks.published.passed', { n: task.successfulParticipantCount }),
    t('spaces.detail.tasks.published.failed', { n: task.failedParticipantCount }),
    t('spaces.detail.tasks.published.submitRate', { rate: percent(task.submissionConversionRate) }),
    t('spaces.detail.tasks.published.passRate', { rate: percent(task.successRate) }),
  ]
})
</script>

<template>
  <div class="pr">
    <div class="pr__main">
      <router-link :to="{ name: 'TasksDetail', params }" class="pr__name">{{ task.taskName }}</router-link>
      <div class="pr__meta">
        <span class="pr__tag">{{ task.category.name }}</span>
        <span>{{
          task.publishedAt
            ? t('spaces.detail.tasks.published.publishedOn', { date: date(task.publishedAt) })
            : t('spaces.detail.tasks.published.createdOn', { date: date(task.createdAt) })
        }}</span>
        <span>{{
          task.deadline
            ? t('spaces.detail.tasks.row.deadline', { date: date(task.deadline) })
            : t('spaces.detail.tasks.row.noDeadline')
        }}</span>
      </div>
      <div class="pr__stats t-num">
        <span v-for="stat in stats" :key="stat">{{ stat }}</span>
      </div>
    </div>
    <div class="pr__side">
      <span class="pr__state" :class="`pr__state--${TONE[task.visibilityStatus]}`">
        <span class="pr__dot" aria-hidden="true" />{{
          t(`spaces.detail.tasks.published.state.${task.visibilityStatus}`)
        }}
      </span>
      <router-link
        v-if="task.pendingParticipantApprovalCount > 0"
        :to="{ name: 'TasksParticipants', params }"
        class="pr__todo"
      >
        {{ t('spaces.detail.tasks.published.pendingClaims', { n: task.pendingParticipantApprovalCount }) }}
      </router-link>
      <router-link v-if="task.pendingReviewCount > 0" :to="{ name: 'TasksParticipants', params }" class="pr__todo">
        {{ t('spaces.detail.tasks.published.pendingReviews', { n: task.pendingReviewCount }) }}
      </router-link>
    </div>
  </div>
</template>

<style scoped>
.pr {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 160px;
  gap: 24px;
  padding: 14px 8px;
  border-bottom: 1px solid var(--line);
}
.pr__main {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}
.pr__name {
  overflow: hidden;
  color: var(--ink);
  font-size: 15px;
  font-weight: 600;
  line-height: var(--lh-15);
  text-decoration: none;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.pr__name:hover {
  text-decoration: underline;
  text-underline-offset: 3px;
}
.pr__meta,
.pr__stats {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 12px;
  align-items: center;
  color: var(--muted);
  font-size: 12px;
  line-height: var(--lh-12);
}
.pr__stats {
  color: var(--faint);
}
.pr__tag {
  padding: 0 6px;
  border-radius: var(--radius-sm);
  background: var(--fill-2);
}
.pr__side {
  display: flex;
  flex-direction: column;
  gap: 4px;
  align-items: flex-end;
  justify-content: center;
  white-space: nowrap;
}
.pr__state {
  display: inline-flex;
  gap: 6px;
  align-items: center;
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
}
.pr__dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--faint);
}
.pr__state--ok {
  color: var(--ok-ink);
}
.pr__state--ok .pr__dot {
  background: var(--ok);
}
.pr__state--danger {
  color: var(--danger-ink);
}
.pr__state--danger .pr__dot {
  background: var(--danger);
}
.pr__todo {
  color: var(--warn-ink);
  font-size: 12px;
  font-weight: 600;
  line-height: var(--lh-12);
  text-decoration: none;
}
.pr__todo:hover {
  text-decoration: underline;
  text-underline-offset: 3px;
}

/* 断点收进共享 token：767.98 = $bp-phone（styles/breakpoints.scss）。 */
@media (max-width: 767.98px) {
  .pr {
    grid-template-columns: minmax(0, 1fr);
    gap: 8px;
  }
  .pr__side {
    flex-direction: row;
    flex-wrap: wrap;
    gap: 4px 12px;
    align-items: center;
    justify-content: flex-start;
  }
}
</style>
