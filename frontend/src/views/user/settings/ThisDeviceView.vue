<script setup lang="ts">
import type { MyDevice, MyTeam } from '@/cx_types'
import type { NavTarget } from '@/lib/navTarget'
import type { RunningDeviceSession } from '@/types/deviceSessions'

import NavLink from '@/components/common/NavLink.vue'
import i18n, { t } from '@/i18n'
import { teammateName } from '@/lib/agentNames'

const props = withDefaults(
  defineProps<{
    loading: boolean
    enrollments: MyDevice[]
    teams: MyTeam[]
    error: string | null
    sessions?: RunningDeviceSession[]
    preventSleep?: boolean
    sleepSaving?: boolean
    sessionsError?: string
  }>(),
  { sessions: () => [], preventSleep: true, sleepSaving: false, sessionsError: '' }
)

const emit = defineEmits<{ preventSleep: [enabled: boolean] }>()

function teamOf(device: MyDevice): MyTeam | undefined {
  return props.teams.find((team) => team.id === device.team_id)
}

function sessionLink(session: RunningDeviceSession): NavTarget {
  const projectId = session.project_id
  if (session.conversation_kind === 'task')
    return { name: 'workspace-task', params: { projectId, taskId: session.conversation_id } }
  if (session.conversation_kind === 'thread')
    return {
      name: 'workspace-thread',
      params: { projectId, topicId: session.room_id, threadId: session.conversation_id },
    }
  return { name: 'workspace-topic', params: { projectId, topicId: session.room_id } }
}
</script>
<template>
  <div class="settings-page">
    <header>
      <h1 class="t-page-title">{{ t('account.thisDevice.title') }}</h1>
    </header>
    <v-alert v-if="error" type="error" class="mt-4">{{ error }}</v-alert>
    <div v-if="loading" class="py-6 text-center"><v-progress-circular indeterminate size="24" /></div>
    <section v-else class="settings-card">
      <h2 class="settings-card__title">{{ t('account.thisDevice.enrollments') }}</h2>
      <p v-if="!enrollments.length" class="settings-card__desc this-device__last-desc">
        {{ t('account.thisDevice.noEnrollments') }}
      </p>
      <div v-for="device in enrollments" :key="device.device_id" class="srow this-device__row">
        <div class="this-device__col">
          <NavLink
            v-if="teamOf(device)"
            class="this-device__primary"
            :to="`/teams/${teamOf(device)!.handle}/compute`"
            >{{ teamOf(device)!.name }}</NavLink
          >
          <span class="this-device__muted">{{ device.name }}</span>
        </div>
        <span class="this-device__status">
          <span class="status-dot" :class="device.online ? 'status-dot--ok' : 'status-dot--muted'" aria-hidden="true" />
          {{ device.online ? t('teams.compute.online') : t('teams.compute.offline') }}
        </span>
      </div>
    </section>
    <section class="settings-card">
      <h2 class="settings-card__title">{{ t('account.thisDevice.sessions') }}</h2>
      <p v-if="sessionsError" class="settings-card__desc this-device__last-desc" role="alert">{{ sessionsError }}</p>
      <p v-else-if="!sessions.length" class="settings-card__desc this-device__last-desc">
        {{ t('account.thisDevice.noSessions') }}
      </p>
      <div v-for="session in sessions" :key="session.session_id" class="srow this-device__work-row">
        <div class="this-device__col">
          <span class="this-device__primary">{{ teammateName(session.agent_name, session.agent_name_source) }}</span>
          <span class="this-device__muted">{{ session.team_name }} · {{ session.project_name }}</span>
          <NavLink class="this-device__link" :to="sessionLink(session)">{{ session.conversation_title }}</NavLink>
        </div>
        <div class="this-device__col">
          <span class="this-device__roles">{{
            session.roles.map((role) => t(`account.thisDevice.role.${role}`)).join(' / ')
          }}</span>
          <time :datetime="session.since" class="this-device__muted">{{
            t('account.thisDevice.since', { time: new Date(session.since).toLocaleString(i18n.global.locale.value) })
          }}</time>
        </div>
      </div>
    </section>
    <section class="settings-card">
      <div class="settings-card__head">
        <div>
          <h2 class="settings-card__title">
            <label for="prevent-sleep">{{ t('account.thisDevice.preventSleep') }}</label>
          </h2>
          <p class="settings-card__desc this-device__last-desc">
            {{ t('account.thisDevice.preventSleepHint') }}<br />{{ t('account.thisDevice.lidHint') }}
          </p>
        </div>
        <v-switch
          id="prevent-sleep"
          class="this-device__sleep-switch"
          color="primary"
          density="compact"
          inset
          :model-value="preventSleep"
          :disabled="sleepSaving"
          :loading="sleepSaving"
          hide-details
          @update:model-value="emit('preventSleep', !!$event)"
        />
      </div>
    </section>
  </div>
</template>
<style scoped src="@/styles/settings-card.css"></style>
<style scoped>
.this-device__row,
.this-device__work-row {
  grid-template-columns: minmax(0, 1fr) auto;
}

.this-device__work-row {
  align-items: start;
}

.this-device__col {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.this-device__primary {
  font-size: 14px;
  font-weight: 500;
  line-height: var(--lh-14);
  color: var(--ink);
  text-decoration: none;
}

.this-device__roles {
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--text);
}

a.this-device__primary:hover {
  text-decoration: underline;
  text-underline-offset: 2px;
}

.this-device__status {
  display: inline-flex;
  gap: 6px;
  align-items: center;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
  white-space: nowrap;
}

.this-device__link {
  color: var(--text);
  text-decoration: underline;
}

.this-device__muted {
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}

/* A card that is only its head ends on its description: the card's own bottom
   padding, not the 12 that would lead into a row. */
.this-device__last-desc {
  padding-bottom: 20px;
}

/* On the title's line, as `.settings-card__head > .v-btn` puts a button. */
.this-device__sleep-switch {
  flex: 0 0 auto;
  margin-top: 10px;
}

@media (width < 768px) {
  .this-device__last-desc {
    padding-bottom: 16px;
  }

  .this-device__sleep-switch {
    margin-top: 6px;
  }
}
</style>
