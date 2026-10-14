/**
 * AI 队友改了一份文档留下的那一行（`DocEditLine`）在预览站里的条目，和它用的那条
 * 示例记录（`catalog.ts` 里 RoomNotice 的那一格也用它）。单独一份是因为
 * `catalog.ts` 已经顶到一千行的上限。
 */
import type { Block } from '@/cx_types'
import type { DocLine } from '@/lib/docEditLine'
import type { CatalogEntry } from './catalog'

import { ROOM_REFS } from './catalogFixtures'

import DocEditLine from '@/components/room/DocEditLine.vue'
import { docLineOf } from '@/lib/docEditLine'
import { platformNotice } from '@/lib/platformNotice'

/** AI 队友在它那一轮里改了资料库里的一份文档：对话里那一行是这一轮里的一步。 */
export const LIBRARY_DOC_EDITED: Block = {
  id: 'catalog-library-doc-edited',
  conversation_id: 'demo',
  kind: 'event',
  author_type: 'platform',
  author: 'cheese',
  content: '<@cheese> 改了文档《第一周课件》',
  turn_id: 'catalog-turn',
  created_at: new Date('2026-09-28T14:32:00+08:00').toISOString(),
  meta: {
    platform: true,
    action: 'doc',
    document: { id: 'doc-week-1', title: '第一周课件' },
    doc_created: false,
    doc_edits: [
      { old: '周三交作业', new: '周五交作业' },
      { old: '共 3 道题', new: '共 4 道题' },
      { old: '课程邮箱', new: '课程平台' },
    ],
  },
}

/** 同一行，不属于哪一轮（更早留下的、或者人改的）。 */
const LIBRARY_DOC_ALONE: Block = { ...LIBRARY_DOC_EDITED, id: 'catalog-library-doc-alone', turn_id: null }

/** 那一行要画的东西，名字按房间里的叫法。 */
function docLine(block: Block): DocLine {
  return docLineOf(block, platformNotice(block), (handle) => ROOM_REFS.mentionNames[handle] || handle)!
}

export const DOC_LINE_ENTRIES: CatalogEntry[] = [
  {
    id: 'doc-edit-line',
    title: 'DocEditLine',
    about: 'AI 队友改了一份文档留下的那一行：在它那一轮里不再写名字，不在哪一轮里时名字是普通文字。',
    file: 'src/components/room/DocEditLine.vue',
    component: DocEditLine,
    needs: ['vuetify'],
    states: [
      {
        name: '在一轮里',
        note: '前面一枚文档记号；点这一行打开文档，「查看改动」标出改过的几处。',
        props: { block: LIBRARY_DOC_EDITED, line: docLine(LIBRARY_DOC_EDITED), inTurn: true },
        expect: '3 处',
      },
      {
        name: '不在哪一轮里',
        note: '谁做的、谁让做的都写成普通的名字。',
        props: { block: LIBRARY_DOC_ALONE, line: docLine(LIBRARY_DOC_ALONE), inTurn: false },
        expect: '改了《第一周课件》',
      },
    ],
  },
]
