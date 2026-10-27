// 发布页、改题页登记的可写面：芝士写这份草稿时表单怎么跟着动、面板上的改动卡怎么画、人怎么
// 撤销它改的格子。对话本身是全站那一条（`stores/agent.ts`），这里不再自己开。
import type { AgentFormField } from '@/composables/useAgentPointer'
import type { useTaskDraft } from '@/composables/useTaskDraft'
import type { DraftTerms } from '@/lib/taskDraftAgent'
import type { AssistantEdit } from '@/stores/agentThread'
import type { TaskDraftFields } from '@/types'

import { computed, onScopeDispose, ref } from 'vue'
import { toast } from 'vuetify-sonner'

import { ApiError } from '@/api'
import { t } from '@/i18n'
import { proposalView, stepView } from '@/lib/assistantView'
import { fieldLabel, panelMessages, resultView, writesView } from '@/lib/draftChangeView'
import { partialStringField } from '@/lib/partialJson'
import { partialValue } from '@/lib/partialJson'
import { AGENT_FIELDS, DRAFT_WRITE_PACE } from '@/lib/taskDraftAgent'
import { createTypist } from '@/lib/typist'
import { agentThread, useAgentSurface } from '@/stores/agent'

type Draft = ReturnType<typeof useTaskDraft>

export interface DraftAgentOptions {
  draft: Draft
  /** 表单的外框：芝士那一层画在这里。 */
  host: () => HTMLElement | null
  terms: () => DraftTerms
  /** 有表单可撤销。 */
  canUndo?: () => boolean
  /** 芝士新建、发布或改了别的草稿：草稿列表要重读。 */
  draftsChanged?: () => void
  /** 芝士发布了眼前这份草稿。 */
  publishedHere?: (taskId: number) => void
  /** 一份文件拆出几道题时，这份草稿是第几道（改动卡上写「第 N 道」）。 */
  ordinal?: (draftId: number) => number | undefined
  openDraft?: (id: number) => void
  openTask?: (id: number) => void
}

/** 题目表单上芝士写的那几格（按眼下的语言叫）：名称和详情是打字写的。 */
function formFields(): Record<string, AgentFormField> {
  return Object.fromEntries(
    Object.keys(AGENT_FIELDS).map((key) => [
      key,
      { label: fieldLabel(key), typed: key === 'name' || key === 'description' },
    ])
  )
}

export function useDraftAgent(options: DraftAgentOptions) {
  const { draft } = options
  const assistant = agentThread

  // 芝士写的参数按打字的节奏放进表单（见 lib/typist.ts）；调用结束、写进草稿、发布都排在
  // 放完之后。
  // The calls writing a draft (`cheese_problem(action=write)`), by call id.
  const writes = new Set<string>()
  const typist = createTypist(
    (_call, raw, held) => void draft.preview(partialValue(raw), options.terms(), held),
    DRAFT_WRITE_PACE
  )
  let after: Promise<void> = Promise.resolve()
  function then(step: () => void) {
    after = after
      .then(() => typist.idle())
      .then(step)
      .catch(() => undefined)
  }
  onScopeDispose(() => typist.stop())

  /** 芝士这次调用写的是哪份草稿（表单上方那一排里转圈的那份）：草稿 id，还没建好的新草稿是 null。 */
  const writingTarget = ref<{ draft: number | null; name: string } | null>(null)
  function aimOf(raw: string) {
    const args = partialValue(raw)
    if (!args || typeof args !== 'object' || Array.isArray(args)) return
    const given = args as Record<string, unknown>
    const name = typeof given.name === 'string' ? given.name : ''
    if (typeof given.draft === 'number') writingTarget.value = { draft: given.draft, name }
    else if ('space' in given) writingTarget.value = { draft: null, name }
  }

  const canUndo = () => options.canUndo?.() ?? true
  const terms = () => options.terms()
  const undoable = (e: AssistantEdit) => canUndo() && e.undoable && e.draft === draft.id.value

  /** 撤销一格：服务端放回这一轮写它之前的样子（芝士可能写过它几次，卡上只留最后一次），
   *  当作人改的存下；表单照着填回。人还没存的改动先存掉，免得盖过去。 */
  async function undo(changeId: number, key: string) {
    await draft.flush()
    try {
      const done = await assistant.undo(changeId, key)
      if (done.draft != null && done.fields && done.version != null)
        await draft.restored(done.draft, done.fields as TaskDraftFields, done.version)
    } catch (error) {
      toast.error(error instanceof ApiError ? error.message : t('tasks.assistant.changes.undoFailed'))
    }
  }

  /** 卡上的「全部撤销」：这张卡上还没撤销的每一格。 */
  async function undoAll(rows: { change: number; key: string }[]) {
    for (const row of rows) await undo(row.change, row.key)
  }

  /** 表单上「芝士已填写」旁的撤销：撤销最近那次写入里的这一格。 */
  async function undoMark(key: string) {
    const changeId = draft.marks.value[key]
    if (changeId !== undefined) await undo(changeId, key)
  }

  useAgentSurface(() => ({
    form: {
      host: options.host,
      fields: formFields,
      writing: () => draft.writing.value,
      passage: () => draft.passage.value,
      holding: () => draft.holding.value,
      marks: () => draft.marks.value,
      undoMark: (key) => void undoMark(key),
    },
    // 这一问挂在这一页的这份草稿上：问之前建好、存好，交回它。
    prepare: async () => {
      await draft.flush()
      const row = await draft.ensure()
      return { kind: 'draft' as const, id: row.id }
    },
    on: {
      toolArgs: (call, name, raw) => {
        // Writing a draft is one verb of `cheese_problem`; publishing it is another.
        if (name !== 'cheese_problem' || partialStringField(raw, 'action')?.text !== 'write') return
        writes.add(call)
        typist.feed(call, raw)
        aimOf(raw)
      },
      toolEnded: (call, _name, failed) => {
        if (!writes.delete(call)) return
        then(() => void draft.ended(failed))
        if (failed) writingTarget.value = null
      },
      edit: (edit) =>
        then(() => {
          writingTarget.value = null
          void draft.settle(edit)
          // The draft now names the topic that wrote it: the drafts one file split
          // into are told apart from the rest by it.
          const conversation = assistant.current.value
          if (edit.draft === draft.id.value && conversation && draft.draft.value && !draft.draft.value.conversationId)
            draft.draft.value = { ...draft.draft.value, conversationId: conversation }
          if (edit.draft !== draft.id.value || edit.created) options.draftsChanged?.()
        }),
      published: (done) =>
        then(() => {
          if (done.draft === draft.id.value) {
            draft.gone()
            options.publishedHere?.(done.task.id)
          }
          options.draftsChanged?.()
        }),
    },
    view: () => ({
      messages: panelMessages(
        assistant.messages.value,
        terms(),
        undoable,
        draft.id.value ?? null,
        options.ordinal,
        assistant.edits.value
      ),
      steps: assistant.steps.value.map(stepView),
      ...writesView(assistant.edits.value, terms(), draft.id.value ?? null, undoable, options.ordinal),
      results: assistant.published.value.map(resultView),
      proposals: assistant.proposals.value.map(proposalView),
    }),
    undo: (change, key) => void undo(change, key),
    undoAll: (rows) => void undoAll(rows),
    openDraft: options.openDraft,
    openTask: options.openTask,
  }))

  return {
    assistant,
    writingTarget,
    undo,
    undoAll,
    undoMark,
    busy: computed(() => assistant.busy.value),
  }
}
