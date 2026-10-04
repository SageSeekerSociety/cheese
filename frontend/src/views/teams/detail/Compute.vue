<script setup lang="ts">
// The team's work computers: its Cloud quota and usage, the Cloud machines its
// projects already have, and every self-hosted device its projects can use,
// whether registered for the team or attached to one of its projects. Cloud
// machines are opened by an agent in a room that needs one; nothing here opens one.
import type { TeamResourceQuotas } from '@/api'
import type { MyDevice, Project, ProjectMachine } from '@/cx_types'

import { computed, inject, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import {
  ApiError,
  authToken,
  BASE,
  changeProjectMachinePower,
  deleteProjectMachine,
  getTeamResourceQuotas,
  listMyDevices,
  listProjectMachines,
  listProjects,
  listTeamDevices,
  registerDeviceForTeam,
  unregisterDeviceFromTeam,
} from '@/api'
import BaseButton from '@/components/base/BaseButton.vue'
import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import UserRef from '@/components/common/UserRefLink.vue'
import { useRoomSocket } from '@/components/room/composables/useRoomSocket'
import { t } from '@/i18n'
import { teamDataInjectionKey } from '@/keys'
import { teammateName } from '@/lib/agentNames'
import { renderNoticeMessage } from '@/lib/noticeText'
import { topicTitle } from '@/lib/topicState'

type CloudMachine = ProjectMachine & { projectName: string }

const teamData = inject(teamDataInjectionKey, ref())
const teamId = computed(() => teamData.value?.id ?? 0)
const canManage = computed(() => ['OWNER', 'ADMIN'].includes(teamData.value?.role ?? ''))
// 自己名下（只有自己的那个团队）不说「团队」：说到归属的几句各有一份。
const scope = computed(() => (teamData.value?.personal ? 'own' : 'team'))

const devices = ref<MyDevice[]>([])
const myDevices = ref<MyDevice[]>([])
const projects = ref<Project[]>([])
const cloudMachines = ref<CloudMachine[]>([])
const quotas = ref<TeamResourceQuotas | null>(null)
const quotaFull = computed(() => Boolean(quotas.value && quotas.value.machines.used >= quotas.value.machines.limit))
const loading = ref(false)
const error = ref<string | null>(null)
const busy = ref<string | null>(null)
const cloudConfigured = ref(true)

const myDeviceIds = computed(() => new Set(myDevices.value.map((device) => device.device_id)))
const addable = computed(() => myDevices.value.filter((device) => !device.team_ids.includes(teamId.value)))
const cloudDeviceIds = computed(
  () => new Set(cloudMachines.value.map((machine) => machine.device_id).filter((id): id is string => Boolean(id)))
)
const selfHostedDevices = computed(() => devices.value.filter((device) => !cloudDeviceIds.value.has(device.device_id)))
const onlineCount = computed(() => selfHostedDevices.value.filter((device) => device.online).length)
const statusLabel = computed<Record<ProjectMachine['status'], string>>(() => ({
  provisioning: t('teams.compute.status.provisioning'),
  starting: t('teams.compute.status.starting'),
  running: t('teams.compute.status.running'),
  suspending: t('teams.compute.status.suspending'),
  suspended: t('teams.compute.status.suspended'),
  resuming: t('teams.compute.status.resuming'),
  stopping: t('teams.compute.status.stopping'),
  stopped: t('teams.compute.status.stopped'),
  deleting: t('teams.compute.status.deleting'),
  deleted: t('teams.compute.status.deleted'),
  error: t('teams.compute.status.error'),
  unknown: t('teams.compute.status.unknown'),
}))

function errorMessage(value: unknown, fallback: string): string {
  return value instanceof Error ? value.message : fallback
}

async function loadCloud() {
  let configured = true
  const batches = await Promise.all(
    projects.value.map(async (project) => {
      try {
        const result = await listProjectMachines(project.id)
        return result.data.map((machine) => ({ ...machine, projectName: project.name }))
      } catch (cause) {
        const message = errorMessage(cause, t('teams.compute.loadCloudFailed'))
        if (message.includes('not configured')) {
          configured = false
          return []
        }
        throw cause
      }
    })
  )
  cloudConfigured.value = configured
  cloudMachines.value = batches.flat()
  quotas.value = await getTeamResourceQuotas(teamId.value)
}

async function load() {
  loading.value = true
  error.value = null
  quotas.value = null
  try {
    const [teamDevices, mine, projectList] = await Promise.all([
      listTeamDevices(teamId.value),
      listMyDevices().catch(() => ({ devices: [] })),
      listProjects(teamId.value),
    ])
    devices.value = teamDevices.devices
    myDevices.value = mine.devices
    projects.value = projectList.data
    await loadCloud()
  } catch (cause) {
    error.value = errorMessage(cause, t('teams.compute.loadFailed'))
  } finally {
    loading.value = false
  }
}

// Read some projects' machines again: the ones a `machines` signal names, or all
// of them after a reconnect and on the resync. A refused credential stops the
// resync rather than repeating the refusal.
async function refreshCloud(only?: Set<string>) {
  if (!only) {
    try {
      await loadCloud()
    } catch (cause) {
      if (cause instanceof ApiError && [401, 403].includes(cause.status)) stopResync()
      error.value = errorMessage(cause, t('teams.compute.refreshFailed'))
    }
    return
  }
  try {
    const fresh = new Map(
      await Promise.all(
        projects.value
          .filter((project) => only.has(project.id))
          .map(
            async (project) =>
              [
                project.id,
                (await listProjectMachines(project.id)).data.map((machine) => ({
                  ...machine,
                  projectName: project.name,
                })),
              ] as const
          )
      )
    )
    cloudMachines.value = projects.value.flatMap(
      (project) => fresh.get(project.id) ?? cloudMachines.value.filter((m) => m.project_id === project.id)
    )
    quotas.value = await getTeamResourceQuotas(teamId.value)
  } catch (cause) {
    if (cause instanceof ApiError && [401, 403].includes(cause.status)) stopResync()
    error.value = errorMessage(cause, t('teams.compute.refreshFailed'))
  }
}

// The team's live feed (backend `routes/team_live.py`) says which projects'
// machines changed; nothing here polls. The token rides as ?token=, as on the room
// socket, because a browser cannot set a header on a WebSocket.
function teamLiveUrl(team: string): string {
  const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
  const token = authToken()
  const q = token ? `?token=${encodeURIComponent(token)}` : ''
  return `${proto}://${window.location.host}${BASE}/teams/${encodeURIComponent(team)}/live${q}`
}

const live = useRoomSocket({
  topicId: () => (teamId.value ? String(teamId.value) : undefined),
  url: teamLiveUrl,
  onFrame(frame) {
    if (frame.type === 'error' && live.isConnectRefusal(frame.code)) {
      live.connectRefused.value = true
      stopResync()
      error.value = renderNoticeMessage(frame.i18n, frame.message)
    } else if (frame.type === 'state' && frame.resource === 'machines') {
      void refreshCloud(new Set(frame.project_ids ?? []))
    }
  },
  onOpen: () => {},
  // Signals sent while the socket was down are gone; read everything again.
  reconnect(team) {
    void refreshCloud()
    live.open(team)
  },
  errorMsg: ref<string | null>(null),
})

// Insurance, as a controller's resync is: a change no signal reached still shows
// within this long. Only while the page is shown.
const RESYNC_MS = 5 * 60_000
let resyncTimer: ReturnType<typeof setInterval> | null = null

function startResync() {
  stopResync()
  resyncTimer = setInterval(() => {
    if (document.visibilityState === 'visible') void refreshCloud()
  }, RESYNC_MS)
}

function stopResync() {
  if (resyncTimer) clearInterval(resyncTimer)
  resyncTimer = null
}

function openTeam() {
  void load()
  live.connectRefused.value = false
  if (teamId.value) live.open(String(teamId.value))
  else live.close()
  startResync()
}

async function addMachine(device: MyDevice) {
  busy.value = device.device_id
  error.value = null
  try {
    await registerDeviceForTeam(device.device_id, teamId.value)
    await load()
  } catch (cause) {
    error.value = errorMessage(cause, t('teams.compute.addMachineFailed'))
  } finally {
    busy.value = null
  }
}

async function removeMachine(device: MyDevice) {
  if (!window.confirm(t(`teams.compute.${scope.value}.removeDeviceConfirm`, { name: device.name }))) return
  busy.value = device.device_id
  error.value = null
  try {
    await unregisterDeviceFromTeam(device.device_id, teamId.value)
    await load()
  } catch (cause) {
    error.value = errorMessage(cause, t('teams.compute.removeMachineFailed'))
  } finally {
    busy.value = null
  }
}

async function destroyCloud(machine: CloudMachine) {
  if (!window.confirm(t('teams.compute.destroyConfirm', { hostname: machine.hostname }))) return
  busy.value = machine.id
  error.value = null
  try {
    await deleteProjectMachine(machine.project_id, machine.id)
    await loadCloud()
  } catch (cause) {
    error.value = errorMessage(cause, t('teams.compute.destroyFailed'))
  } finally {
    busy.value = null
  }
}

async function changePower(machine: CloudMachine, operation: 'suspend' | 'resume') {
  if (operation === 'suspend' && !window.confirm(t('teams.compute.suspendConfirm', { hostname: machine.hostname })))
    return
  busy.value = machine.id
  error.value = null
  try {
    await changeProjectMachinePower(machine.project_id, machine.id, operation)
    await loadCloud()
  } catch (cause) {
    error.value = errorMessage(
      cause,
      operation === 'suspend' ? t('teams.compute.suspendFailed') : t('teams.compute.resumeFailed')
    )
  } finally {
    busy.value = null
  }
}

onMounted(openTeam)
watch(teamId, openTeam)
onBeforeUnmount(stopResync)
</script>

<template>
  <v-container class="px-6 py-5" fluid>
    <div class="mb-5 d-flex align-start flex-wrap ga-3">
      <div>
        <p class="text-body-2 text-medium-emphasis mb-0">
          {{ t(`teams.compute.${scope}.subtitle`) }}
        </p>
      </div>
    </div>

    <v-alert v-if="error" type="error" density="comfortable" class="mb-4" closable @click:close="error = null">
      {{ error }}
    </v-alert>

    <div v-if="loading" class="py-12 text-center">
      <v-progress-circular indeterminate color="primary" />
    </div>

    <template v-else>
      <section v-if="quotas" class="compute-section mb-7">
        <h3 class="text-subtitle-1 font-weight-medium mb-3">{{ t('teams.compute.quotaSection') }}</h3>
        <v-row>
          <v-col cols="12" md="6">
            <v-card variant="outlined" rounded="lg" class="pa-4 fill-height">
              <div class="text-body-2 mb-2">{{ t(`teams.compute.${scope}.machines`) }}</div>
              <div class="text-h6">
                {{ t('teams.compute.machineQuotaCount', { used: quotas.machines.used, limit: quotas.machines.limit }) }}
              </div>
              <v-progress-linear
                class="my-3"
                :model-value="Math.min(100, (quotas.machines.used / quotas.machines.limit) * 100)"
                :color="quotaFull ? 'warning' : 'primary'"
              />
              <div class="text-caption text-medium-emphasis">{{ t('teams.compute.machinesSharedHint') }}</div>
            </v-card>
          </v-col>
        </v-row>
        <v-table v-if="quotas.projects.length" density="comfortable">
          <thead>
            <tr>
              <th>{{ t('teams.compute.projectCol') }}</th>
              <th>{{ t('teams.compute.machinesUsedCol') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="project in quotas.projects" :key="project.id">
              <td>{{ project.name }}</td>
              <td>{{ t('teams.compute.machinesUsed', project.machines_used) }}</td>
            </tr>
          </tbody>
        </v-table>
      </section>

      <section class="compute-section mb-7">
        <div class="section-heading mb-3">
          <div>
            <h3 class="text-subtitle-1 font-weight-medium">{{ t('teams.compute.cloudSection') }}</h3>
            <p class="text-caption text-medium-emphasis mb-0">{{ t(`teams.compute.${scope}.cloudSubtitle`) }}</p>
          </div>
        </div>

        <v-alert v-if="!cloudConfigured" type="info" variant="tonal" density="comfortable">
          {{ t('teams.compute.cloudNotConfigured') }}
        </v-alert>
        <div v-else-if="!cloudMachines.length" class="empty-panel">
          <BaseEmptyState size="compact" icon="mdi-cloud-outline" :title="t('teams.compute.cloudEmpty')" />
        </div>
        <v-row v-else>
          <v-col v-for="machine in cloudMachines" :key="machine.id" cols="12" md="6" xl="4">
            <v-card variant="outlined" rounded="lg" class="pa-4 fill-height">
              <div class="d-flex align-center mb-3">
                <v-icon size="20" color="primary" class="mr-2">mdi-cloud</v-icon>
                <span class="text-subtitle-2 font-weight-medium text-truncate">{{ machine.hostname }}</span>
                <v-spacer />
                <v-progress-circular
                  v-if="
                    ['provisioning', 'starting', 'suspending', 'resuming', 'stopping', 'deleting', 'unknown'].includes(
                      machine.status
                    )
                  "
                  indeterminate
                  size="15"
                  width="2"
                  class="mr-2"
                />
                <v-chip
                  size="x-small"
                  :color="machine.status === 'running' ? 'success' : machine.status === 'error' ? 'error' : undefined"
                  variant="tonal"
                >
                  {{ statusLabel[machine.status] }}
                </v-chip>
              </div>
              <div class="machine-meta">
                <span>{{ t('teams.compute.cores', machine.cores) }}</span>
                <span>{{ t('teams.compute.memory', { size: Math.round(machine.memory_mb / 1024) }) }}</span>
                <span>{{ t('teams.compute.disk', { size: machine.disk_gb }) }}</span>
              </div>
              <div class="text-caption text-medium-emphasis mt-2">
                {{ t('teams.compute.billedTo', { project: machine.projectName }) }}
              </div>
              <div v-if="machine.ip" class="text-caption text-medium-emphasis mt-1">
                {{ t('teams.compute.address', { ip: machine.ip }) }}
              </div>
              <div class="text-caption mt-1" :class="machine.device_id ? 'text-success' : 'text-medium-emphasis'">
                {{
                  machine.device_id
                    ? t('teams.compute.enrolled')
                    : machine.enroll_error
                      ? t('teams.compute.enrollFailed', {
                          attempts: machine.enroll_attempts,
                          max: machine.enroll_max_attempts,
                        })
                      : t('teams.compute.enrollPending')
                }}
              </div>
              <v-alert v-if="machine.enroll_error" type="error" variant="tonal" density="compact" class="mt-3">
                {{ machine.enroll_error }}
              </v-alert>
              <div v-if="canManage" class="mt-3 d-flex justify-end">
                <BaseButton
                  v-if="machine.status === 'running' || machine.status === 'suspended'"
                  size="sm"
                  :disabled="busy !== null"
                  @click="changePower(machine, machine.status === 'running' ? 'suspend' : 'resume')"
                >
                  {{ machine.status === 'running' ? t('teams.compute.suspend') : t('teams.compute.resume') }}
                </BaseButton>
                <BaseButton
                  size="sm"
                  :loading="busy === machine.id"
                  :disabled="['suspending', 'resuming'].includes(machine.status)"
                  @click="destroyCloud(machine)"
                >
                  {{ t('teams.compute.release') }}
                </BaseButton>
              </div>
            </v-card>
          </v-col>
        </v-row>
      </section>

      <section class="compute-section">
        <div class="section-heading mb-3">
          <div>
            <h3 class="text-subtitle-1 font-weight-medium">{{ t('teams.compute.selfHostedSection') }}</h3>
            <p class="text-caption text-medium-emphasis mb-0">{{ t(`teams.compute.${scope}.selfHostedSubtitle`) }}</p>
          </div>
          <v-menu location="bottom end">
            <template #activator="{ props: menuProps }">
              <BaseButton v-bind="menuProps" kind="secondary" prepend-icon="mdi-plus">{{
                t('teams.compute.addDevice')
              }}</BaseButton>
            </template>
            <v-list density="compact" min-width="280">
              <v-list-item
                v-for="device in addable"
                :key="device.device_id"
                :title="device.name"
                :disabled="busy === device.device_id"
                @click="addMachine(device)"
              >
                <template #prepend>
                  <v-icon :color="device.online ? 'success' : 'grey'" size="12" class="mr-2">mdi-circle</v-icon>
                </template>
              </v-list-item>
              <v-list-item
                v-if="!addable.length && myDevices.length"
                disabled
                :title="t(`teams.compute.${scope}.allDevicesAdded`)"
              />
              <v-list-item
                v-if="!myDevices.length"
                :to="{ name: 'UserSettingsDevices' }"
                :title="t('teams.compute.goToMyDevices')"
              >
                <template #prepend><v-icon size="18" class="mr-2">mdi-laptop-account</v-icon></template>
              </v-list-item>
            </v-list>
          </v-menu>
        </div>

        <div v-if="!selfHostedDevices.length" class="empty-panel">
          <BaseEmptyState
            size="compact"
            icon="mdi-laptop-off"
            :title="t('teams.compute.selfHostedEmptyTitle')"
            :desc="t(`teams.compute.${scope}.selfHostedEmptyHint`)"
          />
        </div>
        <template v-else>
          <div class="text-caption text-medium-emphasis mb-3">
            {{ t('teams.compute.deviceSummary', { count: selfHostedDevices.length, online: onlineCount }) }}
          </div>
          <v-row>
            <v-col v-for="device in selfHostedDevices" :key="device.device_id" cols="12" sm="6" lg="4">
              <v-card variant="outlined" rounded="lg" class="pa-4 fill-height">
                <div class="d-flex align-center mb-2">
                  <v-icon :color="device.online ? 'success' : 'grey'" size="20" class="mr-2">mdi-laptop</v-icon>
                  <span class="text-subtitle-2 font-weight-medium text-truncate">{{ device.name }}</span>
                  <v-spacer />
                  <span
                    class="text-caption"
                    :class="device.online ? 'text-success font-weight-medium' : 'text-medium-emphasis'"
                  >
                    <span class="status-dot" :class="device.online ? 'status-dot--on' : ''" />
                    {{ device.online ? t('teams.compute.online') : t('teams.compute.offline') }}
                  </span>
                </div>
                <div class="text-caption text-medium-emphasis machine-id">{{ device.device_id }}</div>
                <div v-if="!device.team_ids.includes(teamId)" class="mt-2 device-user">
                  {{
                    t('work.deviceInUse.attached', {
                      projects: (device.attached_projects ?? [])
                        .map((project) => project.name)
                        .join(t('teams.compute.listSeparator')),
                    })
                  }}
                </div>
                <div v-if="device.screens.length" class="mt-3 d-flex flex-wrap ga-2">
                  <v-chip
                    v-for="screen in device.screens"
                    :key="screen.sid"
                    size="x-small"
                    variant="tonal"
                    color="primary"
                  >
                    <v-icon start size="12">mdi-monitor-eye</v-icon>
                    {{ t('teams.compute.screenRunning', { handle: screen.agent_handle }) }}
                  </v-chip>
                </div>
                <div
                  v-for="use in device.in_use ?? []"
                  :key="`${use.topic_id}:${use.agent_handle}`"
                  class="mt-2 device-user"
                >
                  <i18n-t keypath="work.deviceInUse.line" tag="span">
                    <template #project
                      ><span data-user-content>{{ use.project_name }}</span></template
                    >
                    <template #room
                      ><span :data-user-content="use.topic_title || undefined">{{
                        topicTitle({ title: use.topic_title, title_source: use.topic_title_source })
                      }}</span></template
                    >
                    <template #agent>
                      <UserRef
                        :handle="use.agent_handle"
                        :name="teammateName(use.agent_name, use.agent_name_source)"
                        :project-id="use.project_id"
                      />
                    </template>
                  </i18n-t>
                </div>
                <div
                  v-if="myDeviceIds.has(device.device_id) && device.team_ids.includes(teamId)"
                  class="mt-2 d-flex justify-end"
                >
                  <BaseButton size="sm" :loading="busy === device.device_id" @click="removeMachine(device)">
                    {{ t(`teams.compute.${scope}.remove`) }}
                  </BaseButton>
                </div>
              </v-card>
            </v-col>
          </v-row>
        </template>
      </section>
    </template>
  </v-container>
</template>

<style scoped>
.section-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}
.profile-grid {
  display: grid;
  /* See NodeBoard: without min(), 240px is a floor the grid keeps even in a
     narrower column, and the board overflows rather than reflowing. */
  grid-template-columns: repeat(auto-fit, minmax(min(240px, 100%), 1fr));
  gap: 10px;
}
.profile-card {
  display: flex;
  align-items: center;
  gap: 12px;
  min-height: 76px;
  padding: 12px 14px;
  border: 1px solid rgba(var(--v-border-color), 0.18);
  border-radius: var(--radius-lg);
  background: rgb(var(--v-theme-surface));
  text-align: left;
}
.profile-card:not(:disabled) {
  cursor: pointer;
}
.profile-card--active {
  border-color: rgb(var(--v-theme-primary));
  background: rgba(var(--v-theme-primary), 0.04);
}
.profile-card--off {
  opacity: 0.58;
}
.profile-copy {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-width: 0;
}
.profile-title {
  font-size: 0.875rem;
  font-weight: 600;
}
.profile-description {
  margin-top: 2px;
  color: rgba(var(--v-theme-on-surface), 0.62);
  font-size: 0.75rem;
  line-height: 1.4;
}
.empty-panel {
  border: 1px dashed rgba(var(--v-border-color), 0.24);
  border-radius: var(--radius-lg);
}
.machine-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.machine-meta span {
  padding: 2px 7px;
  border-radius: var(--radius-sm);
  background: rgba(var(--v-theme-on-surface), 0.05);
  font-size: 0.72rem;
}
.device-user {
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}
.machine-id {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}
.status-dot {
  display: inline-block;
  width: 6px;
  height: 6px;
  margin-right: 4px;
  border-radius: 50%;
  background: rgb(var(--v-theme-on-surface));
  opacity: 0.35;
}
.status-dot--on {
  background: rgb(var(--v-theme-success));
  opacity: 1;
}
@media (max-width: 600px) {
  .section-heading {
    align-items: flex-start;
    flex-direction: column;
  }
}
</style>
