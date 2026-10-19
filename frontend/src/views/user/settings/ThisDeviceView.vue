<script setup lang="ts">
import type { MyDevice, MyTeam } from '@/cx_types'
import type { NavTarget } from '@/lib/navTarget'
import type { RunningDeviceSession } from '@/types/deviceSessions'

import NavLink from '@/components/common/NavLink.vue'
import i18n, { t } from '@/i18n'
import { teammateName } from '@/lib/agentNames'

withDefaults(
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
      <h2 class="t-title enrollment-title">{{ t('account.thisDevice.enrollments') }}</h2>
      <p v-if="!enrollments.length" class="t-body c-muted enrollment-title">
        {{ t('account.thisDevice.noEnrollments') }}
      </p>
      <div v-for="device in enrollments" :key="device.device_id" class="srow enrollment">
        <div>
          <NavLink
            v-if="teams.find((team) => team.id === device.team_id)"
            :to="`/teams/${teams.find((team) => team.id === device.team_id)!.handle}/compute`"
            >{{ teams.find((team) => team.id === device.team_id)!.name }}</NavLink
          >
          <span class="t-caption c-muted">{{ device.name }}</span>
          <p v-for="login in device.logins ?? []" :key="login.id" class="t-caption c-muted">
            {{ login.name || login.service_host || 'Claude' }} · {{ login.model || login.subscription_type || '—' }}
          </p>
        </div>
        <span class="t-caption" :class="device.online ? 'enrollment-online' : 'c-muted'">{{
          device.online ? t('teams.compute.online') : t('teams.compute.offline')
        }}</span>
      </div>
    </section>
    <section class="settings-card">
      <h2 class="settings-card__title">{{ t('account.thisDevice.sessions') }}</h2>
      <p v-if="sessionsError" class="settings-card__desc" role="alert">{{ sessionsError }}</p>
      <p v-else-if="!sessions.length" class="settings-card__desc">{{ t('account.thisDevice.noSessions') }}</p>
      <div v-for="session in sessions" :key="session.session_id" class="srow this-device__work-row">
        <div class="this-device__col">
          <span>{{ teammateName(session.agent_name, session.agent_name_source) }}</span>
          <span class="this-device__muted">{{ session.team_name }} · {{ session.project_name }}</span>
          <NavLink class="this-device__link" :to="sessionLink(session)">{{ session.conversation_title }}</NavLink>
        </div>
        <div class="this-device__col">
          <span>{{ session.roles.map((role) => t(`account.thisDevice.role.${role}`)).join(' / ') }}</span>
          <time :datetime="session.since" class="this-device__muted">{{
            t('account.thisDevice.since', { time: new Date(session.since).toLocaleString(i18n.global.locale.value) })
          }}</time>
        </div>
      </div>
    </section>
    <section class="settings-card">
      <div class="srow this-device__work-row">
        <div class="this-device__col">
          <label for="prevent-sleep">{{ t('account.thisDevice.preventSleep') }}</label>
          <span class="this-device__muted">{{ t('account.thisDevice.preventSleepHint') }}</span>
          <span class="this-device__muted">{{ t('account.thisDevice.lidHint') }}</span>
        </div>
        <v-switch
          id="prevent-sleep"
          class="this-device__sleep-switch"
          color="secondary"
          density="compact"
          inset
          :model-value="preventSleep"
          :disabled="sleepSaving"
          :aria-label="t('account.thisDevice.preventSleep')"
          hide-details
          @update:model-value="emit('preventSleep', !!$event)"
        />
      </div>
    </section>
  </div>
</template>
<style scoped src="@/styles/settings-card.css"></style>
<style scoped>
.enrollment-title {
  padding: 20px 24px;
}
.enrollment > div {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
}
.enrollment-online {
  color: var(--ok-ink);
}
.this-device__sleep-switch :deep(.v-switch__thumb) {
  border: 1px solid var(--muted);
}

.this-device__work-row {
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: start;
}

.this-device__link {
  color: var(--text);
  text-decoration: underline;
}

.this-device__col {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
}

.this-device__muted {
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}
</style>
