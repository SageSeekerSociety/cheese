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
import { changeView, panelMessages, resultView, stepView } from '@/lib/assistantView'
import { partialValue } from '@/lib/partialJson'
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
  const typist = createTypist((_call, raw) => void draft.preview(partialValue(raw), options.terms()))
  let after: Promise<void> = Promise.resolve()
  function then(step: () => void) {
    after = after
      .then(() => typist.idle())
      .then(step)
      .catch(() => undefined)
  }
  onScopeDispose(() => typist.stop())

  const assistant = useAssistant({
    home: options.home,
    page: () => (draft.id.value ? { kind: 'draft', id: draft.id.value } : null),
    prepare: async () => {
      await draft.flush()
      await draft.ensure()
    },
    on: {
      toolArgs: (call, name, raw) => {
        if (name === 'cheese_task_write') typist.feed(call, raw)
      },
      toolEnded: (_call, name, failed) => {
        if (name === 'cheese_task_write') then(() => void draft.ended(failed))
      },
      edit: (edit) =>
        then(() => {
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

  const panel = computed(() => ({
    messages: panelMessages(assistant.messages.value, terms(), (e) => canUndo() && e.draft === draft.id.value),
    steps: assistant.steps.value.map(stepView),
    changes: assistant.edits.value.map((e) => changeView(e, terms(), canUndo() && e.draft === draft.id.value)),
    results: assistant.published.value.map(resultView),
    files: files.value.map((f): PanelFile => ({ name: f.name, mime: f.mime, uploading: f.uploading })),
  }))

  function editById(id: number): AssistantEdit | undefined {
    return (
      assistant.edits.value.find((e) => e.id === id) ??
      assistant.messages.value.flatMap((m) => m.edits ?? []).find((e) => e.id === id)
    )
  }

  async function undo(changeId: number, key: string) {
    const edit = editById(changeId)
    if (!edit) return
    await draft.undo(edit, key)
    assistant.markUndone(changeId, key)
  }

  async function undoAll(changeId: number) {
    const edit = editById(changeId)
    if (!edit) return
    for (const row of changeView(edit, terms(), true).rows) if (!row.undone && !row.kept) await undo(changeId, row.key)
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
    attach,
    removeFile,
    ask,
    undo,
    undoAll,
    undoMark,
  }
}
