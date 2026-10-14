// The line a change to a document leaves in the conversation.
//
// An AI teammate edits a document by calling a tool, and the line that records
// it is that tool call, not something it said. Inside the teammate's own turn
// it is drawn the way a tool call is drawn inside an assistant turn: one
// compact line under the turn's messages, without a name of its own — the
// turn already says whose it is. Outside a turn (a person's edit, a run that
// mixes several editors, a line written before lines carried their turn) it is
// one muted line that names who did it in plain text. Neither form draws a name
// as an @ chip: the line notifies nobody, it only says who.
//
// Only lines the backend writes with structure today take this form: a
// library document's (`meta.document`) or a living document's that lists its
// editors (`meta.doc_actors`). Older lines keep the free sentence they were
// stored with (RoomNotice's action row).
import type { Block } from '../cx_types'
import type { DocEdit } from './docEdits'
import type { LibraryDocCard, PlatformNotice } from './platformNotice'

import { t } from '@/i18n'

function meta(block: Block): Record<string, unknown> {
  return (block.meta as Record<string, unknown> | null) ?? {}
}

/** The handles a living document's line names as its editors (`doc_actors`), or null when it names none. */
function editors(block: Block): string[] | null {
  const raw = meta(block).doc_actors
  return Array.isArray(raw) ? raw.filter((h): h is string => typeof h === 'string') : null
}

/** Whether this event is a document line with the structure this module draws. */
export function isDocLine(block: Block): boolean {
  if (block.kind !== 'event') return false
  const m = meta(block)
  if (m.action !== 'doc') return false
  const doc = m.document as { id?: unknown } | undefined
  return (!!doc && typeof doc.id === 'string') || editors(block) !== null
}

/**
 * Whether the line records a tool call in its editor's own turn: it carries the
 * turn, and nobody but the author edited in the run it covers.
 */
export function docLineInTurn(block: Block): boolean {
  if (!isDocLine(block) || !block.turn_id || block.author === 'system') return false
  const who = editors(block)
  return who === null || who.every((handle) => handle === block.author)
}

/** What the line says and what its two actions lead to. */
export interface DocLine {
  /** The thing done, without who did it: `改了《方案》`. */
  action: string
  /** Who asked for it, by name; null when nobody did. */
  requester: string | null
  /** Who did it, by name, for a line outside a turn. */
  actors: string[]
  /** How many passages were changed; null when the line does not count them. */
  count: number | null
  /** The library document the line is about; null for the conversation's own document. */
  library: LibraryDocCard | null
  /** The passages 「查看改动」 marks; empty when there is nothing to mark. */
  edits: DocEdit[]
  /** The requester's handle, for a change made at someone's request. */
  requestedBy: string | null
  /** Changes proposed rather than made: the line opens the document where they wait. */
  suggested: boolean
}

/** Joined the way the reader's language joins a list (`李老师、芝士S`). */
function joined(names: string[]): string {
  return names.join(t('work.room.notice.docLine.sep'))
}

/**
 * The document line `notice` draws for `block`, with the names `nameOf` gives
 * each handle; null for anything that is not one.
 */
export function docLineOf(
  block: Block,
  notice: PlatformNotice | null,
  nameOf: (handle: string) => string
): DocLine | null {
  if (!notice || notice.mode !== 'action' || notice.resource !== 'doc' || !isDocLine(block)) return null
  const library = notice.libraryDoc ?? null
  const actors = (editors(block) ?? [block.author]).filter((h) => h !== 'system').map(nameOf)
  if (library) {
    const title = library.title || t('work.room.doc.untitled')
    const edits = library.created || library.suggestions ? [] : library.edits
    return {
      action: library.created
        ? t('work.room.notice.docLine.created', { title })
        : library.suggestions
          ? t('work.room.notice.docLine.suggested', { title, n: library.suggestions })
          : t('work.room.notice.docLine.edited', { title }),
      requester: null,
      actors,
      count: edits.length || null,
      library,
      edits,
      requestedBy: null,
      suggested: library.suggestions > 0,
    }
  }
  const suggestions = notice.docSuggestions ?? 0
  const request = notice.docRequest ?? null
  return {
    action: suggestions
      ? t('work.room.notice.docLine.suggestedDoc', { n: suggestions })
      : t('work.room.notice.docLine.editedDoc'),
    requester: request ? nameOf(request.requestedBy) : null,
    actors,
    count: request?.edits.length || null,
    library: null,
    edits: request?.edits ?? [],
    requestedBy: request?.requestedBy ?? null,
    suggested: suggestions > 0,
  }
}

/** What the line says before its count (`inTurn`: without who did it, the turn says that). */
export function docLineSentence(line: DocLine, inTurn: boolean): string {
  if (inTurn) {
    return line.requester
      ? t('work.room.notice.docLine.forInTurn', { requester: line.requester, action: line.action })
      : line.action
  }
  if (line.requester) {
    return t('work.room.notice.docLine.forBy', {
      requester: line.requester,
      agent: joined(line.actors),
      action: line.action,
    })
  }
  return line.actors.length
    ? t('work.room.notice.docLine.by', { actor: joined(line.actors), action: line.action })
    : line.action
}

/** 「3 处」, or '' when the line does not count its changes. */
export function docLineCount(line: DocLine): string {
  return line.count ? t('work.room.notice.docLine.count', { n: line.count }, line.count) : ''
}
