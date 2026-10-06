// 「这一份 .docx 里的修订」：读清单、逐条接受或拒绝、处理完之后通知谁。
//
// 和画的那一半（`components/panels/preview/RevisionList.vue`）分家的理由和别处一样：
// 那一只原先自己 import 两个接口函数，于是改动和预览两格都跟着它够得着接口层——两格
// 都是场景，场景只吃 props 和事件。清单本身是同一份（一处修订算一条这件事只能有一个
// 答案），所以这份取数被两格共用：改动那一格看的是工作树里的文件，预览那一格看的是
// 房间里摆出来的那一份。
//
// 哪些参数、什么时候重读、只读怎么判，全在这里；画的那一半只认 props。
import type { DocumentRevision, FileSource } from '../cx_types'

import { computed, ref, watch } from 'vue'

import { decideDocumentRevisions, documentRevisions } from '../api'
import { isLibraryPath } from '../lib/library'

import { t } from '@/i18n'

export interface DocumentRevisionsScope {
  topicId: () => string | null
  /** 要读哪一份；null 就是这份文件没有修订可读（不是 docx，或者还没打开）。 */
  path: () => string | null
  /** 这个文件现在是哪一版，用来判断要不要重读清单。 */
  version: () => string | null
  /** 从哪个库读：某个任务的工作树，还是房间自己的文件（null）。 */
  task: () => string | null
  source: () => FileSource
  /** 调用方自己那点「不许改」的理由（比如这一格现在是只读、或者这条活已经不在跑了）。 */
  readOnly?: () => boolean
}

export interface DocumentRevisionsHooks {
  /** 处理完一条：文件变了，宿主该把那一页重画一遍。 */
  onDecided?: () => void
}

export function useDocumentRevisions(scope: DocumentRevisionsScope, hooks: DocumentRevisionsHooks = {}) {
  const revisions = ref<DocumentRevision[]>([])
  const error = ref('')
  const deciding = ref(0)
  let listedKey = ''
  // 读这份清单时文件是哪一版：处理时带回去，芝士在这中间重新交付过就不会被盖掉。
  let listedVersion = ''

  // 资料库里的那一份是用户给进来的原件，只读——修订照样列出来（它们是这份文档的一部
  // 分，读者有权看见），但处理不了：接受一处修订会改写所有房间都在引用的那一份。
  const readOnly = computed(
    () => scope.readOnly?.() === true || scope.source() === 'committed' || isLibraryPath(scope.path() ?? '')
  )

  async function load() {
    const tid = scope.topicId()
    const path = scope.path()
    if (!tid || !path) {
      revisions.value = []
      listedKey = ''
      return
    }
    const key = `${tid}:${scope.task() ?? ''}:${scope.source()}:${path}:${scope.version() ?? ''}`
    if (key === listedKey) return
    listedKey = key
    revisions.value = []
    error.value = ''
    try {
      const read = await documentRevisions(tid, path, scope.task(), scope.source())
      if (listedKey !== key) return
      revisions.value = read.revisions
      listedVersion = read.version
    } catch (e) {
      if (listedKey !== key) return
      // 读不到修订不该把文档也弄没：文档本身还好好地显示着。
      revisions.value = []
      listedVersion = ''
      error.value = e instanceof Error ? e.message : t('work.room.revisions.loadFailed')
    }
  }

  async function decide(decision: { accept?: number[]; reject?: number[] }) {
    const tid = scope.topicId()
    const path = scope.path()
    if (!tid || !path || readOnly.value) return
    deciding.value += 1
    error.value = ''
    try {
      const done = await decideDocumentRevisions(tid, path, listedVersion, decision, scope.task())
      revisions.value = done.revisions
      listedVersion = done.version
      // 文件改了，重新数的序号也变了：清单和那一页都要刷新，别让读者对着旧清单点第二下。
      listedKey = ''
      hooks.onDecided?.()
      await load()
    } catch (e) {
      const said = e instanceof Error ? e.message : t('work.room.revisions.decideFailed')
      // 写不进去多半是文件已经变了：先把清单换成现在这份，再说刚才那下没生效。
      listedKey = ''
      await load()
      error.value = said
    } finally {
      deciding.value -= 1
    }
  }

  watch([scope.topicId, scope.path, scope.version, scope.task, scope.source], () => void load(), { immediate: true })

  return { revisions, error, deciding, readOnly, load, decide }
}

/** 「修订清单」这一份取数原样递给面板（props）：面板自己不认识接口。 */
export type DocumentRevisionsBundle = ReturnType<typeof useDocumentRevisions>
