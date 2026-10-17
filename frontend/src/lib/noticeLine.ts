// What a platform line in the room says, as one line of plain text: the words
// RoomNotice puts on screen for it, without the buttons and the fold-out detail.
//
// The timeline names each of these rows with it. A row off screen is not
// rendered (content-visibility, see room-row.css), and Chrome names a row
// labelled by its own contents as nothing while they are skipped, so the name
// has to come from the notice rather than from what the row draws.
import type { Block } from '../cx_types'
import type { PlatformNotice } from './platformNotice'
import type { RefNames } from './refChip'

import { docLineCount, docLineInTurn, docLineOf, docLineSentence } from './docEditLine'
import { repeatsLine } from './noticeRepeats'
import { noticeText } from './noticeText'
import { plainRefs } from './refChip'

import { t } from '@/i18n'

/** `actor` is who the row is signed by, the way RoomNotice gets it (`agent`); null when unsigned. */
export function noticeLine(
  block: Block,
  notice: PlatformNotice,
  actor: string | null,
  refs: RefNames,
  max = 140
): string {
  const parts: (string | undefined)[] = []
  switch (notice.mode) {
    case 'repeats':
      parts.push(repeatsLine(notice.rows, actor ?? notice.actor))
      break
    case 'incident':
      parts.push(notice.incident.title, notice.incident.status, notice.lead)
      break
    case 'turn-summary':
      if (notice.changes)
        parts.push(
          `${t('work.room.notice.filesChanged', { n: notice.changes.filesTotal })} +${notice.changes.added} −${notice.changes.removed}`
        )
      parts.push(...notice.actions.map((act) => act.text))
      break
    case 'action': {
      // 改文档那一行读到的和屏幕上一样：在一轮里的不再报名字（行名里已经有了）。
      const doc = docLineOf(block, notice, (handle) => refs.mentionNames[handle] || handle)
      if (doc) parts.push(docLineSentence(doc, actor !== null && docLineInTurn(block)), docLineCount(doc))
      else parts.push(notice.text, notice.libraryDoc?.title)
      break
    }
    case 'fold':
      parts.push(notice.line, notice.quote ? t('work.room.notice.quote', { text: notice.quote }) : undefined)
      break
    default:
      parts.push(noticeText(block))
  }
  const text = plainRefs(parts.filter(Boolean).join(' · '), refs).replace(/\s+/g, ' ').trim()
  return text.length > max ? text.slice(0, max) + '…' : text
}
