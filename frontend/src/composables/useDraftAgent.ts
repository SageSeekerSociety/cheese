// 发布页、改题页登记的可写面：芝士写这份草稿时表单怎么跟着动、面板上的改动卡怎么画、人怎么
// 撤销它改的格子。对话本身是全站那一条（`stores/agent.ts`），这里不再自己开。
import type { useTaskDraft } from '@/composables/useTaskDraft'
import type { DraftTerms } from '@/lib/taskDraftAgent'
import type { AssistantEdit } from '@/stores/agentThread'

import { computed, onScopeDispose, ref } from 'vue'

import { panelMessages, resultView, stepView, writesView } from '@/lib/assistantView'
import { partialValue } from '@/lib/partialJson'
import { AGENT_FIELDS } from '@/lib/taskDraftAgent'
import { createTypist } from '@/lib/typist'
import { agentThread, useAgentSurface } from '@/stores/agent'

type Draft = ReturnType<typeof useTaskDraft>

export interface DraftAgentOptions {
  draft: Draft
  terms: () => DraftTerms
  /** 有表单可撤销。 */
  canUndo?: () => boolean
  /** 芝士新建、发布或改了别的草稿：草稿列表要重读。 */
  draftsChanged?: () => void
  /** 芝士发布了眼前这份草稿。 */
  publishedHere?: (taskId: number) => void
  /** 一份文件拆出几道题时，这份草稿是第几道（改动卡上写「第 N 道」）。 */
  ordinal?: (draftId: number) => number | undefined
  /** 面板上「跳到这一格」。 */
  jump?: (key: string) => void
  openDraft?: (id: number) => void
  openTask?: (id: number) => void
}

export function useDraftAgent(options: DraftAgentOptions) {
  const { draft } = options
  const assistant = agentThread

  // 芝士写的参数按打字的节奏放进表单（见 lib/typist.ts）；调用结束、写进草稿、发布都排在
  // 放完之后。
  const typist = createTypist((_call, raw, held) => void draft.preview(partialValue(raw), options.terms(), held))
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
  const undoable = (e: AssistantEdit) => canUndo() && e.draft === draft.id.value

  function editById(id: number): AssistantEdit | undefined {
    return (
      assistant.edits.value.find((e) => e.id === id) ??
      assistant.messages.value.flatMap((m) => m.edits ?? []).find((e) => e.id === id)
    )
  }

  /** 和这次写入同一轮的几次写入（还在答的这一轮，或已经答完的那条）。 */
  function turnOf(id: number): AssistantEdit[] {
    if (assistant.edits.value.some((e) => e.id === id)) return assistant.edits.value
    return assistant.messages.value.find((m) => m.edits?.some((e) => e.id === id))?.edits ?? []
  }

  const touches = (edit: AssistantEdit, key: string) => (AGENT_FIELDS[key] ?? []).some((f) => f in edit.changed)

  /** 撤销一格：这一轮里芝士可能写过它几次（卡上只留最后一次），放回第一次写之前的样子，
   * 几次都记成撤销了。 */
  async function undo(changeId: number, key: string) {
    const edit = editById(changeId)
    if (!edit) return
    const same = turnOf(changeId).filter((e) => e.draft === edit.draft && touches(e, key))
    await draft.undo(same[0] ?? edit, key)
    for (const e of same.length ? same : [edit]) assistant.markUndone(e.id, key)
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
    prepare: async () => {
      await draft.flush()
      await draft.ensure()
    },
    on: {
      toolArgs: (call, name, raw) => {
        if (name !== 'cheese_task_write') return
        typist.feed(call, raw)
        aimOf(raw)
      },
      toolEnded: (_call, name, failed) => {
        if (name !== 'cheese_task_write') return
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
      messages: panelMessages(assistant.messages.value, terms(), undoable, draft.id.value ?? null, options.ordinal),
      steps: assistant.steps.value.map(stepView),
      ...writesView(assistant.edits.value, terms(), draft.id.value ?? null, undoable, options.ordinal),
      results: assistant.published.value.map(resultView),
    }),
    undo: (change, key) => void undo(change, key),
    undoAll: (rows) => void undoAll(rows),
    jump: options.jump,
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
