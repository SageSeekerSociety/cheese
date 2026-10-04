<script setup lang="ts">
// 「AI 队友」—— 这个项目里所有 AI 队友，项目设置里的一节。
//
// 它曾经是一整页，而项目设置里只有仓库、分支保护和运行环境 —— 于是设置页对一个
// 没绑仓库的项目是空的，还写着「角色设定请到 AI 队友 中修改」，把人往外指。
// 队友的角色设定本来就是这个项目的设置，所以它回到这里。
//
// 每一行除了名字和类型带两个数字 —— 记忆条数和现在有几个话题在用它 —— 有没有在
// 干活，一眼就分得出来。用哪个模型不在这一行上：那是一条活的事，写在卡上。
//
// 页面读三处，只有第一处是必须的：队友名册。类型目录、记忆各自失败都不该让整页
// 塌掉，它们只会让对应的那个数字消失，而不是让人看不到队友。
import type { MemoryEntryOut } from '@/api'
import type { AgentType, ProjectAgent } from '@/cx_types'

import { computed, ref } from 'vue'

import { useCachedResource } from '@/composables/useCachedResource'

import {
  deactivateProjectAgent,
  isEndpointMissing,
  listAgentTypes,
  listMemory,
  listProjectAgents,
  setProjectDefaultAgent,
} from '@/api'
import AgentEditorDialog from '@/components/agents/AgentEditorDialog.vue'
import BaseButton from '@/components/base/BaseButton.vue'
import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import ConfirmDialog from '@/components/base/ConfirmDialog.vue'
import CheeseAvatar from '@/components/CheeseAvatar.vue'
import { t } from '@/i18n'
import { teammateName } from '@/lib/agentNames'
import { memoryCountsByHandle, typeLabel } from '@/lib/projectAgents'
import { relTime } from '@/lib/relTime'

defineOptions({ name: 'AgentTeamSettings' })

const props = defineProps<{ projectId: string }>()

interface AgentsPayload {
  agents: ProjectAgent[]
  types: AgentType[]
  memories: MemoryEntryOut[]
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
      backendMissing: false,
      loadError: null,
    }
    try {
      payload.agents = (await listProjectAgents(props.projectId)).data
    } catch (e) {
      if (isEndpointMissing(e)) payload.backendMissing = true
      else payload.loadError = e instanceof Error ? e.message : t('work.projectSettings.agents.loadFailed')
      return payload
    }
    // 两个补充数据，谁失败谁空着。
    const [typeList, memoryList] = await Promise.all([
      listAgentTypes().then(
        (r) => r.data,
        () => [] as AgentType[]
      ),
      listMemory(props.projectId).then(
        (r) => r.data,
        () => [] as MemoryEntryOut[]
      ),
    ])
    payload.types = typeList
    payload.memories = memoryList
    return payload
  }
)

const agents = computed<ProjectAgent[]>(() => data.value?.agents ?? [])
const types = computed<AgentType[]>(() => data.value?.types ?? [])
const memories = computed<MemoryEntryOut[]>(() => data.value?.memories ?? [])
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
// 确认框的开关跟着「选中的那个队友」走：有目标就是开着，关掉就把目标清掉。
const deactivateOpen = computed({
  get: () => deactivateTarget.value !== null,
  set: (value) => {
    if (!value) deactivateTarget.value = null
  },
})
const deactivateTitle = computed(() =>
  deactivateTarget.value
    ? t('work.projectSettings.agents.deactivateTitle', {
        name:
          teammateName(deactivateTarget.value.display_name, deactivateTarget.value.name_source) ||
          deactivateTarget.value.handle,
      })
    : ''
)
const deactivating = ref(false)
// 展开看记忆的那一行。一次只展开一个 —— 这一栏是用来「看这个队友学到了什么」，
// 不是用来横向对比的。
const expanded = ref<string | null>(null)

function memoriesOf(agent: ProjectAgent): MemoryEntryOut[] {
  const prefix = `${props.projectId}:`
  return memories.value.filter((m) => m.scope === 'agent_project' && m.scope_id === `${prefix}${agent.handle}`)
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
  if (agent.is_default) return
  settingDefault.value = agent.id
  actionError.value = null
  try {
    await setProjectDefaultAgent(props.projectId, { instance_id: agent.id })
    await refresh()
  } catch (e) {
    actionError.value = isEndpointMissing(e)
      ? t('work.projectSettings.agents.defaultUnsupported')
      : e instanceof Error
        ? e.message
        : t('work.projectSettings.agents.setDefaultFailed')
  } finally {
    settingDefault.value = null
  }
}

async function confirmDeactivate() {
  const agent = deactivateTarget.value
  if (!agent) return
  deactivating.value = true
  actionError.value = null
  try {
    await deactivateProjectAgent(props.projectId, agent.id)
    deactivateTarget.value = null
    await refresh()
  } catch (e) {
    actionError.value = isEndpointMissing(e)
      ? t('work.projectSettings.agents.deactivateUnsupported')
      : e instanceof Error
        ? e.message
        : t('work.projectSettings.agents.deactivateFailed')
    deactivateTarget.value = null
  } finally {
    deactivating.value = false
  }
}
</script>

<template>
  <section class="page-section agent-team">
    <div class="page-section-head">
      <v-icon size="14" class="c-faint">mdi-robot-outline</v-icon>
      <span class="page-section-title">{{ t('work.projectSettings.agents.title') }}</span>
      <v-spacer />
      <BaseButton
        icon="mdi-refresh"
        size="sm"
        class="mr-1"
        :aria-label="t('work.projectSettings.agents.refresh')"
        :loading="loading || refreshing"
        @click="refresh"
      />
      <BaseButton
        v-if="!backendMissing"
        kind="primary"
        size="sm"
        prepend-icon="mdi-plus"
        :disabled="loading"
        @click="openCreate"
      >
        {{ t('work.projectSettings.agents.create') }}
      </BaseButton>
    </div>
    <div class="page-section-body">
      <p class="t-body c-muted mb-6" style="max-width: 640px">
        {{ t('work.projectSettings.agents.intro') }}
      </p>

      <v-alert v-if="backendMissing" type="info" density="comfortable" class="mb-4">
        {{ t('work.projectSettings.agents.unsupported') }}
      </v-alert>

      <v-alert v-if="error" type="error" density="comfortable" class="mb-4" closable @click:close="dismissError">
        {{ error }}
      </v-alert>

      <div v-if="loading" class="d-flex justify-center py-10">
        <v-progress-circular indeterminate color="primary" />
      </div>

      <BaseEmptyState
        v-else-if="!backendMissing && agents.length === 0"
        size="compact"
        icon="mdi-robot-outline"
        :title="t('work.projectSettings.agents.empty')"
      >
        <BaseButton kind="primary" prepend-icon="mdi-plus" class="mt-4" @click="openCreate">
          {{ t('work.projectSettings.agents.create') }}
        </BaseButton>
      </BaseEmptyState>

      <v-card v-for="a in agents" :key="a.id" class="mb-3 pa-4" variant="outlined">
        <div class="agent-head">
          <div class="agent-head__id">
            <CheeseAvatar
              :name="teammateName(a.display_name, a.name_source) || a.handle"
              :handle="a.seat_handle"
              :size="36"
              class="mr-3 flex-shrink-0"
            />
            <div class="min-w-0">
              <div class="d-flex align-center flex-wrap ga-2">
                <span class="t-title agent-head__name">{{
                  teammateName(a.display_name, a.name_source) || a.handle
                }}</span>
                <v-chip v-if="a.is_default" size="x-small" color="primary" variant="tonal">{{
                  t('work.projectSettings.agents.default')
                }}</v-chip>
                <v-chip v-if="a.is_active === false" size="x-small" variant="tonal">{{
                  t('work.projectSettings.agents.inactive')
                }}</v-chip>
              </div>
              <div class="t-meta c-muted agent-head__name">@{{ a.handle }} · {{ typeLabel(types, a.type_name) }}</div>
            </div>
          </div>
          <div class="agent-head__actions">
            <BaseButton
              v-if="!a.is_default && a.is_active !== false"
              size="sm"
              :loading="settingDefault === a.id"
              @click="makeDefault(a)"
            >
              {{ t('work.projectSettings.agents.setDefault') }}
            </BaseButton>
            <BaseButton size="sm" @click="openEdit(a)">{{ t('work.projectSettings.agents.edit') }}</BaseButton>
            <BaseButton v-if="a.is_active !== false" size="sm" @click="deactivateTarget = a">
              {{ t('work.projectSettings.agents.deactivate') }}
            </BaseButton>
          </div>
        </div>

        <div class="d-flex align-center ga-4 mt-3 flex-wrap">
          <button
            type="button"
            class="stat-link"
            :aria-expanded="expanded === a.id"
            @click="expanded = expanded === a.id ? null : a.id"
          >
            <v-icon size="14" class="mr-1">mdi-book-open-variant-outline</v-icon>
            {{ t('work.projectSettings.agents.memories', { n: memoryCounts[a.handle] ?? 0 }) }}
          </button>
        </div>

        <v-expand-transition>
          <div v-if="expanded === a.id" class="memory-list mt-3">
            <div v-if="memoriesOf(a).length === 0" class="t-meta c-muted">
              {{ t('work.projectSettings.agents.noMemories') }}
            </div>
            <div v-for="m in memoriesOf(a)" :key="m.id" class="memory-row">
              <span class="t-body">{{ m.content }}</span>
              <span class="t-meta c-muted ml-2">{{ relTime(m.created_at) }}</span>
            </div>
          </div>
        </v-expand-transition>
      </v-card>
    </div>

    <AgentEditorDialog v-model="editorOpen" :project-id="projectId" :agent="editing" :types="types" @saved="refresh" />

    <ConfirmDialog
      v-model="deactivateOpen"
      :title="deactivateTitle"
      :confirm-label="t('work.projectSettings.agents.deactivate')"
      danger
      :loading="deactivating"
      @confirm="confirmDeactivate"
    >
      {{ t('work.projectSettings.agents.deactivateHint') }}
    </ConfirmDialog>
  </section>
</template>

<style scoped>
.min-w-0 {
  min-width: 0;
}

/* 名字一组、按钮一组：放不下时整组按钮换到下一行靠右，而不是把名字挤成一列，
   也不是三颗按钮一颗一颗地掉下去。 */
.agent-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px 8px;
}
.agent-head__id {
  display: flex;
  flex: 1 1 auto;
  align-items: center;
  min-width: 0;
}
.agent-head__name {
  overflow-wrap: anywhere;
}
.agent-head__actions {
  display: flex;
  flex: none;
  align-items: center;
  margin-inline-start: auto;
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
