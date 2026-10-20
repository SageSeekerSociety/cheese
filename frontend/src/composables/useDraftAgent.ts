// 发布页、改题页上的芝士：对话挂在哪、它写草稿时表单怎么跟着动、面板上画什么、人怎么
// 撤销它改的格子、怎么把文件交给它。两页只在「对话挂在哪」上不一样：发布页的对话挂在
// 草稿上，改题页的挂在那道题上（和题目页「问芝士」是同一段）。
import type { Ref } from 'vue'
import type { PanelFile } from '@/components/assistant/AssistantPanel.vue'
import type { AssistantEdit, AssistantHome } from '@/composables/useAssistant'
import type { useTaskDraft } from '@/composables/useTaskDraft'
import type { DraftTerms } from '@/lib/taskDraftAgent'

import { computed, onScopeDispose, ref } from 'vue'
import { toast } from 'vuetify-sonner'

import { useAssistant } from '@/composables/useAssistant'

import { AttachmentsApi } from '@/api/attachments'
import { t } from '@/i18n'
import { panelMessages, resultView, stepView, writesView } from '@/lib/assistantView'
import { partialValue } from '@/lib/partialJson'
import { AGENT_FIELDS } from '@/lib/taskDraftAgent'
import { createTypist } from '@/lib/typist'

type Draft = ReturnType<typeof useTaskDraft>

export interface DraftAgentOptions {
  draft: Draft
  terms: () => DraftTerms
  /** 对话挂在哪：发布页是草稿，改题页是题目。 */
  home: () => AssistantHome | null
  /** 有表单可撤销。 */
  canUndo?: () => boolean
  /** 芝士新建、发布或改了别的草稿：草稿列表要重读。 */
  draftsChanged?: () => void
  /** 芝士发布了眼前这份草稿。 */
  publishedHere?: (taskId: number) => void
  /** 一份文件拆出几道题时，这份草稿是第几道（改动卡上写「第 N 道」）。 */
  ordinal?: (draftId: number) => number | undefined
}

interface ShownFile {
  id: number | null
  name: string
  mime: string
}

export function useDraftAgent(options: DraftAgentOptions) {
  const { draft } = options

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

  const assistant = useAssistant({
    home: options.home,
    page: () => (draft.id.value ? { kind: 'draft', id: draft.id.value } : null),
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
      conversation: (id) => {
        if (draft.draft.value) draft.draft.value = { ...draft.draft.value, conversationId: id }
      },
    },
  })

  /** 交给芝士的文件：先传上去，下一个问题带上它们。 */
  const files = ref<(ShownFile & { uploading: boolean })[]>([])

  async function attach(picked: File[]) {
    for (const file of picked) {
      const row = ref({ id: null as number | null, name: file.name, mime: file.type, uploading: true })
      files.value = [...files.value, row.value]
      try {
        const done = await AttachmentsApi.upload({ type: 'file', file })
        files.value = files.value.map((f) => (f === row.value ? { ...f, id: done.id, uploading: false } : f))
      } catch {
        files.value = files.value.filter((f) => f !== row.value)
        toast.error(t('tasks.assistant.files.failed'))
      }
    }
  }

  function removeFile(index: number) {
    files.value = files.value.filter((_, i) => i !== index)
  }

  async function ask(text: string) {
    const handed = files.value.filter((f) => f.id !== null).map((f) => ({ id: f.id as number, name: f.name }))
    files.value = []
    await assistant.ask(text, t('tasks.assistant.failed'), handed)
  }

  const canUndo = () => options.canUndo?.() ?? true
  const terms = () => options.terms()

  const undoable = (e: AssistantEdit) => canUndo() && e.draft === draft.id.value

  const panel = computed(() => ({
    messages: panelMessages(assistant.messages.value, terms(), undoable, draft.id.value ?? null, options.ordinal),
    steps: assistant.steps.value.map(stepView),
    ...writesView(assistant.edits.value, terms(), draft.id.value ?? null, undoable, options.ordinal),
    results: assistant.published.value.map(resultView),
    files: files.value.map((f): PanelFile => ({ name: f.name, mime: f.mime, uploading: f.uploading })),
  }))

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

  return {
    assistant,
    panel,
    files: files as Ref<(ShownFile & { uploading: boolean })[]>,
    writingTarget,
    attach,
    removeFile,
    ask,
    undo,
    undoAll,
    undoMark,
  }
}
