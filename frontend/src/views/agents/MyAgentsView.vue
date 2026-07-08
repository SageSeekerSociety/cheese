<!-- 「我的 Agent」管理页（owner 视角，与 project 无关）。
     两个板块：Agent 管理 + 设备管理，均由可复用的 <SectionBlock> + 响应式卡片网格拼成，
     便于日后追加更多板块或每个 agent 的更多操作。数据来自 GET /connector/my/devices。 -->
<template>
  <div class="my-agents">
    <div class="my-agents__inner">
      <h1 class="my-agents__page-title">我的 Agent</h1>

      <v-alert v-if="error" type="error" variant="tonal" class="mb-6" closable @click:close="error = ''">
        {{ error }}
      </v-alert>

      <!-- 板块一：Agent 管理 -->
      <SectionBlock
        title="Agent 管理"
        subtitle="创建并管理你在各设备上的 AI 队友"
        icon="mdi-robot-happy-outline"
        :count="allAgents.length"
      >
        <template #actions>
          <v-btn color="primary" variant="flat" prepend-icon="mdi-plus" @click="openCreate">
            创建 agent
          </v-btn>
        </template>

        <div v-if="loading" class="my-agents__loading">
          <v-progress-circular indeterminate color="primary" />
        </div>

        <div v-else-if="!allAgents.length" class="my-agents__empty">
          还没有 agent，点右上角「创建 agent」在某台设备上创建一个。
        </div>

        <div v-else class="card-grid">
          <v-card v-for="a in allAgents" :key="a.user_id" class="agent-card" variant="outlined" rounded="lg">
            <div class="agent-card__body">
              <UserAvatar :member="a as unknown as PresenceMember" :size="52" class="agent-card__avatar" />
              <div class="agent-card__meta">
                <div class="agent-card__name">{{ a.nickname || `agent#${a.user_id}` }}</div>
                <div class="agent-card__status">
                  <span class="status-dot" :class="{ 'status-dot--on': isAgentOnline(a) }" />
                  <span>{{ agentStatusText(a) }}</span>
                </div>
                <div class="agent-card__device">
                  <v-icon icon="mdi-desktop-classic" size="13" class="me-1" />{{ deviceNameOf(a) }}
                </div>
              </div>
            </div>
            <v-divider />
            <v-card-actions class="agent-card__actions">
              <v-btn size="small" variant="text" prepend-icon="mdi-rename-box" @click="openRename(a)">改名</v-btn>
              <v-btn size="small" variant="text" prepend-icon="mdi-image-edit" @click="openAvatar(a)">头像</v-btn>
              <v-spacer />
              <v-btn
                size="small"
                variant="text"
                icon="mdi-backup-restore"
                :loading="recreating === a.user_id"
                :disabled="!isAgentOnline(a)"
                title="重启并恢复会话（复用原用户与对话历史）"
                @click="recreateAgentRow(a)"
              />
              <v-btn
                size="small"
                variant="text"
                color="error"
                icon="mdi-delete-outline"
                :loading="busyAgent === a.sid"
                :disabled="!a.sid"
                title="删除"
                @click="removeAgent(a)"
              />
            </v-card-actions>
          </v-card>
        </div>
      </SectionBlock>

      <!-- 板块二：设备管理 -->
      <SectionBlock
        title="设备管理"
        subtitle="接入的客户机，agent 在其上运行"
        icon="mdi-desktop-classic"
        :count="devices.length"
      >
        <template #actions>
          <v-btn color="primary" variant="tonal" prepend-icon="mdi-plus" @click="showAddDevice = true">
            添加设备
          </v-btn>
        </template>

        <div v-if="loading" class="my-agents__loading">
          <v-progress-circular indeterminate color="primary" />
        </div>

        <div v-else-if="!devices.length" class="my-agents__empty">
          还没有设备，点右上角「添加设备」按教程接入一台。
        </div>

        <div v-else class="card-grid">
          <v-card v-for="d in devices" :key="d.device_id" class="device-card" variant="outlined" rounded="lg">
            <div class="device-card__body">
              <v-avatar size="40" color="primary" variant="tonal" class="me-3">
                <v-icon icon="mdi-desktop-classic" />
              </v-avatar>
              <div class="device-card__meta">
                <div class="device-card__name">{{ d.name || d.device_id }}</div>
                <div class="device-card__status">
                  <span class="status-dot" :class="{ 'status-dot--on': d.online }" />
                  <span>{{ d.online ? '在线' : '离线' }} · {{ d.agents.length }} 个 agent</span>
                </div>
              </div>
            </div>
            <v-divider />
            <v-card-actions class="device-card__actions">
              <v-btn size="small" variant="text" prepend-icon="mdi-rename-box" @click="openRenameDevice(d)">重命名</v-btn>
              <v-spacer />
              <v-btn
                size="small"
                variant="text"
                color="error"
                icon="mdi-delete-outline"
                :loading="busyDevice === d.device_id"
                title="删除"
                @click="removeDevice(d)"
              />
            </v-card-actions>
          </v-card>
        </div>
      </SectionBlock>
    </div>

    <!-- 创建 agent 对话框 -->
    <v-dialog v-model="showCreate" max-width="440">
      <v-card rounded="lg">
        <v-card-title>创建 agent</v-card-title>
        <v-card-text>
          <v-select
            v-model="createForm.device_id"
            :items="onlineDeviceOptions"
            item-title="title"
            item-value="value"
            label="选择设备（仅在线）"
            variant="outlined"
            density="comfortable"
            :no-data-text="'没有在线设备，请先接入一台设备'"
          />
          <v-text-field
            v-model="createForm.nickname"
            label="昵称（可选）"
            variant="outlined"
            density="comfortable"
          />
          <v-select
            v-model="createForm.mode"
            :items="createModeOptions"
            item-title="title"
            item-value="value"
            label="创建方式"
            variant="outlined"
            density="comfortable"
          />

          <!-- 「复制自」：把某个已有 agent 的 Claude 会话复制到目标机并从中恢复。 -->
          <template v-if="createForm.mode === 'copy'">
            <v-select
              v-model="createForm.copy_from"
              :items="copyFromOptions"
              item-title="title"
              item-value="value"
              label="源 agent"
              variant="outlined"
              density="comfortable"
              hint="它的 Claude 会话会被复制到目标机并从中恢复；源 agent 所在机器需在线"
              persistent-hint
            />
            <v-text-field
              v-model="createForm.target_cwd"
              label="目标工作目录（可选）"
              variant="outlined"
              density="comfortable"
              hint="目标机上的工作目录；不填则沿用源 agent 的目录。目录不存在会自动创建。"
              persistent-hint
            />
          </template>

          <!-- 「接入已有会话」：resume 目标设备上已经存在的一个 Claude 会话（例如你自己在
               那台机器上手动跑过 claude），而不是新建或复制一份。 -->
          <template v-else-if="createForm.mode === 'attach'">
            <v-alert type="info" variant="tonal" density="compact" class="mb-4 text-body-2">
              前提：该会话是在<strong>上面选中的这台设备</strong>上，用 <code>claude</code> 跑出来的——
              cheese 目前只能接管自己知道的机器，接管不了机器上随便一个终端。
            </v-alert>
            <div class="tut-step">
              <div class="tut-step__num">1</div>
              <div class="tut-step__content">
                <div class="tut-step__title">在目标设备上，进入该会话对应的项目目录，执行：</div>
                <div class="tut-code">
                  <code>{{ attachLookupCmd }}</code>
                  <v-btn icon="mdi-content-copy" size="x-small" variant="text" @click="copy(attachLookupCmd)" />
                </div>
                <div class="text-body-2 text-medium-emphasis mt-2">
                  输出就是这个目录里最近一次会话的 session id；把这个目录的<strong>绝对路径</strong>和这个 id
                  分别填到下面两个框里。
                </div>
              </div>
            </div>
            <v-text-field
              v-model="createForm.attach_session_id"
              label="Claude session id"
              variant="outlined"
              density="comfortable"
              class="mt-2"
              placeholder="例如 38747be3-845e-44ea-8b87-732509654485"
            />
            <v-text-field
              v-model="createForm.attach_cwd"
              label="工作目录（绝对路径）"
              variant="outlined"
              density="comfortable"
              placeholder="例如 /home/nictheboy/repo/SageSeekerSociety"
              hint="必须和上面命令执行时的目录一致，否则 Claude 找不到这个会话的记录"
              persistent-hint
            />
          </template>
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" @click="showCreate = false">取消</v-btn>
          <v-btn
            color="primary"
            variant="flat"
            :loading="creating"
            :disabled="!canSubmitCreate"
            @click="submitCreate"
          >
            创建
          </v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <!-- 改名对话框 -->
    <v-dialog v-model="showRename" max-width="420">
      <v-card rounded="lg">
        <v-card-title>改名</v-card-title>
        <v-card-text>
          <v-text-field v-model="renameForm.nickname" label="昵称" variant="outlined" density="comfortable" autofocus />
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" @click="showRename = false">取消</v-btn>
          <v-btn color="primary" variant="flat" :loading="savingRename" @click="submitRename">保存</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <!-- 设置头像对话框（复用项目现有的 AvatarUploader + AvatarsApi） -->
    <v-dialog v-model="showAvatar" max-width="360">
      <v-card rounded="lg">
        <v-card-title>设置头像</v-card-title>
        <v-card-text class="d-flex justify-center">
          <div style="width: 180px">
            <AvatarUploader v-model="avatarFile" @error="onAvatarError" />
          </div>
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" @click="showAvatar = false">取消</v-btn>
          <v-btn color="primary" variant="flat" :loading="savingAvatar" :disabled="!avatarFile" @click="submitAvatar">
            保存
          </v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <!-- 重命名设备对话框 -->
    <v-dialog v-model="showRenameDevice" max-width="420">
      <v-card rounded="lg">
        <v-card-title>重命名设备</v-card-title>
        <v-card-text>
          <v-text-field
            v-model="renameDeviceForm.name"
            label="设备名称"
            variant="outlined"
            density="comfortable"
            autofocus
          />
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" @click="showRenameDevice = false">取消</v-btn>
          <v-btn color="primary" variant="flat" :loading="savingRenameDevice" @click="submitRenameDevice">保存</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <!-- 「添加设备」对话框：不是表单，而是接入教程 -->
    <v-dialog v-model="showAddDevice" max-width="560">
      <v-card rounded="lg">
        <v-card-title class="d-flex align-center">
          <v-icon icon="mdi-plus-network-outline" class="me-2" />接入一台新设备
        </v-card-title>
        <v-card-text>
          <p class="mb-3 text-body-2 text-medium-emphasis">
            在你要接入的机器上执行这两条命令即可，没有网页表单。
          </p>
          <v-alert
            type="info"
            variant="tonal"
            density="compact"
            class="mb-4 text-body-2"
            icon="mdi-laptop"
          >
            支持 <strong>Linux</strong> 与 <strong>macOS</strong>（x86-64 / ARM64），<strong>暂不支持 Windows</strong>。
            前置条件只有一个：本机已安装并登录 <strong>Claude Code</strong>。
          </v-alert>

          <div class="tut-step">
            <div class="tut-step__num">1</div>
            <div class="tut-step__content">
              <div class="tut-step__title">安装</div>
              <div class="tut-code">
                <code>{{ installCmd }}</code>
                <v-btn icon="mdi-content-copy" size="x-small" variant="text" @click="copy(installCmd)" />
              </div>
            </div>
          </div>

          <div class="tut-step">
            <div class="tut-step__num">2</div>
            <div class="tut-step__content">
              <div class="tut-step__title">连接（自动登录并常驻）</div>
              <div class="tut-code">
                <code>{{ connectCmd }}</code>
                <v-btn icon="mdi-content-copy" size="x-small" variant="text" @click="copy(connectCmd)" />
              </div>
              <div class="text-body-2 text-medium-emphasis mt-2">
                它会打印一个批准链接——用<strong>已登录的账号</strong>在浏览器打开并批准，这台机器就绑定到你名下了。
                之后它会作为系统服务常驻，开机自动恢复；需要 root 时会自动提权。
              </div>
              <div class="text-body-2 text-medium-emphasis mt-2">
                完成后，这台设备会出现在上方「设备管理」中，你就能在这里创建 agent 了。
              </div>
            </div>
          </div>
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn color="primary" variant="text" @click="showAddDevice = false">我知道了</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { toast } from 'vuetify-sonner'

import AvatarUploader from '@/components/common/AvatarUploader.vue'
import UserAvatar from '@/components/common/UserAvatar.vue'

import { AvatarsApi } from '@/network/api/avatars'
import * as AgentsApi from '@/network/api/agents'
import type { Device, Member } from '@/network/api/agents'
import type { Member as PresenceMember } from '@/network/api/threads'

import SectionBlock from './components/SectionBlock.vue'

const devices = ref<Device[]>([])
const loading = ref(false)
const error = ref('')
const busyAgent = ref<string | null>(null)
const busyDevice = ref<string | null>(null)
const recreating = ref<number | null>(null)

// 跨设备扁平化所有 agent，并记住其所在设备（用于展示设备名 / 在线态）。
interface AgentRow extends Member {
  _deviceId: string
  _deviceName: string
  _deviceOnline: boolean
}

const allAgents = computed<AgentRow[]>(() =>
  devices.value.flatMap((d) =>
    d.agents.map((a) => ({
      ...a,
      _deviceId: d.device_id,
      _deviceName: d.name || d.device_id,
      _deviceOnline: d.online,
    })),
  ),
)

const onlineDeviceOptions = computed(() =>
  devices.value
    .filter((d) => d.online)
    .map((d) => ({ title: d.name || d.device_id, value: d.device_id })),
)

function isAgentOnline(a: AgentRow): boolean {
  return a._deviceOnline && !!a.sid
}

function agentStatusText(a: AgentRow): string {
  if (!a._deviceOnline) return '离线'
  if (a.agent_status) return a.agent_status
  return a.sid ? '在线' : '空闲'
}

function deviceNameOf(a: AgentRow): string {
  return a._deviceName
}

async function load(): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    const { devices: ds } = await AgentsApi.listMyDevices()
    devices.value = ds
  } catch (e) {
    error.value = e instanceof Error ? e.message : '加载失败'
  } finally {
    loading.value = false
  }
}

onMounted(load)

// —— 创建 agent ——
const showCreate = ref(false)
const creating = ref(false)
type CreateMode = 'fresh' | 'copy' | 'attach'
const createForm = ref<{
  device_id: string | null
  nickname: string
  mode: CreateMode
  copy_from: number | null
  target_cwd: string
  attach_session_id: string
  attach_cwd: string
}>({
  device_id: null,
  nickname: '',
  mode: 'fresh',
  copy_from: null,
  target_cwd: '',
  attach_session_id: '',
  attach_cwd: '',
})

const createModeOptions: { title: string; value: CreateMode }[] = [
  { title: '从零开始', value: 'fresh' },
  { title: '复制已有 agent 的会话', value: 'copy' },
  { title: '接入设备上已有的 Claude 会话', value: 'attach' },
]

// 「复制自」下拉：每个已有 agent（复制其 Claude 会话）。
const copyFromOptions = computed(() =>
  allAgents.value.map((a) => ({
    title: `${a.nickname || `agent#${a.user_id}`}${a._deviceOnline ? '' : '（离线）'}`,
    value: a.user_id as number | null,
  })),
)

// 「接入已有会话」教程里的查找命令：cd 到会话对应的项目目录后执行，取该目录最近一次
// 会话的 session id（Claude 按 cwd 的 slug 存 transcript，文件名去掉 .jsonl 就是 id）。
const attachLookupCmd =
  'slug=$(pwd | sed \'s/\\//-/g\'); ls -t ~/.claude/projects/"$slug"/*.jsonl 2>/dev/null | head -1 | xargs -n1 basename | sed \'s/\\.jsonl$//\''

const canSubmitCreate = computed(() => {
  if (!createForm.value.device_id) return false
  if (createForm.value.mode === 'copy') return createForm.value.copy_from != null
  if (createForm.value.mode === 'attach') {
    return !!createForm.value.attach_session_id.trim() && !!createForm.value.attach_cwd.trim()
  }
  return true
})

function openCreate(): void {
  createForm.value = {
    device_id: onlineDeviceOptions.value[0]?.value ?? null,
    nickname: '',
    mode: 'fresh',
    copy_from: null,
    target_cwd: '',
    attach_session_id: '',
    attach_cwd: '',
  }
  showCreate.value = true
}

async function submitCreate(): Promise<void> {
  if (!createForm.value.device_id || !canSubmitCreate.value) return
  creating.value = true
  try {
    await AgentsApi.createAgent({
      device_id: createForm.value.device_id,
      nickname: createForm.value.nickname || undefined,
      copy_from_agent_user_id: createForm.value.mode === 'copy' ? createForm.value.copy_from ?? undefined : undefined,
      // 仅在「复制自」时透传目标目录；不填则由后端沿用源 agent 的 cwd。
      target_cwd:
        createForm.value.mode === 'copy' && createForm.value.target_cwd.trim()
          ? createForm.value.target_cwd.trim()
          : undefined,
      attach_session_id:
        createForm.value.mode === 'attach' ? createForm.value.attach_session_id.trim() : undefined,
      attach_cwd: createForm.value.mode === 'attach' ? createForm.value.attach_cwd.trim() : undefined,
    })
    toast.success(
      createForm.value.mode === 'copy'
        ? '已复制并创建 agent'
        : createForm.value.mode === 'attach'
          ? '已接入该会话'
          : '已创建 agent',
    )
    showCreate.value = false
    await load()
  } catch (e) {
    toast.error(e instanceof Error ? e.message : '创建失败')
  } finally {
    creating.value = false
  }
}

// —— 删除 agent ——
async function removeAgent(a: AgentRow): Promise<void> {
  if (!a.sid) {
    toast.error('该 agent 当前不在线，无法删除')
    return
  }
  busyAgent.value = a.sid
  try {
    await AgentsApi.deleteAgent(a.sid)
    toast.success('已删除')
    await load()
  } catch (e) {
    toast.error(e instanceof Error ? e.message : '删除失败')
  } finally {
    busyAgent.value = null
  }
}

// —— 重启并恢复会话（复用原用户 + 原 Claude session）——
async function recreateAgentRow(a: AgentRow): Promise<void> {
  if (!isAgentOnline(a)) return
  if (!window.confirm(`将结束「${a.nickname || `agent#${a.user_id}`}」当前的现场，并用同一个 Claude 会话重启它。继续？`))
    return
  recreating.value = a.user_id
  try {
    // 现场仍存活 → force；resume=true 沿用原会话继续对话。
    await AgentsApi.recreateAgent(a.user_id, { resume: true, force: true })
    toast.success('已重启并恢复会话')
    await load()
    setTimeout(load, 3000)
  } catch (e) {
    toast.error(e instanceof Error ? e.message : '重启失败')
  } finally {
    recreating.value = null
  }
}

// —— 改名 ——
const showRename = ref(false)
const savingRename = ref(false)
const renameForm = ref<{ agentUserId: number; nickname: string }>({ agentUserId: 0, nickname: '' })

function openRename(a: AgentRow): void {
  renameForm.value = { agentUserId: a.user_id, nickname: a.nickname || '' }
  showRename.value = true
}

async function submitRename(): Promise<void> {
  savingRename.value = true
  try {
    await AgentsApi.updateAgent(renameForm.value.agentUserId, { nickname: renameForm.value.nickname })
    toast.success('已更新')
    showRename.value = false
    await load()
  } catch (e) {
    toast.error(e instanceof Error ? e.message : '更新失败')
  } finally {
    savingRename.value = false
  }
}

// —— 设置头像（复用 AvatarUploader 上传 → 取回 avatarId → PATCH agent） ——
const showAvatar = ref(false)
const savingAvatar = ref(false)
const avatarFile = ref<File>()
const avatarTargetId = ref(0)

function openAvatar(a: AgentRow): void {
  avatarTargetId.value = a.user_id
  avatarFile.value = undefined
  showAvatar.value = true
}

function onAvatarError(e: Error): void {
  toast.error(e.message || '头像选择失败')
}

async function submitAvatar(): Promise<void> {
  if (!avatarFile.value) return
  savingAvatar.value = true
  try {
    const { data } = await AvatarsApi.createAvatar(avatarFile.value)
    await AgentsApi.updateAgent(avatarTargetId.value, { avatar_id: data.avatarId })
    toast.success('头像已更新')
    showAvatar.value = false
    await load()
  } catch (e) {
    toast.error(e instanceof Error ? e.message : '设置头像失败')
  } finally {
    savingAvatar.value = false
  }
}

// —— 重命名设备 ——
const showRenameDevice = ref(false)
const savingRenameDevice = ref(false)
const renameDeviceForm = ref<{ device_id: string; name: string }>({ device_id: '', name: '' })

function openRenameDevice(d: Device): void {
  renameDeviceForm.value = { device_id: d.device_id, name: d.name || '' }
  showRenameDevice.value = true
}

async function submitRenameDevice(): Promise<void> {
  savingRenameDevice.value = true
  try {
    await AgentsApi.renameDevice(renameDeviceForm.value.device_id, renameDeviceForm.value.name)
    toast.success('已重命名')
    showRenameDevice.value = false
    await load()
  } catch (e) {
    toast.error(e instanceof Error ? e.message : '重命名失败')
  } finally {
    savingRenameDevice.value = false
  }
}

// —— 删除设备 ——
async function removeDevice(d: Device): Promise<void> {
  busyDevice.value = d.device_id
  try {
    await AgentsApi.deleteDevice(d.device_id)
    toast.success('已删除设备')
    await load()
  } catch (e) {
    toast.error(e instanceof Error ? e.message : '删除失败')
  } finally {
    busyDevice.value = null
  }
}

// —— 添加设备（教程） ——
const showAddDevice = ref(false)
const origin = typeof window !== 'undefined' ? window.location.origin : ''
// No sudo: binary → ~/.local/bin, config/tmux → your home.
const installCmd = computed(() => `curl ${origin}/install.sh | sh`)
// auto-connect logs in (prints an approve link) and installs the boot service; it
// self-elevates just to install a *system* service that runs as you.
const connectCmd = 'cheese link auto-connect'

async function copy(text: string): Promise<void> {
  // navigator.clipboard only exists in secure contexts (https / localhost). The demo is
  // served over plain http, so fall back to the legacy execCommand path there.
  try {
    if (window.isSecureContext && navigator.clipboard) {
      await navigator.clipboard.writeText(text)
      toast.success('已复制')
      return
    }
    throw new Error('insecure context')
  } catch {
    if (legacyCopy(text)) {
      toast.success('已复制')
    } else {
      toast.error('复制失败，请手动选择')
    }
  }
}

function legacyCopy(text: string): boolean {
  try {
    const ta = document.createElement('textarea')
    ta.value = text
    ta.style.position = 'fixed'
    ta.style.opacity = '0'
    document.body.appendChild(ta)
    ta.focus()
    ta.select()
    const ok = document.execCommand('copy')
    document.body.removeChild(ta)
    return ok
  } catch {
    return false
  }
}
</script>

<style scoped lang="scss">
.my-agents {
  padding: 24px;

  &__inner {
    max-width: 1080px;
    margin: 0 auto;
  }

  &__page-title {
    font-size: 24px;
    font-weight: 700;
    margin-bottom: 24px;
  }

  &__loading,
  &__empty {
    padding: 32px;
    text-align: center;
    color: rgba(var(--v-theme-on-surface), 0.55);
    font-size: 14px;
  }
}

.card-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: 16px;
}

.agent-card,
.device-card {
  display: flex;
  flex-direction: column;

  &__body {
    display: flex;
    align-items: center;
    padding: 16px;
  }

  &__actions {
    padding: 4px 8px;
  }
}

.agent-card {
  &__avatar {
    margin-right: 14px;
  }

  &__meta {
    min-width: 0;
    flex: 1;
  }

  &__name {
    font-weight: 600;
    font-size: 15px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  &__status,
  &__device {
    display: flex;
    align-items: center;
    font-size: 12px;
    color: rgba(var(--v-theme-on-surface), 0.6);
    margin-top: 3px;
  }
}

.device-card {
  &__meta {
    min-width: 0;
    flex: 1;
  }

  &__name {
    font-weight: 600;
    font-size: 15px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  &__status {
    display: flex;
    align-items: center;
    font-size: 12px;
    color: rgba(var(--v-theme-on-surface), 0.6);
    margin-top: 3px;
  }
}

.status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: rgba(var(--v-theme-on-surface), 0.3);
  margin-right: 6px;
  flex: none;

  &--on {
    background: #22c55e;
  }
}

.tut-step {
  display: flex;
  gap: 12px;
  margin-bottom: 18px;

  &__num {
    flex: none;
    width: 24px;
    height: 24px;
    border-radius: 50%;
    background: rgb(var(--v-theme-primary));
    color: #fff;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 13px;
    font-weight: 700;
  }

  &__content {
    flex: 1;
    min-width: 0;
  }

  &__title {
    font-weight: 600;
    font-size: 14px;
    margin-bottom: 6px;
  }
}

.tut-code {
  display: flex;
  align-items: center;
  gap: 8px;
  background: rgba(var(--v-theme-on-surface), 0.06);
  border-radius: 6px;
  padding: 6px 8px 6px 12px;
  font-family: monospace;
  font-size: 13px;

  code {
    flex: 1 1 auto;
    min-width: 0;
    overflow-x: auto;
    white-space: nowrap;
  }

  // copy button never gets pushed out of view by a long command
  .v-btn {
    flex: 0 0 auto;
  }
}
</style>
