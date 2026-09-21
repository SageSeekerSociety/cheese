<script setup lang="ts">
// Team compute is the ownership surface from execution-architecture v4:
// platform cloud machines and self-hosted nodes live in one team pool; projects
// only provide billing/audit attribution, while a topic chooses the actual target.
import type { TeamResourceQuotas } from '@/api'
import type { MyDevice, Project, ProjectMachine } from '@/cx_types'

import { computed, inject, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'

import {
  createProjectMachine,
  deleteProjectMachine,
  getTeamResourceQuotas,
  listMyDevices,
  listProjectMachines,
  listProjects,
  listTeamDevices,
  registerDeviceForTeam,
  unregisterDeviceFromTeam,
} from '@/api'
import { teamDataInjectionKey } from '@/keys'

type CloudMachine = ProjectMachine & { projectName: string }

const route = useRoute()
const { t } = useI18n()
const teamData = inject(teamDataInjectionKey, ref())
const teamId = computed(() => Number(route.params.teamId))
const canManage = computed(() => ['OWNER', 'ADMIN'].includes(teamData.value?.role ?? ''))

const devices = ref<MyDevice[]>([])
const myDevices = ref<MyDevice[]>([])
const projects = ref<Project[]>([])
const cloudMachines = ref<CloudMachine[]>([])
const quotas = ref<TeamResourceQuotas | null>(null)
const selectedQuota = computed(() => quotas.value?.machines)
const selectedProjectUsage = computed(
  () => quotas.value?.projects.find((project) => project.id === selectedProject.value)?.machines_used ?? 0
)
const quotaFull = computed(() => Boolean(selectedQuota.value && selectedQuota.value.used >= selectedQuota.value.limit))
const loading = ref(false)
const error = ref<string | null>(null)
const busy = ref<string | null>(null)
const cloudConfigured = ref(true)

const createDialog = ref(false)
const creating = ref(false)
const selectedProject = ref<string | null>(null)
const cores = ref(4)
const memoryMb = ref(8192)
const diskGb = ref(64)

let pollTimer: ReturnType<typeof setTimeout> | null = null

const myDeviceIds = computed(() => new Set(myDevices.value.map((device) => device.device_id)))
const addable = computed(() => myDevices.value.filter((device) => !device.team_ids.includes(teamId.value)))
const cloudDeviceIds = computed(
  () => new Set(cloudMachines.value.map((machine) => machine.device_id).filter((id): id is string => Boolean(id)))
)
const selfHostedDevices = computed(() => devices.value.filter((device) => !cloudDeviceIds.value.has(device.device_id)))
const onlineCount = computed(() => selfHostedDevices.value.filter((device) => device.online).length)
const projectOptions = computed(() => projects.value.map((project) => ({ title: project.name, value: project.id })))
const cloudMoving = computed(() =>
  cloudMachines.value.some(
    (machine) =>
      ['provisioning', 'starting', 'stopping', 'deleting', 'unknown'].includes(machine.status) ||
      ['provisioning', 'unknown'].includes(machine.ai_status) ||
      (machine.status === 'running' &&
        machine.ai_status === 'ready' &&
        !machine.device_id &&
        machine.enroll_attempts < machine.enroll_max_attempts)
  )
)

// 状态文案要跟着界面语言走，所以是 computed，不是模块级常量表。
const statusLabels = computed<Record<ProjectMachine['status'], string>>(() => ({
  provisioning: t('teams.compute.statusProvisioning'),
  starting: t('teams.compute.statusStarting'),
  running: t('teams.compute.statusRunning'),
  stopping: t('teams.compute.statusStopping'),
  stopped: t('teams.compute.statusStopped'),
  deleting: t('teams.compute.statusDeleting'),
  deleted: t('teams.compute.statusDeleted'),
  error: t('teams.compute.statusError'),
  unknown: t('teams.compute.statusUnknown'),
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
  selectedProject.value = null
  try {
    const [teamDevices, mine, projectList] = await Promise.all([
      listTeamDevices(teamId.value),
      listMyDevices().catch(() => ({ devices: [] })),
      listProjects(teamId.value),
    ])
    devices.value = teamDevices.devices
    myDevices.value = mine.devices
    projects.value = projectList.data
    selectedProject.value = selectedProject.value ?? projects.value[0]?.id ?? null
    await loadCloud()
  } catch (cause) {
    error.value = errorMessage(cause, t('teams.compute.loadTeamFailed'))
  } finally {
    loading.value = false
    schedulePoll()
  }
}

async function refreshCloud() {
  try {
    await loadCloud()
  } catch (cause) {
    error.value = errorMessage(cause, t('teams.compute.refreshFailed'))
  } finally {
    schedulePoll()
  }
}

function schedulePoll() {
  if (pollTimer) clearTimeout(pollTimer)
  pollTimer = null
  if (cloudMoving.value) pollTimer = setTimeout(refreshCloud, 5000)
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
  if (!window.confirm(t('teams.compute.removeDeviceConfirm', { name: device.name }))) return
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

async function provisionCloud() {
  if (!selectedProject.value || creating.value || !selectedQuota.value || quotaFull.value) return
  creating.value = true
  error.value = null
  try {
    await createProjectMachine(selectedProject.value, {
      cores: Number(cores.value),
      memoryMb: Number(memoryMb.value),
      diskGb: Number(diskGb.value),
    })
    createDialog.value = false
    await loadCloud()
  } catch (cause) {
    error.value = errorMessage(cause, t('teams.compute.provisionFailed'))
  } finally {
    creating.value = false
    schedulePoll()
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
    schedulePoll()
  }
}

onMounted(load)
watch(teamId, load)
watch(cloudMoving, schedulePoll)
onBeforeUnmount(() => {
  if (pollTimer) clearTimeout(pollTimer)
})
</script>

<template>
  <v-container class="px-6 py-5" fluid>
    <div class="mb-5 d-flex align-start flex-wrap ga-3">
      <div>
        <h2 class="text-h6 font-weight-medium mb-1">{{ t('teams.compute.title') }}</h2>
        <p class="text-body-2 text-medium-emphasis mb-0">
          {{ t('teams.compute.subtitle') }}
        </p>
      </div>
      <v-spacer />
      <v-btn
        v-if="canManage"
        color="primary"
        variant="flat"
        prepend-icon="mdi-cloud-plus-outline"
        :disabled="!cloudConfigured || !projects.length"
        @click="createDialog = true"
      >
        {{ t('teams.compute.provision') }}
      </v-btn>
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
              <div class="text-body-2 mb-2">{{ t('teams.compute.teamMachines') }}</div>
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
          <v-col cols="12" md="6">
            <v-card variant="outlined" rounded="lg" class="pa-4 fill-height">
              <div class="text-body-2 mb-2">{{ t('teams.compute.creditsTitle') }}</div>
              <div v-if="quotas.credits.unlimited" class="text-h6">{{ t('teams.compute.creditsUnlimited') }}</div>
              <template v-else>
                <div class="text-h6">
                  {{
                    t('teams.compute.creditsRemaining', { count: quotas.credits.credits_remaining.toLocaleString() })
                  }}
                </div>
                <div class="text-body-2 my-2">
                  {{
                    t('teams.compute.creditsUsed', {
                      used: quotas.credits.credits_used.toLocaleString(),
                      total: quotas.credits.credits_total.toLocaleString(),
                    })
                  }}
                </div>
              </template>
              <div class="text-caption text-medium-emphasis mt-2">
                {{
                  t('teams.compute.creditsSharedHint', {
                    per: quotas.credits.tokens_per_credit.toLocaleString(),
                  })
                }}
              </div>
            </v-card>
          </v-col>
        </v-row>
        <p class="text-caption text-medium-emphasis mt-3 mb-2">{{ t('teams.compute.creditsFootnote') }}</p>
        <v-table v-if="quotas.projects.length" density="comfortable">
          <thead>
            <tr>
              <th>{{ t('teams.compute.projectCol') }}</th>
              <th>{{ t('teams.compute.machinesUsedCol') }}</th>
              <th>{{ t('teams.compute.tokensUsedCol') }}</th>
              <th>{{ t('teams.compute.restrictedCreditsCol') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="project in quotas.projects" :key="project.id">
              <td>{{ project.name }}</td>
              <td>{{ t('teams.compute.machinesUsed', { count: project.machines_used }) }}</td>
              <td>{{ project.total_tokens.toLocaleString() }}</td>
              <td>{{ project.restricted_credits_remaining.toLocaleString() }}</td>
            </tr>
          </tbody>
        </v-table>
      </section>

      <section class="compute-section mb-7">
        <div class="section-heading mb-3">
          <div>
            <h3 class="text-subtitle-1 font-weight-medium">{{ t('teams.compute.cloudSection') }}</h3>
            <p class="text-caption text-medium-emphasis mb-0">{{ t('teams.compute.cloudSubtitle') }}</p>
          </div>
        </div>

        <v-alert v-if="!cloudConfigured" type="info" variant="tonal" density="comfortable">
          {{ t('teams.compute.cloudNotConfigured') }}
        </v-alert>
        <v-alert v-else-if="!projects.length" type="info" variant="tonal" density="comfortable">
          {{ t('teams.compute.cloudNoProjects') }}
        </v-alert>
        <div v-else-if="!cloudMachines.length" class="empty-panel">
          <v-icon size="38" class="empty-panel-icon">mdi-cloud-outline</v-icon>
          <div>
            <div class="text-body-2 font-weight-medium">{{ t('teams.compute.cloudEmptyTitle') }}</div>
            <div class="text-caption text-medium-emphasis">
              {{ t('teams.compute.cloudEmptyHint') }}
            </div>
          </div>
        </div>
        <v-row v-else>
          <v-col v-for="machine in cloudMachines" :key="machine.id" cols="12" md="6" xl="4">
            <v-card variant="outlined" rounded="lg" class="pa-4 fill-height">
              <div class="d-flex align-center mb-3">
                <v-icon size="20" color="primary" class="mr-2">mdi-cloud</v-icon>
                <span class="text-subtitle-2 font-weight-medium text-truncate">{{ machine.hostname }}</span>
                <v-spacer />
                <v-progress-circular
                  v-if="['provisioning', 'starting', 'stopping', 'deleting', 'unknown'].includes(machine.status)"
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
                  {{ statusLabels[machine.status] }}
                </v-chip>
              </div>
              <div class="machine-meta">
                <span>{{ t('teams.compute.cores', { count: machine.cores }) }}</span>
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
                <v-btn
                  size="small"
                  variant="text"
                  color="error"
                  :loading="busy === machine.id"
                  @click="destroyCloud(machine)"
                >
                  {{ t('teams.compute.release') }}
                </v-btn>
              </div>
            </v-card>
          </v-col>
        </v-row>
      </section>

      <section class="compute-section">
        <div class="section-heading mb-3">
          <div>
            <h3 class="text-subtitle-1 font-weight-medium">{{ t('teams.compute.selfHostedSection') }}</h3>
            <p class="text-caption text-medium-emphasis mb-0">{{ t('teams.compute.selfHostedSubtitle') }}</p>
          </div>
          <v-menu location="bottom end">
            <template #activator="{ props: menuProps }">
              <v-btn v-bind="menuProps" variant="outlined" prepend-icon="mdi-plus">{{
                t('teams.compute.addDevice')
              }}</v-btn>
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
                :title="t('teams.compute.allDevicesAdded')"
              />
              <v-list-item
                v-if="!myDevices.length"
                :to="{ name: 'my-devices' }"
                :title="t('teams.compute.goToMyDevices')"
              >
                <template #prepend><v-icon size="18" class="mr-2">mdi-laptop-account</v-icon></template>
              </v-list-item>
            </v-list>
          </v-menu>
        </div>

        <div v-if="!selfHostedDevices.length" class="empty-panel">
          <v-icon size="38" class="empty-panel-icon">mdi-laptop-off</v-icon>
          <div>
            <div class="text-body-2 font-weight-medium">{{ t('teams.compute.selfHostedEmptyTitle') }}</div>
            <div class="text-caption text-medium-emphasis">{{ t('teams.compute.selfHostedEmptyHint') }}</div>
          </div>
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
                <div v-if="myDeviceIds.has(device.device_id)" class="mt-2 d-flex justify-end">
                  <v-btn
                    size="small"
                    variant="text"
                    color="error"
                    :loading="busy === device.device_id"
                    @click="removeMachine(device)"
                  >
                    {{ t('teams.compute.removeFromTeam') }}
                  </v-btn>
                </div>
              </v-card>
            </v-col>
          </v-row>
        </template>
      </section>
    </template>

    <v-dialog v-model="createDialog" max-width="520">
      <v-card rounded="lg">
        <v-card-title class="pt-5 px-5">{{ t('teams.compute.provisionTitle') }}</v-card-title>
        <v-card-text class="px-5">
          <v-alert type="info" variant="tonal" density="compact" class="mb-4">
            {{ t('teams.compute.provisionWarning') }}
          </v-alert>
          <v-select
            v-model="selectedProject"
            autocomplete="off"
            :items="projectOptions"
            :label="t('teams.compute.billedProject')"
            variant="outlined"
            density="comfortable"
          />
          <v-alert
            v-if="selectedQuota"
            :type="quotaFull ? 'warning' : 'info'"
            variant="tonal"
            density="compact"
            class="mb-3"
          >
            {{ t('teams.compute.quotaUsed', { used: selectedQuota.used, limit: selectedQuota.limit }) }}
            <div>{{ t('teams.compute.projectUsage', { count: selectedProjectUsage }) }}</div>
            <div v-if="!quotaFull">
              {{
                t('teams.compute.afterCreate', {
                  used: selectedQuota.used + 1,
                  limit: selectedQuota.limit,
                })
              }}
            </div>
            <div>{{ t('teams.compute.stopKeepsSlot') }}</div>
            <div v-if="quotaFull">{{ t('teams.compute.quotaFull') }}</div>
          </v-alert>
          <div v-else class="text-body-2 text-medium-emphasis mb-3">{{ t('teams.compute.quotaUnavailable') }}</div>
          <v-row dense>
            <v-col cols="4"
              ><v-text-field
                v-model.number="cores"
                type="number"
                min="1"
                :label="t('teams.compute.coresLabel')"
                variant="outlined"
            /></v-col>
            <v-col cols="4"
              ><v-text-field
                v-model.number="memoryMb"
                type="number"
                min="512"
                step="512"
                :label="t('teams.compute.memoryLabel')"
                variant="outlined"
            /></v-col>
            <v-col cols="4"
              ><v-text-field
                v-model.number="diskGb"
                type="number"
                min="10"
                :label="t('teams.compute.diskLabel')"
                variant="outlined"
            /></v-col>
          </v-row>
        </v-card-text>
        <v-card-actions class="px-5 pb-5">
          <v-spacer />
          <v-btn variant="text" :disabled="creating" @click="createDialog = false">{{
            t('teams.compute.cancel')
          }}</v-btn>
          <v-btn
            color="primary"
            variant="flat"
            :loading="creating"
            :disabled="!selectedProject || !selectedQuota || quotaFull"
            @click="provisionCloud"
            >{{ t('teams.compute.confirmProvision') }}</v-btn
          >
        </v-card-actions>
      </v-card>
    </v-dialog>
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
  display: flex;
  align-items: center;
  gap: 14px;
  min-height: 94px;
  padding: 18px;
  border: 1px dashed rgba(var(--v-border-color), 0.24);
  border-radius: var(--radius-lg);
}
/* 空面板里陪着文字的图标属于元信息一档（§1.3），不是插图 */
.empty-panel-icon {
  color: var(--faint);
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
