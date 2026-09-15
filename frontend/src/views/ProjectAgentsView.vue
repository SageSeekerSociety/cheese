<script setup lang="ts">
// 「AI 队友」—— 这个项目里所有 AI 队友的管理页。
//
// 为什么值得有一整页：一个队友身上真正重要的东西是它**攒下了什么**，而这在别处
// 完全看不见。所以每一行除了名字和类型，都带两个数字 —— 记忆条数和现在有几个
// 话题在用它 —— 有没有在干活，一眼就分得出来。
//
// 页面读四处，只有第一处是必须的：队友名册。类型目录、记忆、话题各自失败都不该
// 让整页塌掉，它们只会让对应的那个数字消失，而不是让人看不到队友。
import type { MemoryEntryOut } from '../api'
import type { AgentType, ProjectAgent } from '../cx_types'

import { computed, ref } from 'vue'

import { useCachedResource } from '@/composables/useCachedResource'

import {
  deactivateProjectAgent,
  getProjectAgentOptions,
  isEndpointMissing,
  listAgentTypes,
  listMemory,
  listProjectAgents,
  listTopics,
  setProjectDefaultAgent,
} from '../api'
import AgentEditorDialog from '../components/agents/AgentEditorDialog.vue'
import UserAvatar from '../components/common/UserAvatar.vue'
import { agentKey, memoryCountsByHandle, topicCountsByAgent } from '../lib/projectAgents'
import { relTime } from '../lib/relTime'

defineOptions({ name: 'ProjectAgentsView' })

const props = defineProps<{ projectId: string }>()

interface AgentsPayload {
  agents: ProjectAgent[]
  types: AgentType[]
  memories: MemoryEntryOut[]
  topicCounts: Record<string, number>
  // 运行方式的人话名字。名字只有后端那份目录知道（HARNESSES），在这里留第二份
  // 清单就是留一份会过期的副本 —— 所以它跟别的补充数据一样，拉得到就用，拉不到
  // 就退回 id 本身。
  harnessLabels: Record<string, string>
  // 后端那一半是单独上线的。没上线时这一页不能是白屏，也不能是一句看起来像
  // bug 的报错 —— 它得说清楚「功能还没到这个环境」。
  backendMissing: boolean
  // 「名册没拉回来」是这一页的一个状态，不是一次异常：页面照样有标题、有刷新
  // 按钮，只是列表位置换成一条错误。所以它跟数据一起走，而不是抛出去。
  loadError: string | null
}

// 进过一次的队友名册，再进来第一帧就在（useCachedResource）。
const { data, loading, refreshing, refresh } = useCachedResource(
  () => `project-agents:${props.projectId}`,
  async (): Promise<AgentsPayload> => {
    const payload: AgentsPayload = {
      agents: [],
      types: [],
      memories: [],
      topicCounts: {},
      harnessLabels: {},
      backendMissing: false,
      loadError: null,
    }
    try {
      payload.agents = (await listProjectAgents(props.projectId)).data
    } catch (e) {
      if (isEndpointMissing(e)) payload.backendMissing = true
      else payload.loadError = e instanceof Error ? e.message : '加载 AI 队友失败'
      return payload
    }
    // 四个补充数据，谁失败谁空着。
    const [typeList, memoryList, topicList, harnessLabels] = await Promise.all([
      listAgentTypes().then(
        (r) => r.data,
        () => [] as AgentType[]
      ),
      listMemory(props.projectId).then(
        (r) => r.data,
        () => [] as MemoryEntryOut[]
      ),
      listTopics(props.projectId).then(
        (r) => r.data,
        () => []
      ),
      getProjectAgentOptions(props.projectId).then(
        (r) => Object.fromEntries((r.harness?.choices ?? []).map((c) => [c.id, c.label])),
        () => ({}) as Record<string, string>
      ),
    ])
    payload.types = typeList
    payload.memories = memoryList
    payload.topicCounts = topicCountsByAgent(topicList, payload.agents)
    payload.harnessLabels = harnessLabels
    return payload
  }
)

const agents = computed<ProjectAgent[]>(() => data.value?.agents ?? [])
const types = computed<AgentType[]>(() => data.value?.types ?? [])
const memories = computed<MemoryEntryOut[]>(() => data.value?.memories ?? [])
const topicCounts = computed<Record<string, number>>(() => data.value?.topicCounts ?? {})
const harnessLabels = computed<Record<string, string>>(() => data.value?.harnessLabels ?? {})
const backendMissing = computed<boolean>(() => data.value?.backendMissing ?? false)
// 名册取不回来，和「设为默认 / 停用」那一下失败，都显示在同一条 alert 上。
const actionError = ref<string | null>(null)
const error = computed<string | null>(() => actionError.value ?? data.value?.loadError ?? null)

// 关掉这条 alert 要连缓存里的那份一起关，不然离开这一页再回来它又弹出来。
function dismissError() {
  actionError.value = null
  if (data.value) data.value.loadError = null
}

const memoryCounts = computed(() => memoryCountsByHandle(memories.value, props.projectId))

const editing = ref<ProjectAgent | null>(null)
const editorOpen = ref(false)
const deactivateTarget = ref<ProjectAgent | null>(null)
const deactivating = ref(false)
// 展开看记忆的那一行。一次只展开一个 —— 这一栏是用来「看这个队友学到了什么」，
// 不是用来横向对比的。
const expanded = ref<string | null>(null)

function memoriesOf(agent: ProjectAgent): MemoryEntryOut[] {
  const prefix = `${props.projectId}:`
  return memories.value.filter((m) => m.scope === 'agent_project' && m.scope_id === `${prefix}${agent.handle}`)
}

// 一个队友是「用什么跑的」和「背后是哪个模型」两件事，现在两件都是人挑的，
// 所以名册上两件都得看得见 —— 否则两个队友一个走 pi 一个走 Claude Code，这一栏
// 长得一模一样。
function subtitleOf(agent: ProjectAgent): string {
  const model = agent.configuration.model
  const harness = agent.configuration.harness
  if (!harness) return model
  return `${harnessLabels.value[harness] ?? harness} · ${model}`
}

function openCreate() {
  editing.value = null
  editorOpen.value = true
}

function openEdit(agent: ProjectAgent) {
  editing.value = agent
  editorOpen.value = true
}

const settingDefault = ref<string | null>(null)

async function makeDefault(agent: ProjectAgent) {
  if (agent.is_default || !agent.id) return
  settingDefault.value = agentKey(agent)
  actionError.value = null
  try {
    await setProjectDefaultAgent(props.projectId, { instance_id: agent.id })
    await refresh()
  } catch (e) {
    actionError.value = isEndpointMissing(e)
      ? '这个环境还没上线默认队友的设置'
      : e instanceof Error
        ? e.message
        : '设为默认失败'
  } finally {
    settingDefault.value = null
  }
}

async function confirmDeactivate() {
  const agent = deactivateTarget.value
  if (!agent?.id) return
  deactivating.value = true
  actionError.value = null
  try {
    await deactivateProjectAgent(props.projectId, agent.id)
    deactivateTarget.value = null
    await refresh()
  } catch (e) {
    actionError.value = isEndpointMissing(e)
      ? '这个环境还没上线停用功能，队友没有变化'
      : e instanceof Error
        ? e.message
        : '停用失败'
    deactivateTarget.value = null
  } finally {
    deactivating.value = false
  }
}
</script>

<template>
  <div class="agents-page fill-height overflow-y-auto">
    <v-container class="py-6" style="max-width: 900px">
      <div class="mb-6 d-flex align-center">
        <div>
          <div class="t-eyebrow mb-1">项目</div>
          <h1 class="t-page-title">AI 队友</h1>
        </div>
        <v-spacer />
        <v-btn
          variant="text"
          icon="mdi-refresh"
          class="mr-1"
          aria-label="刷新"
          :loading="loading || refreshing"
          @click="refresh"
        />
        <v-btn
          v-if="!backendMissing"
          color="primary"
          variant="flat"
          prepend-icon="mdi-plus"
          :disabled="loading"
          @click="openCreate"
        >
          新建队友
        </v-btn>
      </div>

      <p class="t-body c-muted mb-6" style="max-width: 640px">
        每个队友有自己的角色设定和自己的记忆。新开话题默认交给标了「默认」的那一个，也可以在话题里单独换
      </p>

      <v-alert v-if="backendMissing" type="info" density="comfortable" class="mb-4">
        这个环境还没上线 AI 队友的管理功能，上线后这一页会列出项目里的所有队友
      </v-alert>

      <v-alert v-if="error" type="error" density="comfortable" class="mb-4" closable @click:close="dismissError">
        {{ error }}
      </v-alert>

      <div v-if="loading" class="d-flex justify-center py-10">
        <v-progress-circular indeterminate color="primary" />
      </div>

      <div v-else-if="!backendMissing && agents.length === 0" class="empty-state text-center py-10">
        <v-icon size="34" class="mb-3 c-muted">mdi-robot-outline</v-icon>
        <div class="t-body c-muted mb-1">暂无 AI 队友</div>
        <div class="t-caption c-muted mb-5" style="max-width: 460px; margin: 0 auto">
          队友就是在话题里和你一起干活的那个
          AI，给它一套角色设定，它在这个项目里学到的东西会一直跟着它，换个话题也还记得
        </div>
        <v-btn color="primary" variant="flat" prepend-icon="mdi-plus" @click="openCreate">新建队友</v-btn>
      </div>

      <v-card v-for="a in agents" :key="agentKey(a)" class="mb-3 pa-4" variant="outlined">
        <div class="d-flex align-center">
          <UserAvatar :name="a.display_name || a.handle" :size="36" class="mr-3" />
          <div class="min-w-0">
            <div class="d-flex align-center ga-2">
              <span class="t-title">{{ a.display_name || a.handle }}</span>
              <v-chip v-if="a.is_default" size="x-small" color="primary" variant="tonal">默认</v-chip>
              <v-chip v-if="a.is_active === false" size="x-small" variant="tonal">已停用</v-chip>
            </div>
            <div class="t-meta c-muted">@{{ a.handle }} · {{ subtitleOf(a) }}</div>
          </div>
          <v-spacer />
          <v-btn
            v-if="!a.is_default && a.is_active !== false"
            variant="text"
            size="small"
            :loading="settingDefault === agentKey(a)"
            :disabled="!a.id"
            @click="makeDefault(a)"
          >
            设为默认
          </v-btn>
          <v-btn variant="text" size="small" @click="openEdit(a)">编辑</v-btn>
          <v-btn
            v-if="a.is_active !== false"
            variant="text"
            size="small"
            color="error"
            :disabled="!a.id"
            @click="deactivateTarget = a"
          >
            停用
          </v-btn>
        </div>

        <div class="d-flex align-center ga-4 mt-3 flex-wrap">
          <button
            type="button"
            class="stat-link"
            :aria-expanded="expanded === agentKey(a)"
            @click="expanded = expanded === agentKey(a) ? null : agentKey(a)"
          >
            <v-icon size="14" class="mr-1">mdi-book-open-variant-outline</v-icon>
            {{ memoryCounts[a.handle] ?? 0 }} 条记忆
          </button>
          <span class="t-meta c-muted">
            <v-icon size="14" class="mr-1">mdi-forum-outline</v-icon>
            {{ topicCounts[agentKey(a)] ?? 0 }} 个话题在用
          </span>
        </div>

        <v-expand-transition>
          <div v-if="expanded === agentKey(a)" class="memory-list mt-3">
            <div v-if="memoriesOf(a).length === 0" class="t-meta c-muted">暂无记忆</div>
            <div v-for="m in memoriesOf(a)" :key="m.id" class="memory-row">
              <span class="t-body">{{ m.content }}</span>
              <span class="t-meta c-muted ml-2">{{ relTime(m.created_at) }}</span>
            </div>
          </div>
        </v-expand-transition>
      </v-card>
    </v-container>

    <AgentEditorDialog v-model="editorOpen" :project-id="projectId" :agent="editing" :types="types" @saved="refresh" />

    <v-dialog :model-value="deactivateTarget !== null" max-width="440" @update:model-value="deactivateTarget = null">
      <v-card v-if="deactivateTarget" class="pa-5">
        <div class="d-flex align-center mb-3">
          <v-icon color="error" class="mr-2">mdi-account-off-outline</v-icon>
          <span class="t-title">停用队友</span>
        </div>
        <div class="t-body mb-1">
          确定停用「<strong>{{ deactivateTarget.display_name || deactivateTarget.handle }}</strong
          >」吗？
        </div>
        <div class="t-caption c-muted mb-5">停用之后新话题选不到它，已经在用它的话题照常工作，它的记忆也都保留</div>
        <div class="d-flex justify-end">
          <v-btn variant="text" class="mr-2" @click="deactivateTarget = null">取消</v-btn>
          <v-btn color="error" variant="flat" :loading="deactivating" @click="confirmDeactivate">停用</v-btn>
        </div>
      </v-card>
    </v-dialog>
  </div>
</template>

<style scoped>
.min-w-0 {
  min-width: 0;
}

/* 记忆条数是可以点开的，所以它长得像个链接而不像一段说明文字。 */
.stat-link {
  display: inline-flex;
  align-items: center;
  font-size: 13px;
  color: var(--muted);
  background: transparent;
  border: 0;
  padding: 0;
  cursor: pointer;
}
.stat-link:hover {
  color: var(--text);
}

.memory-list {
  border-top: 1px solid var(--line);
  padding-top: 12px;
}
.memory-row {
  display: flex;
  align-items: baseline;
  padding: 6px 0;
}
.memory-row + .memory-row {
  border-top: 1px solid var(--line);
}
</style>
