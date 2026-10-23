// 「AI 队友」编辑框背后的两件事：打开时取回这个项目的模型目录，保存时新建或更新那
// 一位队友（`POST` / `PATCH /projects/{id}/agents`）。
//
// 拆自 `components/agents/AgentEditorDialog.vue`：取数和动作在这一层，组件只管画和接线
// —— 组件不直接碰 API 层（`.claude/rules/architecture.md`）。
import type { MaybeRefOrGetter } from 'vue'
import type { AgentConfiguration, ProjectAgent } from '@/cx_types'
import type { AgentFieldChoice } from '@/lib/modelChoices'

import { ref, toValue } from 'vue'

import { createProjectAgent, getProjectDefaultModel, updateProjectAgent } from '@/api'
import { getOwnAgents } from '@/api/ownAgents'
import { t } from '@/i18n'

/** 表单收拢出来的那一份：新建和更新发的是同一个形状。 */
export interface AgentDraftPayload {
  display_name: string
  configuration: AgentConfiguration
  /** 名字有没有被改过。没改过就不发出去，见下面 `save`。 */
  nameUnchanged: boolean
  /** 新建才有：队友要占的 handle 和套用的预置。 */
  handle?: string
  type_name?: string | null
}

export function useAgentEditor(projectId: MaybeRefOrGetter<string>) {
  /** 这个项目的模型目录。取不回来就是空的，下拉只剩「跟随项目」那一项。 */
  const choices = ref<AgentFieldChoice[]>([])
  const connectionModel = ref(false)
  const modelsLoading = ref(false)
  const saving = ref(false)
  const error = ref<string | null>(null)

  /** 重新打开时清掉上一次留下的理由。 */
  function clearError() {
    error.value = null
  }

  /** 打开编辑框时问一次模型目录。失败留在 `error` 里，不挡住填写。 */
  async function loadModels(agent?: ProjectAgent | null) {
    modelsLoading.value = true
    choices.value = []
    connectionModel.value = false
    try {
      const state = await getProjectDefaultModel(toValue(projectId))
      choices.value = state.choices
      if (agent) {
        const team = await getOwnAgents(toValue(projectId))
        const connection = team.agents.find((a) => a.handle === agent.seat_handle)?.connection
        if (connection) {
          connectionModel.value = true
          choices.value = connection.allowed_models.map((id) => ({
            id,
            label: id,
            description: '',
            default: false,
            allowed: true,
            requires_plan: null,
          }))
        }
      }
    } catch (e) {
      error.value = e instanceof Error ? e.message : t('work.models.loadError')
    } finally {
      modelsLoading.value = false
    }
  }

  /**
   * 存下这一次编辑。成功返回 true —— 调用方这时候就可以把框收掉；失败返回 false 并把
   * 理由留在 `error` 里，框不关：那句话正是用户要的下一步。
   *
   * `agent` 为空是新建，否则更新那一位。**名字和打开时显示的一样就不发它**：保存默认
   * 队友的设置，不该把它现在被显示成哪个名字固定成有人挑过的名字。
   */
  async function save(agent: ProjectAgent | null, draft: AgentDraftPayload): Promise<boolean> {
    saving.value = true
    error.value = null
    try {
      if (agent?.id) {
        // ``expected_version`` rides along either way: this dialog was opened on the
        // version it read, and a save built from a stale one is refused (409).
        const expected_version = agent.version
        await updateProjectAgent(
          toValue(projectId),
          agent.id,
          draft.nameUnchanged
            ? { configuration: draft.configuration, expected_version }
            : { display_name: draft.display_name, configuration: draft.configuration, expected_version }
        )
      } else {
        await createProjectAgent(toValue(projectId), {
          display_name: draft.display_name,
          configuration: draft.configuration,
          handle: draft.handle,
          type_name: draft.type_name,
        })
      }
      return true
    } catch (e) {
      error.value = e instanceof Error ? e.message : t('work.projectSettings.agents.editor.saveFailed')
      return false
    } finally {
      saving.value = false
    }
  }

  return { choices, connectionModel, modelsLoading, saving, error, clearError, loadModels, save }
}
