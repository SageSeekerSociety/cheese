// 项目设置「AI 队友」这一节的数据：队友名册，外加类型目录和模型目录两样补充，以及
// 「设为默认」「停用」两条动作。
//
// 从 `components/settings/AgentTeamSettings.vue` 拆出来：取数和动作留在这一层，组件只
// 管画和接线 —— 组件不直接碰 API 层（`.claude/rules/architecture.md`）。
//
// 三处读数只有第一处是必须的：类型目录、模型目录各自失败都不该让整节塌掉，它们只会让
// 对应的那几个字退回成默认的说法，而不是让人看不到队友。
import type { MaybeRefOrGetter } from 'vue'
import type { AgentType, ProjectAgent } from '@/cx_types'
import type { AgentFieldChoice } from '@/lib/modelChoices'

import { computed, ref, toValue } from 'vue'

import { useCachedResource } from '@/composables/useCachedResource'

import {
  deactivateProjectAgent,
  getProjectDefaultModel,
  isEndpointMissing,
  listAgentTypes,
  listProjectAgents,
  setProjectDefaultAgent,
} from '@/api'
import { t } from '@/i18n'

interface AgentsPayload {
  agents: ProjectAgent[]
  types: AgentType[]
  models: AgentFieldChoice[]
  // 后端那一半是单独上线的。没上线时这一节不能是白屏，也不能是一句看起来像
  // bug 的报错 —— 它得说清楚「功能还没到这个环境」。
  backendMissing: boolean
  // 「名册没拉回来」是这一节的一个状态，不是一次异常：照样有标题、有刷新按钮，
  // 只是列表位置换成一条错误。所以它跟数据一起走，而不是抛出去。
  loadError: string | null
}

export function useProjectAgents(projectId: MaybeRefOrGetter<string>) {
  // 进过一次的队友名册，再进来第一帧就在（useCachedResource）。
  const { data, loading, refreshing, refresh } = useCachedResource(
    () => `project-agents:${toValue(projectId)}`,
    async (): Promise<AgentsPayload> => {
      const id = toValue(projectId)
      const payload: AgentsPayload = {
        agents: [],
        types: [],
        models: [],
        backendMissing: false,
        loadError: null,
      }
      try {
        payload.agents = (await listProjectAgents(id)).data
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
        getProjectDefaultModel(id).then(
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

  // 关掉这条 alert 要连缓存里的那份一起关，不然离开这一节再回来它又弹出来。
  function dismissError() {
    actionError.value = null
    if (data.value) data.value.loadError = null
  }

  /** 设为项目默认的那一个队友；正在设的是谁，用它把那一行的按钮画成 loading。 */
  const settingDefault = ref<string | null>(null)

  async function setDefault(agent: ProjectAgent) {
    if (agent.is_default) return
    settingDefault.value = agent.id
    actionError.value = null
    try {
      await setProjectDefaultAgent(toValue(projectId), { instance_id: agent.id })
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

  /** 停用正在进行的那一个队友。 */
  const deactivating = ref(false)

  async function deactivate(agent: ProjectAgent) {
    deactivating.value = true
    actionError.value = null
    try {
      await deactivateProjectAgent(toValue(projectId), agent.id)
      // 名册在背后刷，不拖着确认框：停下的是哪一行，用户已经知道了，等这一趟回来
      // 才关框只是让转圈多转一会儿。
      void refresh()
    } catch (e) {
      actionError.value = isEndpointMissing(e)
        ? t('work.projectSettings.agents.deactivateUnsupported')
        : e instanceof Error
          ? e.message
          : t('work.projectSettings.agents.deactivateFailed')
    } finally {
      deactivating.value = false
    }
  }

  return {
    agents,
    types,
    models,
    backendMissing,
    loading,
    refreshing,
    refresh,
    error,
    dismissError,
    settingDefault,
    setDefault,
    deactivating,
    deactivate,
  }
}
