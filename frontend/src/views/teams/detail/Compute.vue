<script setup lang="ts">
// Team compute is the ownership surface from execution-architecture v4:
// platform cloud machines and self-hosted nodes live in one team pool; projects
// only provide billing/audit attribution, while a topic chooses the actual target.
import type { TeamResourceQuotas } from '@/api'
import type { ComputeProfiles, MyDevice, Project, ProjectMachine } from '@/cx_types'

import { computed, inject, onBeforeUnmount, onMounted, ref, watch } from 'vue'
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
import { TeamsApi } from '@/network/api/teams'

type CloudMachine = ProjectMachine & { projectName: string }

const route = useRoute()
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
const teamCompute = ref<ComputeProfiles | null>(null)
const loading = ref(false)
const error = ref<string | null>(null)
const busy = ref<string | null>(null)
const savingDefault = ref<string | null>(null)
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

const statusLabel: Record<ProjectMachine['status'], string> = {
  provisioning: '正在创建',
  starting: '正在启动',
  running: '运行中',
  stopping: '正在停止',
  stopped: '已停止',
  deleting: '正在释放',
  deleted: '已释放',
  error: '创建失败',
  unknown: '状态未知',
}

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
        const message = errorMessage(cause, '加载云算力失败')
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
    const [teamDevices, mine, profile, projectList] = await Promise.all([
      listTeamDevices(teamId.value),
      listMyDevices().catch(() => ({ devices: [] })),
      TeamsApi.getComputeProfile(teamId.value),
      listProjects(teamId.value),
    ])
    devices.value = teamDevices.devices
    myDevices.value = mine.devices
    teamCompute.value = profile.data
    projects.value = projectList.data
    selectedProject.value = selectedProject.value ?? projects.value[0]?.id ?? null
    await loadCloud()
  } catch (cause) {
    error.value = errorMessage(cause, '加载团队算力失败')
  } finally {
    loading.value = false
    schedulePoll()
  }
}

async function refreshCloud() {
  try {
    await loadCloud()
    const profile = await TeamsApi.getComputeProfile(teamId.value)
    teamCompute.value = profile.data
  } catch (cause) {
    error.value = errorMessage(cause, '刷新云算力状态失败')
  } finally {
    schedulePoll()
  }
}

function schedulePoll() {
  if (pollTimer) clearTimeout(pollTimer)
  pollTimer = null
  if (cloudMoving.value) pollTimer = setTimeout(refreshCloud, 5000)
}

async function pickDefault(profileId: string) {
  if (!canManage.value || savingDefault.value || teamCompute.value?.current === profileId) return
  savingDefault.value = profileId
  error.value = null
  try {
    const response = await TeamsApi.setComputeProfile(teamId.value, profileId)
    if (teamCompute.value) teamCompute.value = { ...teamCompute.value, current: response.data.current }
  } catch (cause) {
    error.value = errorMessage(cause, '修改团队默认算力失败')
  } finally {
    savingDefault.value = null
  }
}

async function addMachine(device: MyDevice) {
  busy.value = device.device_id
  error.value = null
  try {
    await registerDeviceForTeam(device.device_id, teamId.value)
    await load()
  } catch (cause) {
    error.value = errorMessage(cause, '添加机器失败')
  } finally {
    busy.value = null
  }
}

async function removeMachine(device: MyDevice) {
  if (!window.confirm(`确定把「${device.name}」移出这个团队的算力池吗？`)) return
  busy.value = device.device_id
  error.value = null
  try {
    await unregisterDeviceFromTeam(device.device_id, teamId.value)
    await load()
  } catch (cause) {
    error.value = errorMessage(cause, '移出机器失败')
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
    error.value = errorMessage(cause, '开通云算力失败')
  } finally {
    creating.value = false
    schedulePoll()
  }
}

async function destroyCloud(machine: CloudMachine) {
  if (!window.confirm(`确定释放云机器「${machine.hostname}」吗？释放后数据不可恢复。`)) return
  busy.value = machine.id
  error.value = null
  try {
    await deleteProjectMachine(machine.project_id, machine.id)
    await loadCloud()
  } catch (cause) {
    error.value = errorMessage(cause, '释放云算力失败')
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
        <h2 class="text-h6 font-weight-medium mb-1">算力</h2>
        <p class="text-body-2 text-medium-emphasis mb-0">
          团队统一管理云机器和自有设备。新话题沿用上次选择，第一条消息发出后锁定到该算力。
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
        开通云算力
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
        <h3 class="text-subtitle-1 font-weight-medium mb-3">配额与用量</h3>
        <v-row>
          <v-col cols="12" md="6">
            <v-card variant="outlined" rounded="lg" class="pa-4 fill-height">
              <div class="text-body-2 mb-2">团队云虚拟机</div>
              <div class="text-h6">{{ quotas.machines.used }} / {{ quotas.machines.limit }} 台</div>
              <v-progress-linear
                class="my-3"
                :model-value="Math.min(100, (quotas.machines.used / quotas.machines.limit) * 100)"
                :color="quotaFull ? 'warning' : 'primary'"
              />
              <div class="text-caption text-medium-emphasis">所有项目共享；停止机器仍占用名额，释放后归还</div>
            </v-card>
          </v-col>
          <v-col cols="12" md="6">
            <v-card variant="outlined" rounded="lg" class="pa-4 fill-height">
              <div class="text-body-2 mb-2">团队 tokens 额度</div>
              <div v-if="quotas.credits.unlimited" class="text-h6">未设置上限</div>
              <template v-else>
                <div class="text-h6">剩余 {{ quotas.credits.credits_remaining.toLocaleString() }} 额度</div>
                <div class="text-body-2 my-2">
                  已使用 {{ quotas.credits.credits_used.toLocaleString() }} /
                  {{ quotas.credits.credits_total.toLocaleString() }} 额度
                </div>
              </template>
              <div class="text-caption text-medium-emphasis mt-2">
                所有项目共享；1 额度 = {{ quotas.credits.tokens_per_credit.toLocaleString() }} tokens，使用后扣减
              </div>
            </v-card>
          </v-col>
        </v-row>
        <p class="text-caption text-medium-emphasis mt-3 mb-2">额度由平台或发放方调整；机构定向额度仅供指定项目使用</p>
        <v-table v-if="quotas.projects.length" density="comfortable">
          <thead>
            <tr>
              <th>项目</th>
              <th>占用云机器</th>
              <th>累计 tokens</th>
              <th>定向额度剩余</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="project in quotas.projects" :key="project.id">
              <td>{{ project.name }}</td>
              <td>{{ project.machines_used }} 台</td>
              <td>{{ project.total_tokens.toLocaleString() }}</td>
              <td>{{ project.restricted_credits_remaining.toLocaleString() }}</td>
            </tr>
          </tbody>
        </v-table>
      </section>
      <section class="compute-section mb-7">
        <div class="section-heading mb-3">
          <div>
            <h3 class="text-subtitle-1 font-weight-medium">团队默认</h3>
            <p class="text-caption text-medium-emphasis mb-0">项目第一次开话题时从这里开始；之后自动记住上次选择。</p>
          </div>
          <v-chip v-if="!canManage" size="small" variant="tonal">仅管理员可修改</v-chip>
        </div>
        <div class="profile-grid">
          <button
            v-for="profile in teamCompute?.profiles ?? []"
            :key="profile.id"
            type="button"
            class="profile-card"
            :class="{
              'profile-card--active': teamCompute?.current === profile.id,
              'profile-card--off': !profile.available,
            }"
            :disabled="!canManage || !profile.available || savingDefault !== null"
            @click="pickDefault(profile.id)"
          >
            <v-icon size="20">{{
              profile.id === 'device' ? 'mdi-laptop' : profile.id === 'gpu' ? 'mdi-expansion-card' : 'mdi-server'
            }}</v-icon>
            <span class="profile-copy">
              <span class="profile-title">{{ profile.label }}</span>
              <span class="profile-description">{{ profile.description }}</span>
            </span>
            <v-progress-circular v-if="savingDefault === profile.id" indeterminate size="16" width="2" />
            <v-icon v-else-if="teamCompute?.current === profile.id" size="18" color="primary">mdi-check-circle</v-icon>
            <span v-else-if="!profile.available" class="text-caption text-medium-emphasis">暂不可用</span>
          </button>
        </div>
      </section>

      <section class="compute-section mb-7">
        <div class="section-heading mb-3">
          <div>
            <h3 class="text-subtitle-1 font-weight-medium">云算力</h3>
            <p class="text-caption text-medium-emphasis mb-0">由平台创建并自动接入团队算力池，费用归属所选项目。</p>
          </div>
        </div>

        <v-alert v-if="!cloudConfigured" type="info" variant="tonal" density="comfortable">
          当前部署尚未接入云算力供应方；自有设备仍可正常使用。
        </v-alert>
        <v-alert v-else-if="!projects.length" type="info" variant="tonal" density="comfortable">
          先在团队里创建一个项目，云机器会以该项目作为费用与审计归属。
        </v-alert>
        <div v-else-if="!cloudMachines.length" class="empty-panel">
          <v-icon size="38" class="empty-panel-icon">mdi-cloud-outline</v-icon>
          <div>
            <div class="text-body-2 font-weight-medium">还没有云机器</div>
            <div class="text-caption text-medium-emphasis">
              管理员可按需开通，创建完成后会自动出现在话题算力选择器里。
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
                  {{ statusLabel[machine.status] }}
                </v-chip>
              </div>
              <div class="machine-meta">
                <span>{{ machine.cores }} 核</span>
                <span>{{ Math.round(machine.memory_mb / 1024) }} GB 内存</span>
                <span>{{ machine.disk_gb }} GB 磁盘</span>
              </div>
              <div class="text-caption text-medium-emphasis mt-2">费用归属：{{ machine.projectName }}</div>
              <div v-if="machine.ip" class="text-caption text-medium-emphasis mt-1">地址：{{ machine.ip }}</div>
              <div class="text-caption mt-1" :class="machine.device_id ? 'text-success' : 'text-medium-emphasis'">
                {{
                  machine.device_id
                    ? '已接入团队算力池'
                    : machine.enroll_error
                      ? `接入失败（${machine.enroll_attempts}/${machine.enroll_max_attempts}）`
                      : '等待自动接入'
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
                  释放
                </v-btn>
              </div>
            </v-card>
          </v-col>
        </v-row>
      </section>

      <section class="compute-section">
        <div class="section-heading mb-3">
          <div>
            <h3 class="text-subtitle-1 font-weight-medium">自有设备</h3>
            <p class="text-caption text-medium-emphasis mb-0">把成员已接入的机器注册给团队，工作树与数据留在机器上。</p>
          </div>
          <v-menu location="bottom end">
            <template #activator="{ props: menuProps }">
              <v-btn v-bind="menuProps" variant="outlined" prepend-icon="mdi-plus">添加自有设备</v-btn>
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
              <v-list-item v-if="!addable.length && myDevices.length" disabled title="你的设备都已在这个团队中" />
              <v-list-item v-if="!myDevices.length" :to="{ name: 'my-devices' }" title="先去「我的设备」接入机器">
                <template #prepend><v-icon size="18" class="mr-2">mdi-laptop-account</v-icon></template>
              </v-list-item>
            </v-list>
          </v-menu>
        </div>

        <div v-if="!selfHostedDevices.length" class="empty-panel">
          <v-icon size="38" class="empty-panel-icon">mdi-laptop-off</v-icon>
          <div>
            <div class="text-body-2 font-weight-medium">还没有自有设备</div>
            <div class="text-caption text-medium-emphasis">接入后，团队内所有项目都可以使用。</div>
          </div>
        </div>
        <template v-else>
          <div class="text-caption text-medium-emphasis mb-3">
            {{ selfHostedDevices.length }} 台机器 · {{ onlineCount }} 台在线
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
                    {{ device.online ? '在线' : '离线' }}
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
                    运行中 · @{{ screen.agent_handle }}
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
                    移出团队
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
        <v-card-title class="pt-5 px-5">开通云算力</v-card-title>
        <v-card-text class="px-5">
          <v-alert type="info" variant="tonal" density="compact" class="mb-4">
            创建后会持续占用云资源；释放机器会删除其本地数据。
          </v-alert>
          <v-select
            v-model="selectedProject"
            :items="projectOptions"
            label="费用与审计归属项目"
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
            团队云虚拟机已使用 {{ selectedQuota.used }} / {{ selectedQuota.limit }} 台
            <div>本项目占用 {{ selectedProjectUsage }} 台；团队内所有项目共享名额</div>
            <div v-if="!quotaFull">
              本次创建后，团队占用 {{ selectedQuota.used + 1 }} / {{ selectedQuota.limit }} 台
            </div>
            <div>停止机器不会腾出名额，释放后归还</div>
            <div v-if="quotaFull">已达到上限，请先释放不再使用的机器</div>
          </v-alert>
          <div v-else class="text-body-2 text-medium-emphasis mb-3">暂未获取团队资源用量，请刷新后重试</div>
          <v-row dense>
            <v-col cols="4"
              ><v-text-field v-model.number="cores" type="number" min="1" label="CPU 核" variant="outlined"
            /></v-col>
            <v-col cols="4"
              ><v-text-field
                v-model.number="memoryMb"
                type="number"
                min="512"
                step="512"
                label="内存 MB"
                variant="outlined"
            /></v-col>
            <v-col cols="4"
              ><v-text-field v-model.number="diskGb" type="number" min="10" label="磁盘 GB" variant="outlined"
            /></v-col>
          </v-row>
        </v-card-text>
        <v-card-actions class="px-5 pb-5">
          <v-spacer />
          <v-btn variant="text" :disabled="creating" @click="createDialog = false">取消</v-btn>
          <v-btn
            color="primary"
            variant="flat"
            :loading="creating"
            :disabled="!selectedProject || !selectedQuota || quotaFull"
            @click="provisionCloud"
            >确认开通</v-btn
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
