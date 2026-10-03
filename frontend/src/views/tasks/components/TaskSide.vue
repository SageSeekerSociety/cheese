<template>
  <aside class="ts">
    <!-- 我的进度：领了（或申请了）才有。用哪个团队领的、交到第几版、剩几天、从这道题开的项目。 -->
    <section v-if="identity" class="ts__block">
      <h2 class="ts__h t-eyebrow-read">{{ t('tasks.side.progress') }}</h2>
      <div class="ts__mine">
        <div class="ts__mine-head">
          <strong>{{
            identity.type === 'TEAM' ? identity.teamName || t('tasks.side.unnamedTeam') : t('tasks.side.individual')
          }}</strong>
          <span v-if="remaining" class="t-meta-read t-num">{{ remaining }}</span>
          <AdaptiveMenu v-if="identity.approved === 'APPROVED'" :actions="mineActions">
            <template #activator="{ props: menu }">
              <BaseButton v-bind="menu" icon="mdi-dots-horizontal" size="sm" :aria-label="t('tasks.side.more')" />
            </template>
          </AdaptiveMenu>
        </div>
        <p class="ts__status" :class="`ts__status--${status.tone}`">{{ status.label }}</p>

        <template v-if="identity.approved === 'APPROVED'">
          <p v-if="projectsFailed" class="ts__note">
            {{ t('tasks.side.projectsFailed') }}
            <BaseButton kind="secondary" size="sm" @click="loadProjects">{{ t('tasks.side.retry') }}</BaseButton>
          </p>
          <router-link v-for="p in projects" :key="p.id" :to="`/projects/${p.id}`" class="ts__project">
            <v-icon size="14">mdi-folder-outline</v-icon>
            <span>{{ p.name }}</span>
          </router-link>
          <!-- 先列已有的项目再给「新建」：直接给一颗会默默再建一个的按钮，人会建出第二个、第三个同样的项目。 -->
          <BaseButton
            v-if="!projectsLoading && !projectsFailed"
            kind="ghost"
            size="sm"
            prepend-icon="mdi-plus"
            class="ts__new"
            @click="createProject"
          >
            {{ t('tasks.side.newProject') }}
          </BaseButton>
        </template>
      </div>
    </section>

    <section class="ts__block">
      <h2 class="ts__h t-eyebrow-read">{{ t('tasks.side.info') }}</h2>
      <dl class="ts__facts">
        <div>
          <dt>{{ t('tasks.side.form') }}</dt>
          <dd>{{ formText }}</dd>
        </div>
        <div>
          <dt>{{ t('tasks.side.deadline') }}</dt>
          <dd>{{ deadlineText }}</dd>
        </div>
        <div v-if="defaultDeadline">
          <dt>{{ t('tasks.side.period') }}</dt>
          <dd>{{ t('tasks.side.periodValue', { n: defaultDeadline }) }}</dd>
        </div>
        <div>
          <dt>{{ t('tasks.side.attempts') }}</dt>
          <dd>{{ task.resubmittable ? t('tasks.side.multiple') : t('tasks.side.once') }}</dd>
        </div>
        <div v-if="task.rank">
          <dt>{{ t('tasks.side.rank') }}</dt>
          <dd><v-rating :model-value="task.rank" readonly density="compact" size="x-small" /></dd>
        </div>
        <div>
          <dt>{{ t('tasks.side.claimed') }}</dt>
          <dd class="t-num">{{ claimedText }}</dd>
        </div>
      </dl>

      <TaskInheritance :inheritance="inheritance" :loading="inheritanceLoading" />
    </section>
  </aside>
</template>

<script setup lang="ts">
import type { MenuAction } from '@/components/common/menuAction'
import type { Project } from '@/cx_types'
import type { TaskParticipationIdentity } from '@/network/api/tasks/types'
import type { Task, TaskSubmissionReview } from '@/types'

import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import { useNewProjectDialog } from '@/composables/useNewProjectDialog'

import { useTaskInheritance } from '../composables/useTaskInheritance'

import TaskInheritance from './TaskInheritance.vue'

import { listProjectsForTask } from '@/api'
import BaseButton from '@/components/base/BaseButton.vue'
import AdaptiveMenu from '@/components/common/AdaptiveMenu.vue'

const props = defineProps<{
  task: Task
  /** 我那一份报名（批过的或等批的）；没领就是 null。 */
  identity: TaskParticipationIdentity | null
  /** 我最新那一版提交；还没交是 null。 */
  latest: { version: number; review?: TaskSubmissionReview } | null
}>()

const emit = defineEmits<{ leave: [] }>()

// 「会继承什么」常驻在这里 (#944)：建项目之后，同一份说明还看得到 —— 从这道题
// 新建项目就在下面这颗按钮上，两份说明放一起，人不必回头找。
const { inheritance, loading: inheritanceLoading } = useTaskInheritance(() => props.task.id)

const mineActions = computed<MenuAction[]>(() => [
  { key: 'leave', label: t('tasks.side.leave'), icon: 'mdi-exit-to-app', onSelect: () => emit('leave') },
])

const { t } = useI18n()

const DAY_MS = 86_400_000

/** 我这一份的状态：从报名审核和最新一版提交的评审结果算，不看 `completion_status`（那一列服务端自己都说不准）。 */
const status = computed(() => {
  if (props.identity?.approved === 'NONE') return { label: t('tasks.page.claim.pending'), tone: 'muted' }
  const latest = props.latest
  if (!latest) return { label: t('tasks.side.noSubmission'), tone: 'muted' }
  const n = latest.version
  // 还没判的那一版，接口回的是 `{ reviewed: false }` 而不是空：判没判只看 `reviewed`。
  if (!latest.review?.reviewed) return { label: t('tasks.side.versionPending', { n }), tone: 'warn' }
  return latest.review.detail.accepted
    ? { label: t('tasks.side.versionPassed', { n }), tone: 'ok' }
    : { label: t('tasks.side.versionFailed', { n }), tone: 'danger' }
})

/** 我自己的截止：出题人批准时给每人单设的那一个，不是题目的截止。 */
const remaining = computed(() => {
  if (props.identity?.approved !== 'APPROVED') return ''
  const at = props.task.userDeadline ?? props.identity.deadline
  if (at == null) return ''
  const days = Math.ceil((at - Date.now()) / DAY_MS)
  if (days < 0) return t('tasks.side.overdue', { n: -days })
  if (days === 0) return t('tasks.side.dueToday')
  return t('tasks.side.daysLeft', { n: days })
})

const formText = computed(() => {
  const task = props.task
  if (task.submitterType === 'USER') return t('tasks.side.individual')
  const min = task.minTeamSize ?? 1
  const max = task.maxTeamSize ?? 1
  return min === max ? t('tasks.side.formTeam', { n: min }) : t('tasks.side.formTeamRange', { min, max })
})

const deadlineText = computed(() => {
  const at = props.task.deadline
  if (at == null) return t('tasks.side.deadlineNone')
  const days = Math.ceil((at - Date.now()) / DAY_MS)
  if (days < 0) return t('tasks.side.closedAgo', { n: -days })
  if (days === 0) return t('tasks.side.dueToday')
  return t('tasks.side.dueIn', { n: days })
})

/** 领取之后给每人的提交期限（天）。发题表单存的是天数，老数据里存的是毫秒（14 天存成
 *  1209600000），两种都换成天；0 或没有就不画这一行。 */
const defaultDeadline = computed(() => {
  const raw = props.task.defaultDeadline ?? 0
  return raw >= DAY_MS ? Math.round(raw / DAY_MS) : raw
})

/** 领取人数是接口给的 `participants.total`；`participantLimit` 的 0 是「不限」。 */
const claimedText = computed(() => {
  const n = props.task.participants.total
  const limit = props.task.participantLimit
  const teams = props.task.submitterType === 'TEAM'
  if (limit > 0) return t(teams ? 'tasks.side.claimedTeamsOf' : 'tasks.side.claimedPeopleOf', { n, limit })
  return t(teams ? 'tasks.side.claimedTeams' : 'tasks.side.claimedPeople', { n })
})

// ── 从这道题开出来的项目 ─────────────────────────────────────────────────────────

const projects = ref<Project[]>([])
const projectsLoading = ref(false)
const projectsFailed = ref(false)
const { show: showNewProjectDialog } = useNewProjectDialog()

async function loadProjects() {
  projectsLoading.value = true
  projectsFailed.value = false
  try {
    projects.value = (await listProjectsForTask(props.task.id)).data
  } catch {
    projects.value = []
    projectsFailed.value = true
  } finally {
    projectsLoading.value = false
  }
}

/** 用团队领的，新项目就挂在那个团队下；个人领的让人在对话框里选。 */
function createProject() {
  const teamId = props.identity?.type === 'TEAM' ? props.identity.memberId : null
  showNewProjectDialog(teamId, { id: props.task.id, name: props.task.name })
}

watch(
  () => [props.task.id, props.identity?.approved] as const,
  ([, approved]) => {
    if (approved === 'APPROVED') loadProjects()
  },
  { immediate: true }
)
</script>

<style scoped>
.ts {
  display: flex;
  flex-direction: column;
  gap: 24px;
  min-width: 0;
}

.ts__h {
  margin: 0 0 8px;
}

.ts__mine {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 10px 12px;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
}

.ts__mine-head {
  display: flex;
  gap: 8px;
  align-items: center;
  min-height: 24px;
}

.ts__mine-head strong {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  color: var(--ink);
  font-size: 14px;
  font-weight: 600;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.ts__status {
  margin: 0;
  font-size: 13px;
  line-height: var(--lh-13);
}

.ts__status--muted {
  color: var(--muted);
}

.ts__status--warn {
  color: var(--warn-ink);
}

.ts__status--ok {
  color: var(--ok-ink);
}

.ts__status--danger {
  color: var(--danger-ink);
}

.ts__project {
  display: flex;
  gap: 6px;
  align-items: center;
  margin-top: 4px;
  color: var(--text);
  font-size: 13px;
  text-decoration: none;
}

.ts__project:hover {
  color: var(--ink);
}

.ts__project span {
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.ts__new {
  align-self: flex-start;
  margin: 4px 0 0 -8px;
  color: var(--muted);
}

.ts__note {
  margin: 4px 0 0;
  color: var(--muted);
  font-size: 13px;
}

.ts__facts {
  margin: 0;
}

.ts__facts > div {
  display: flex;
  gap: 12px;
  align-items: center;
  justify-content: space-between;
  min-height: 32px;
  border-bottom: 1px solid var(--line);
  font-size: 13px;
}

.ts__facts > div:last-child {
  border-bottom: 0;
}

.ts__facts dt {
  color: var(--muted);
}

.ts__facts dd {
  margin: 0;
  color: var(--ink);
  text-align: right;
}
</style>
