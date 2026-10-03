<script setup lang="ts">
// 待审核列表里的一行。收起时和题目列表的一行一样：左边标题、简介、谁发的、话题，右边
// 个人还是团队、什么时候截止。点开看全题（设置、描述、提交要求），在最底下通过或驳回。
import type { Task } from '@/types'

import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import dayjs from 'dayjs'

import BaseButton from '@/components/base/BaseButton.vue'
import TaskDescription from '@/components/tasks/TaskDescription.vue'

const props = defineProps<{ task: Task; open: boolean }>()
defineEmits<{ toggle: []; approve: []; reject: [] }>()

const { t } = useI18n()

const date = (at: number) => dayjs(at).format('MM-DD HH:mm')
const publisher = computed(() => props.task.creator.nickname || props.task.creator.username)
const bodyId = computed(() => `audit-task-${props.task.id}`)

/** 这道题的几项设置，一项一句，排成一行。 */
const facts = computed(() => {
  const task = props.task
  const team = task.submitterType === 'TEAM'
  return [
    t(team ? 'spaces.detail.auditTasks.teamContest' : 'spaces.detail.auditTasks.individualContest'),
    team && task.teamLockingPolicy
      ? t(
          task.teamLockingPolicy === 'NO_LOCK'
            ? 'spaces.detail.auditTasks.teamLockingPolicyNoLock'
            : 'spaces.detail.auditTasks.teamLockingPolicyLockOnApproval'
        )
      : '',
    task.requireRealName ? t('spaces.detail.auditTasks.requireRealName') : '',
    task.resubmittable ? t('spaces.detail.auditTasks.resubmittable') : '',
    task.editable ? t('spaces.detail.auditTasks.editableSubmission') : '',
  ].filter(Boolean)
})
</script>

<template>
  <article class="ar" :class="{ 'ar--open': open }">
    <button type="button" class="ar__head" :aria-expanded="open" :aria-controls="bodyId" @click="$emit('toggle')">
      <span class="ar__main">
        <span class="ar__name">{{ task.name }}</span>
        <span v-if="task.intro" class="ar__intro">{{ task.intro }}</span>
        <span class="ar__meta">
          <span>{{ t('spaces.detail.auditTasks.by', { name: publisher }) }}</span>
          <span>{{ date(task.createdAt) }}</span>
          <span v-if="task.category" class="ar__tag">{{ task.category.name }}</span>
          <span v-for="topic in task.topics ?? []" :key="topic.id" class="ar__tag">{{ topic.name }}</span>
        </span>
      </span>
      <span class="ar__side">
        <span>{{ facts[0] }}</span>
        <span>{{
          task.deadline == null
            ? t('spaces.detail.auditTasks.noDeadline')
            : t('spaces.detail.auditTasks.deadline', { date: date(task.deadline) })
        }}</span>
        <v-icon :icon="open ? 'mdi-chevron-up' : 'mdi-chevron-down'" size="18" class="ar__chevron" />
      </span>
    </button>

    <div v-if="open" :id="bodyId" class="ar__body">
      <p v-if="facts.length > 1" class="ar__facts">{{ facts.slice(1).join(' · ') }}</p>

      <section>
        <h4 class="ar__label">{{ t('spaces.detail.auditTasks.description') }}</h4>
        <TaskDescription :source="task.description" :empty="t('spaces.detail.auditTasks.noDescription')" />
      </section>

      <section>
        <h4 class="ar__label">{{ t('spaces.detail.auditTasks.submissionRequirements') }}</h4>
        <ol v-if="task.submissionSchema.length" class="ar__schema">
          <li v-for="(entry, index) in task.submissionSchema" :key="index">
            <span class="ar__type">{{ t(`spaces.detail.auditTasks.entryType.${entry.type}`) }}</span>
            <span>{{ entry.prompt }}</span>
          </li>
        </ol>
        <p v-else class="ar__none">{{ t('spaces.detail.auditTasks.noRequirements') }}</p>
      </section>

      <div class="ar__actions">
        <BaseButton kind="ghost" @click="$emit('reject')">
          {{ t('spaces.detail.auditTasks.reject') }}
        </BaseButton>
        <BaseButton kind="primary" @click="$emit('approve')">
          {{ t('spaces.detail.auditTasks.approve') }}
        </BaseButton>
      </div>
    </div>
  </article>
</template>

<style scoped>
.ar {
  border-bottom: 1px solid var(--line);
}

.ar__head {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 24px;
  width: 100%;
  padding: 14px 8px;
  border: 0;
  background: none;
  color: inherit;
  font: inherit;
  text-align: left;
  cursor: pointer;
  transition: background-color var(--dur-quick) var(--ease-standard);
}

.ar__head:hover,
.ar--open .ar__head {
  background: var(--fill);
}

.ar__head:focus-visible {
  outline: 2px solid var(--focus-ring);
  outline-offset: -2px;
}

.ar__main {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.ar__name {
  overflow: hidden;
  color: var(--ink);
  font-size: 15px;
  font-weight: 600;
  line-height: var(--lh-15);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ar__intro {
  display: -webkit-box;
  overflow: hidden;
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}

.ar__meta {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 6px;
  align-items: center;
  color: var(--muted);
  font-size: 12px;
  line-height: var(--lh-12);
}

.ar__meta > span:nth-child(2)::before {
  margin-right: 6px;
  content: '·';
}

.ar__tag {
  padding: 0 6px;
  border-radius: var(--radius-sm);
  background: var(--fill-2);
}

.ar__side {
  display: grid;
  grid-template-columns: auto 18px;
  gap: 2px 8px;
  align-content: center;
  justify-items: end;
  color: var(--muted);
  font-size: 12px;
  line-height: var(--lh-12);
  white-space: nowrap;
}

.ar__chevron {
  grid-row: 1 / span 2;
  grid-column: 2;
  align-self: center;
  color: var(--faint);
}

.ar__body {
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: 4px 8px 20px;
}

.ar__facts {
  margin: 0;
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
}

.ar__label {
  margin: 0 0 6px;
  color: var(--ink);
  font-size: 13px;
  font-weight: 600;
  line-height: var(--lh-13);
}

.ar__schema {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin: 0;
  padding: 0;
  list-style: none;
  font-size: 14px;
  line-height: var(--lh-14);
}

.ar__schema li {
  display: flex;
  gap: 8px;
  align-items: baseline;
}

.ar__type {
  flex: none;
  padding: 0 6px;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-sm);
  color: var(--muted);
  font-size: 12px;
  line-height: var(--lh-12);
}

.ar__none {
  margin: 0;
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
}

.ar__actions {
  display: flex;
  gap: 8px;
  justify-content: flex-end;
}

/* 手机上右边那一栏落到下面，排成一行。 */
@media (max-width: 599px) {
  .ar__head {
    grid-template-columns: minmax(0, 1fr);
    gap: 8px;
  }

  .ar__side {
    display: flex;
    flex-wrap: wrap;
    gap: 4px 12px;
    justify-content: flex-start;
  }

  .ar__chevron {
    display: none;
  }
}
</style>
