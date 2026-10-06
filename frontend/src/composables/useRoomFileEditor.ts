// 「在房间里直接改一份 Word、表格或幻灯片」：开一次编辑会话、把编辑器挂起来、盯着文件
// 有没有被别人改过、另存一份到房间里。
//
// 和画的那一半（`components/panels/preview/RoomFileEditor.vue`）分家的理由和别处一样：
// 那一只原先自己 import 三个接口函数，于是「预览」那一格跟着它够得着接口层——它在场景
// 里，场景只吃 props 和事件。
//
// 编辑器本体（OnlyOffice 的脚本、实例、它报回来的「有没有没存的东西」）也留在这一层：
// 展示组件那边只剩一条横条、几块提示、一个挂载点和一个历史栏。挂载点是它渲染出来的那
// 个 div，`begin()` 由它在挂上之后叫——实例必须落在已经在 DOM 里的元素上。
import type { RoomFileEditorSession, RoomFileRevision } from '../api'

import { nextTick, ref } from 'vue'

import { copyIntoRoom, openRoomFileEditor, roomFileRevisions } from '../api'

import { t } from '@/i18n'

type DocEditor = { destroyEditor: () => void }
type DocsApi = { DocEditor: new (id: string, config: Record<string, unknown>) => DocEditor }

export interface RoomFileEditorScope {
  topicId: () => string | null
  /** 正在改哪一份；null 就是没有开着的编辑会话。 */
  path: () => string | null
}

export interface RoomFileEditorHooks {
  /** 换成另一份文件在编辑了（「另存一份」之后）：宿主要把它开成一个页签。 */
  onOpened?: (path: string) => void
}

export function useRoomFileEditor(scope: RoomFileEditorScope, hooks: RoomFileEditorHooks = {}) {
  const session = ref<RoomFileEditorSession | null>(null)
  const failure = ref('')
  const loading = ref(true)
  const unsaved = ref(false)
  const changedBy = ref<RoomFileRevision | null>(null)
  const savedSeq = ref<number | null>(null)
  const copyName = ref('')

  let instance: DocEditor | null = null
  let loadedVersion = ''
  let editorKey = ''
  let timer: ReturnType<typeof setInterval> | null = null
  // 编辑器要挂上去的那个元素：展示组件挂载之后把自己的挂载点交给 `begin`。实例必须落在
  // 已经在 DOM 里的元素上，而那个 div 是展示组件画出来的，所以 id 从它那里来。
  let mountId = ''

  // 同一个地址的脚本只加载一次：编辑器开开关关，不需要每次都从网上再拿一遍。
  const scripts = new Map<string, Promise<void>>()

  function loadScript(src: string): Promise<void> {
    const existing = scripts.get(src)
    if (existing) return existing
    const made = new Promise<void>((resolve, reject) => {
      const el = document.createElement('script')
      el.src = src
      el.async = true
      el.onload = () => resolve()
      el.onerror = () => {
        scripts.delete(src)
        reject(new Error(t('work.room.fileEditor.serviceUnreachable')))
      }
      document.head.appendChild(el)
    })
    scripts.set(src, made)
    return made
  }

  /** 开一次会话，并把编辑器挂起来。已经在挂的那个先拆掉。 */
  async function start(): Promise<void> {
    const tid = scope.topicId()
    const path = scope.path()
    loading.value = true
    failure.value = ''
    changedBy.value = null
    instance?.destroyEditor()
    instance = null
    if (!tid || !path) {
      session.value = null
      loading.value = false
      return
    }
    try {
      const got = await openRoomFileEditor(tid, path)
      session.value = got
      if (!got.enabled || !got.config || !got.api_url) return
      await loadScript(got.api_url)
      const api = (window as unknown as { DocsAPI?: DocsApi }).DocsAPI
      if (!api) throw new Error(t('work.room.fileEditor.notLoaded'))
      loadedVersion = got.version ?? ''
      editorKey = String((got.config.document as { key?: string })?.key ?? '')
      await nextTick()
      instance = new api.DocEditor(mountId, {
        ...got.config,
        width: '100%',
        height: '100%',
        events: {
          onDocumentStateChange: (e: { data: boolean }) => {
            unsaved.value = e.data
          },
          onError: (e: { data?: { errorDescription?: string } }) => {
            failure.value = e?.data?.errorDescription || t('work.room.fileEditor.editorError')
          },
        },
      })
    } catch (e) {
      failure.value = e instanceof Error ? e.message : t('work.room.fileEditor.openFailed')
    } finally {
      loading.value = false
    }
  }

  /** 文件变了没有、是谁改的。自己这次编辑存下的不算「被别人改了」。 */
  async function watchVersion(): Promise<void> {
    const tid = scope.topicId()
    const path = scope.path()
    if (!tid || !path || !session.value?.enabled) return
    try {
      const got = await roomFileRevisions(tid, path)
      const top = got.data[0]
      if (!top || !got.version || got.version === loadedVersion) return
      if (top.editor_key && top.editor_key === editorKey) {
        loadedVersion = got.version
        savedSeq.value = top.seq
        return
      }
      changedBy.value = top
    } catch {
      // 下一轮再看；读不到历史不妨碍继续编辑。
    }
  }

  async function makeCopy(): Promise<void> {
    const tid = scope.topicId()
    const path = scope.path()
    if (!tid || !path) return
    const leaf = path.split('/').pop() ?? 'file'
    const target = copyName.value.trim() || `${t('work.room.outputs.defaultFolder')}/${leaf}`
    try {
      const made = await copyIntoRoom(tid, path, target)
      hooks.onOpened?.(made.path)
    } catch (e) {
      failure.value = e instanceof Error ? e.message : t('work.room.fileEditor.copyFailed')
    }
  }

  /** 展示组件挂上了（`el` 是它渲染出来的那个挂载点的 id）：把编辑器立起来，并开始盯着
   *  文件有没有被改。 */
  function begin(el: string): void {
    mountId = el
    const path = scope.path()
    if (path) copyName.value = `${t('work.room.outputs.defaultFolder')}/${path.split('/').pop() ?? 'file'}`
    void start()
    if (timer) clearInterval(timer)
    timer = setInterval(() => void watchVersion(), 8000)
  }

  /** 展示组件要卸了：实例和历史轮询一起收掉。 */
  function end(): void {
    if (timer) clearInterval(timer)
    timer = null
    instance?.destroyEditor()
    instance = null
  }

  /** 另存一份时那个名字是读者敲的：写回这一层的状态，展示组件不碰别人的 ref。 */
  function setCopyName(v: string): void {
    copyName.value = v
  }

  return {
    session,
    failure,
    loading,
    unsaved,
    changedBy,
    savedSeq,
    copyName,
    setCopyName,
    start,
    watchVersion,
    makeCopy,
    begin,
    end,
  }
}

/** 「房间文件编辑器」这一份取数原样递给面板（props）：面板自己不认识接口。 */
export type RoomFileEditorBundle = ReturnType<typeof useRoomFileEditor>
