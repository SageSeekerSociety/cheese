<script setup lang="ts">
// 「AI 队友」—— 这个项目里所有 AI 队友，项目设置里的一节。
//
// 它曾经是一整页，而项目设置里只有仓库、分支保护和运行环境 —— 于是设置页对一个
// 没绑仓库的项目是空的，还写着「角色设定请到 AI 队友 中修改」，把人往外指。
// 队友的角色设定本来就是这个项目的设置，所以它回到这里。
//
// 每一行除了名字和类型，还写着它跑在哪个模型上、思考强度是哪一档：两样合起来才
// 说得出这个队友多快、多贵。记忆不在这里：它由平台统一管理，不是队友的一项设置。
//
// 页面读三处，只有第一处是必须的：队友名册。类型目录、模型目录各自失败都不该让整
// 页塌掉，它们只会让对应的那几个字退回成默认的说法，而不是让人看不到队友。
import type { AgentType, ProjectAgent } from '@/cx_types'
import type { AgentFieldChoice } from '@/lib/modelChoices'

import { computed, ref } from 'vue'

import { useCachedResource } from '@/composables/useCachedResource'

import {
  deactivateProjectAgent,
  getProjectDefaultModel,
  isEndpointMissing,
  listAgentTypes,
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
import { effortLabel, typeLabel } from '@/lib/projectAgents'

defineOptions({ name: 'AgentTeamSettings' })

const props = defineProps<{ projectId: string }>()

interface AgentsPayload {
  agents: ProjectAgent[]
  types: AgentType[]
  models: AgentFieldChoice[]
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
      models: [],
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
    const [typeList, modelList] = await Promise.all([
      listAgentTypes().then(
        (r) => r.data,
        () => [] as AgentType[]
      ),
      getProjectDefaultModel(props.projectId).then(
        (r) => r.choices,
        () => [] as AgentFieldChoice[]
      ),
    ])
    payload.types = typeList
    payload.models = modelList
    return payload
  }
)

const agents = computed<ProjectAgent[]>(() => data.value?.agents ?? [])
const types = computed<AgentType[]>(() => data.value?.types ?? [])
const models = computed<AgentFieldChoice[]>(() => data.value?.models ?? [])
const backendMissing = computed<boolean>(() => data.value?.backendMissing ?? false)
// 名册取不回来，和「设为默认 / 停用」那一下失败，都显示在同一条 alert 上。
const actionError = ref<string | null>(null)
const error = computed<string | null>(() => actionError.value ?? data.value?.loadError ?? null)

// 关掉这条 alert 要连缓存里的那份一起关，不然离开这一页再回来它又弹出来。
function dismissError() {
  actionError.value = null
  if (data.value) data.value.loadError = null
}

// 「跟随项目」时也把项目那个模型的名字写出来：否则这一行说不清它到底跑在哪。
function modelOf(agent: ProjectAgent): string {
  const chosen = agent.configuration.model
  if (chosen) {
    const label = models.value.find((m) => m.id === chosen)?.label ?? chosen
    return t('work.projectSettings.agents.rowModel', { model: label })
  }
  const main = models.value.find((m) => m.default)
  return t('work.projectSettings.agents.rowModel', {
    model: main
      ? t('work.projectSettings.agents.rowInherit', { model: main.label })
      : t('work.projectSettings.agents.rowInheritPlain'),
  })
}

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

        <div class="agent-facts t-meta c-muted mt-3">
          <span>{{ modelOf(a) }}</span>
          <span>{{ t('work.projectSettings.agents.rowEffort', { effort: effortLabel(a.configuration.effort) }) }}</span>
        </div>
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

.agent-facts {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 16px;
}
</style>
