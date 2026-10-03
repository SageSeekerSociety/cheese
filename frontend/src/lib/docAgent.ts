// Asking the room's AI teammate from the document: the shortcuts the box offers,
// and what one request and its answer look like.
//
// A shortcut is sent by its id; what it asks for is written out by the service.
// Its label here is only its name. Which ones the box offers depends on where it
// was opened: on a selection that may be changed, on one that may only be asked
// about, or on the whole document.
import type { DocEdit } from './docEdits'

import { renderNoticeMessage } from './noticeText'

export type AgentScope = 'selection' | 'document'

export type AgentPresetId =
  | 'polish'
  | 'shorten'
  | 'list'
  | 'table'
  | 'translate'
  | 'check'
  | 'explain'
  | 'summarize'
  | 'check_all'
  | 'structure'

export interface AgentPreset {
  id: AgentPresetId
  /** Changes the selection, or only answers. */
  kind: 'edit' | 'ask'
  /** The i18n key of its name. */
  label: string
  icon: string
}

const preset = (id: AgentPresetId, kind: AgentPreset['kind'], label: string, icon: string): AgentPreset => ({
  id,
  kind,
  label,
  icon,
})

export interface PresetGroups {
  edit: AgentPreset[]
  ask: AgentPreset[]
}

export interface PresetContext {
  /** The selection may be changed. */
  editable: boolean
  /** The selection is a list: turning it into a list becomes turning it into a table. */
  list: boolean
  /** The selection is written in Chinese: it translates into English, else into Chinese. */
  chinese: boolean
  /** The selection is code, a diagram's source among it: the shortcuts are for prose. */
  code?: boolean
}

/** The shortcuts the box offers, by group. */
export function presetsFor(scope: AgentScope, context: PresetContext): PresetGroups {
  if (scope === 'document') {
    return {
      edit: [],
      ask: [
        preset('summarize', 'ask', 'work.room.docAgent.preset.summarize', 'mdi-text-short'),
        preset('check_all', 'ask', 'work.room.docAgent.preset.checkAll', 'mdi-magnify'),
        preset('structure', 'ask', 'work.room.docAgent.preset.structure', 'mdi-file-tree-outline'),
      ],
    }
  }
  if (context.code) return { edit: [], ask: [] }
  const translate = preset(
    'translate',
    'edit',
    context.chinese ? 'work.room.docAgent.preset.toEnglish' : 'work.room.docAgent.preset.toChinese',
    'mdi-translate'
  )
  if (!context.editable) {
    return {
      edit: [],
      ask: [
        preset('explain', 'ask', 'work.room.docAgent.preset.explain', 'mdi-help-circle-outline'),
        preset('check', 'ask', 'work.room.docAgent.preset.check', 'mdi-magnify'),
        { ...translate, kind: 'ask' },
      ],
    }
  }
  return {
    edit: [
      preset('polish', 'edit', 'work.room.docAgent.preset.polish', 'mdi-pencil-outline'),
      preset('shorten', 'edit', 'work.room.docAgent.preset.shorten', 'mdi-arrow-collapse-horizontal'),
      context.list
        ? preset('table', 'edit', 'work.room.docAgent.preset.table', 'mdi-table')
        : preset('list', 'edit', 'work.room.docAgent.preset.list', 'mdi-format-list-bulleted'),
      translate,
    ],
    ask: [preset('check', 'ask', 'work.room.docAgent.preset.check', 'mdi-magnify')],
  }
}

/** The shortcuts whose name holds `query`, in order. */
export function matching(groups: PresetGroups, query: string, name: (p: AgentPreset) => string): PresetGroups {
  const q = query.trim().toLowerCase()
  if (!q) return groups
  const keep = (list: AgentPreset[]) => list.filter((p) => name(p).toLowerCase().includes(q))
  return { edit: keep(groups.edit), ask: keep(groups.ask) }
}

const CJK = /[㐀-鿿]/

export function isChinese(text: string): boolean {
  return CJK.test(text)
}

/** A selection as the service reads it: the Markdown of its blocks, and where in it the selected text is. */
export interface AgentSelection {
  block: string
  start: number
  end: number
}

export interface DocAgentRequest {
  /** The box's conversation, to go on with it. */
  conversation?: string
  preset?: AgentPresetId
  text?: string
  selection?: AgentSelection
}

/** What the box hears while it is answered. */
export interface DocAgentListener {
  conversation: (id: string) => void
  queued: () => void
  working: () => void
  delta: (text: string) => void
  done: (result: { answer: string; edits: DocEdit[]; stopped: boolean }) => void
  error: (message: string) => void
}

/** Hand one server-sent event of an answer to `listener`. */
export function dispatch(listener: DocAgentListener, event: string, data: Record<string, unknown>): void {
  const text = (key: string) => (typeof data[key] === 'string' ? (data[key] as string) : '')
  if (event === 'conversation') listener.conversation(text('id'))
  else if (event === 'queued') listener.queued()
  else if (event === 'working') listener.working()
  else if (event === 'delta') listener.delta(text('text'))
  else if (event === 'done')
    listener.done({
      answer: text('answer'),
      edits: Array.isArray(data.edits) ? (data.edits as DocEdit[]) : [],
      stopped: data.stopped === true,
    })
  else if (event === 'error') listener.error(renderNoticeMessage(data.i18n, text('message')))
}
