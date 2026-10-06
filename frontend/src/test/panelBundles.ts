// 面板 props 里那几包取数的替身。
//
// 单独挂「画」的那一半（`PanelPreviewView`、`PanelChangesView`）的用例只需要一串 props；
// 包本身的行为各自有用例盯（`PanelPreview.*.spec.ts` 挂整只、`useDocumentRevisions` 那些
// 走端到端）。这里给的是「什么都没发生」的那一包：没有清单、没有历史、编辑器没开。
import type { RoomFileEditorSession, RoomFileRevision } from '@/api'
import type { DocumentRevisionsBundle } from '@/composables/useDocumentRevisions'
import type { RoomFileEditorBundle } from '@/composables/useRoomFileEditor'
import type { RoomFileHistoryBundle } from '@/composables/useRoomFileHistory'
import type { DocumentRevision } from '@/cx_types'

import { computed, ref } from 'vue'

/** 一份修订都还没读到的修订清单。 */
export function revisionsBundle(over: Partial<DocumentRevisionsBundle> = {}): DocumentRevisionsBundle {
  return {
    revisions: ref<DocumentRevision[]>([]),
    error: ref(''),
    deciding: ref(0),
    readOnly: computed(() => false),
    load: async () => {},
    decide: async () => {},
    ...over,
  }
}

/** 一栏还空着的历史。 */
export function fileHistoryBundle(over: Partial<RoomFileHistoryBundle> = {}): RoomFileHistoryBundle {
  return {
    rows: ref<RoomFileRevision[]>([]),
    loading: ref(false),
    error: ref(''),
    busy: ref<string | null>(null),
    load: async () => {},
    restore: async () => true,
    download: async () => {},
    ...over,
  }
}

/** 没开着的编辑会话：挂载点上什么都没有。 */
export function roomFileEditorBundle(over: Partial<RoomFileEditorBundle> = {}): RoomFileEditorBundle {
  return {
    session: ref<RoomFileEditorSession | null>(null),
    failure: ref(''),
    loading: ref(false),
    unsaved: ref(false),
    changedBy: ref<RoomFileRevision | null>(null),
    savedSeq: ref<number | null>(null),
    copyName: ref(''),
    setCopyName: () => {},
    start: async () => {},
    watchVersion: async () => {},
    makeCopy: async () => {},
    begin: () => {},
    end: () => {},
    ...over,
  }
}

/** `PanelPreviewView` 要的那几包平铺的那一份：挂它的时候连同下面几样一起铺进 props。 */
export function previewBundles(
  over: {
    revs?: DocumentRevisionsBundle
    editor?: RoomFileEditorBundle
    fileHistory?: RoomFileHistoryBundle
    editing?: string | null
    showHistory?: boolean
  } = {}
) {
  return {
    revs: revisionsBundle(),
    editor: roomFileEditorBundle(),
    fileHistory: fileHistoryBundle(),
    editing: null as string | null,
    showHistory: false,
    ...over,
  }
}
