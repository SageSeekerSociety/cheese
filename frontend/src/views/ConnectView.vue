<script setup lang="ts">
// Approve a device enrollment for one team; the team controls its compute.
import type { DeviceApproval } from '../cx_types'

import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'

import { authToken, connectDevice, deviceProposedName, listMyTeams } from '../api'
import { t } from '../i18n'

import ConnectViewView from './ConnectViewView.vue'

const route = useRoute()
const code = computed(() => String(route.query.code ?? ''))
// Approving binds the machine to a real account, so a token is required. If the
// human landed here from the cli link without a live session, send them to log
// in; the router brings them straight back here afterwards.
const loggedIn = computed(() => !!authToken())

const loading = ref(false)
const error = ref<string | null>(null)
const approved = ref<DeviceApproval | null>(null)
// The compute-node name, prefilled with the name the cli proposed (this machine's
// hostname) so the human sees a real default and can just edit it — never "unnamed".
const deviceName = ref('')
const teams = ref<import('@/types/devices').MyTeam[]>([])
const teamId = ref<number | null>(null)

// Prefill from the cli-proposed name (the device's hostname). Best-effort: if the
// lookup fails the field stays blank and the server still falls back to a real name.
onMounted(async () => {
  if (!code.value) return
  try {
    teams.value = (await listMyTeams()).filter(
      (team) => team.personal || team.role === 'OWNER' || team.role === 'ADMIN'
    )
    teamId.value = teams.value.find((team) => team.personal)?.id ?? null
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : t('project.connect.failed')
  }
  try {
    const r = await deviceProposedName(code.value)
    if (r.device_name) deviceName.value = r.device_name
  } catch {
    // leave blank; approval still names the node from the server-side default
  }
})

async function approve(name: string) {
  if (!code.value) {
    error.value = t('project.connect.missingCode')
    return
  }
  loading.value = true
  error.value = null
  try {
    approved.value = await connectDevice(code.value, name.trim() || undefined, teamId.value ?? undefined)
  } catch (e) {
    error.value = e instanceof Error ? e.message : t('project.connect.failed')
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <ConnectViewView
    v-model:team-id="teamId"
    :teams="teams"
    :code="code"
    :logged-in="loggedIn"
    :loading="loading"
    :error="error"
    :approved="approved"
    :proposed-name="deviceName"
    @approve="approve"
  />
</template>
