<script setup lang="ts">
import type { MyDevice, MyTeam } from '@/cx_types'

import NavLink from '@/components/common/NavLink.vue'
import { t } from '@/i18n'
defineProps<{ loading: boolean; enrollments: MyDevice[]; teams: MyTeam[]; error: string | null }>()
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
</style>
