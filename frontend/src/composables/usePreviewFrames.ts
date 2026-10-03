import type { PreviewSession } from '../api'

import { computed, nextTick, onBeforeUnmount, ref } from 'vue'

import { frameKeys, runFrameKey } from '../commands/shortcuts'
import { t } from '../i18n'
import { postPreviewSession } from '../lib/previewSession'

export interface PreviewFrame {
  id: number
  name: string
  url: string
  label: string
  mime: string
  version: string | null
  live: boolean
  posted: boolean
  identity?: string
  resourceId?: string
  instance?: string
  runtime?: 'unconfirmed' | 'ready' | 'failed'
  connection?: 'online' | 'disconnected' | 'gone'
  runtimeError?: string
  /** 这一份资源允许等多久，见 `navigationBudget()`。不填＝默认那档。 */
  budgetMs?: number
}

export type PreviewNavigation = 'idle' | 'authorizing' | 'navigating' | 'loaded' | 'failed'

/** 连接状态的一次转折，报给取数那一层：实例被换掉（要跟到新实例），或同一实例断线
 *  后又回来（这段断线里的请求都失败了，要重载一次）。状态没变就是 null。 */
export type ConnectionChange = 'instance-changed' | 'instance-recovered' | null

/** 一整页的默认预算。 */
const FILE_NAVIGATION_BUDGET_MS = 30_000

/** 应用要过隧道，还可能赶上机器冷启动（座位那次实测 2 分 17 秒），默认那 30 秒会把它
 *  误报成「加载超时」。130 秒是 CC 自己的默认档（`13e4`）。 */
export const APP_NAVIGATION_BUDGET_MS = 130_000

/** 硬顶和余量同样照 CC：正数先被夹进 10 分钟，再留 2 秒给最后那一程。 */
const MAX_NAVIGATION_BUDGET_MS = 600_000
const NAVIGATION_BUDGET_SLACK_MS = 2_000

/** 这一份资源是哪个档，单位毫秒：调用方给的正数被夹进 10 分钟硬顶；没给、或者给的
 *  是 0、负数、`NaN`、`Infinity`，退回文件档——这些值不是「立刻失败」，是「没填」。
 *
 *  文案说的是这个数：那 2 秒余量是我们的，不是这一份资源的。 */
export function navigationTier(requested?: number): number {
  return typeof requested === 'number' && Number.isFinite(requested) && requested > 0
    ? Math.min(requested, MAX_NAVIGATION_BUDGET_MS)
    : FILE_NAVIGATION_BUDGET_MS
}

/** 一次导航真正等多久，单位毫秒——档位，再加上最后那 2 秒余量（照 CC 的消费式）。
 *  多出来的这 2 秒是给「就快好了」那一程和 500 毫秒的轮询粒度留的，不进文案。 */
export function navigationBudget(requested?: number): number {
  return typeof requested === 'number' && Number.isFinite(requested) && requested > 0
    ? navigationTier(requested) + NAVIGATION_BUDGET_SLACK_MS
    : navigationTier(requested)
}

export interface PreviewFrameOptions {
  /**
   * 帧里的 ESC 交回宿主时叫一次。怎么处理由画的那一半决定（关标注条、退全屏、把
   * 焦点收回面板），这一层只管把这件事报上来。
   */
  onEscape?: () => void
}

/** Incoming navigation never destroys the last observed loaded browsing context. */
export function usePreviewFrames(frameName: string, options: PreviewFrameOptions = {}) {
  const displayed = ref<PreviewFrame | null>(null)
  const incoming = ref<PreviewFrame | null>(null)
  const navigation = ref<PreviewNavigation>('idle')
  const error = ref('')
  const failedIdentity = ref<string | null>(null)
  const frames = computed(() => [displayed.value, incoming.value].filter((frame): frame is PreviewFrame => !!frame))
  let attemptIdentity: string | null = null
  let serial = 0
  let timer: ReturnType<typeof setInterval> | null = null
  let elapsed = 0
  let lastTick = 0
  let visible = false
  let runtimeWindow: Window | null = null
  let runtimeSession = ''

  function sendRuntimeHello(frame: PreviewFrame) {
    try {
      runtimeWindow?.postMessage(
        {
          channel: 'cheese-preview-runtime',
          version: 1,
          type: 'hello',
          sessionId: runtimeSession,
          resourceId: frame.resourceId ?? null,
          // 焦点在帧里时宿主的键盘监听收不到按键，所以键表随握手一起过去：
          // 帧只回 id，跑哪条命令由这边决定。表是活的，每送一次都重取。
          keys: frameKeys(),
        },
        new URL(frame.url).origin
      )
    } catch {
      // A replaced or opaque document cannot acknowledge; remain unconfirmed.
      runtimeWindow = null
    }
  }

  function runtimeMessage(event: MessageEvent) {
    const frame = displayed.value
    if (!frame || !runtimeWindow || event.source !== runtimeWindow || event.origin !== new URL(frame.url).origin) return
    const data = event.data
    if (!data || data.channel !== 'cheese-preview-runtime' || data.version !== 1) return
    if (data.type === 'hello-request') {
      // An app may install its bridge after iframe load's first hello was sent.
      sendRuntimeHello(frame)
      return
    }
    if (data.sessionId !== runtimeSession) return
    if (data.type === 'key' && typeof data.id === 'string') {
      // 帧只回 id。认不认这条 id 由 `runFrameKey` 拿刚发下去那张表来判。
      runFrameKey(data.id)
    } else if (data.type === 'escape') {
      // 帧里按了 ESC：把控制权要回宿主。不改变就绪状态——注入的页面从不报 ready。
      options.onEscape?.()
    } else if (data.type === 'ready') {
      frame.runtime = 'ready'
      frame.runtimeError = ''
    } else if (data.type === 'error' && typeof data.message === 'string') {
      frame.runtime = 'failed'
      frame.runtimeError = data.message.slice(0, 1000)
    }
  }
  window.addEventListener('message', runtimeMessage)

  function observeConnection(instance: string | null | undefined, online: boolean): ConnectionChange {
    const frame = displayed.value
    if (!frame?.live) return null
    const previous = frame.connection
    frame.connection = !online ? 'disconnected' : frame.instance && instance !== frame.instance ? 'gone' : 'online'
    // 状态更新不动这一帧：旧页面一直显示到新一帧装上为止（双缓冲）。只有那两个转折
    // 值得报上去——取数那一层据此重新授权一次。#2349 说的是授权该拒谁，没变；变的是
    // 面板自己注意到了「该换一帧了」，而不是干等一次手动刷新。
    if (frame.connection === 'gone' && previous !== 'gone') return 'instance-changed'
    if (frame.connection !== 'online' || previous !== 'disconnected') return null
    return 'instance-recovered'
  }

  function stopTimer() {
    if (timer) clearInterval(timer)
    timer = null
    document.removeEventListener('visibilitychange', visibilityChanged)
  }

  function accountTime() {
    const now = performance.now()
    if (visible) elapsed += now - lastTick
    lastTick = now
  }

  function visibilityChanged() {
    // Account the old state before changing it. A hidden page may run no ticks.
    accountTime()
    visible = !document.hidden
  }

  function fail(message: string) {
    stopTimer()
    incoming.value = null
    navigation.value = 'failed'
    failedIdentity.value = attemptIdentity
    error.value = message
  }

  function authorize(identity?: string) {
    stopTimer()
    incoming.value = null
    attemptIdentity = identity ?? null
    failedIdentity.value = null
    navigation.value = 'authorizing'
    error.value = ''
  }

  async function navigate(
    session: PreviewSession,
    page: Pick<PreviewFrame, 'url' | 'label' | 'mime' | 'version' | 'live' | 'identity' | 'budgetMs'>,
    stillCurrent: () => boolean,
    path?: string
  ) {
    stopTimer()
    attemptIdentity = page.identity ?? null
    const id = ++serial
    incoming.value = {
      ...page,
      url: session.url,
      id,
      name: `${frameName}-${id}`,
      posted: false,
      resourceId: session.resource_id,
      instance: session.resource?.instance,
      runtime: 'unconfirmed',
      connection: 'online',
    }
    navigation.value = 'navigating'
    error.value = ''
    await nextTick()
    if (!stillCurrent() || incoming.value?.id !== id) return
    // about:blank's mount load is not evidence of the authorized navigation.
    incoming.value.posted = true
    elapsed = 0
    lastTick = performance.now()
    visible = !document.hidden
    document.addEventListener('visibilitychange', visibilityChanged)
    // 预算按这一份资源定：一份静态网页 30 秒够了，一个可能正在冷启动的应用不是。
    const budget = navigationBudget(page.budgetMs)
    const tierSeconds = Math.round(navigationTier(page.budgetMs) / 1000)
    timer = setInterval(() => {
      accountTime()
      if (elapsed >= budget) fail(t('work.room.preview.navigationTimeout', { seconds: tierSeconds }))
    }, 500)
    try {
      postPreviewSession(session, { target: incoming.value.name, ...(path ? { path } : {}) })
    } catch (cause) {
      fail(cause instanceof Error ? cause.message : String(cause))
      throw cause
    }
  }

  function loaded(id: number, event: Event) {
    const frame = incoming.value?.id === id ? incoming.value : displayed.value
    if (!frame || frame.id !== id || !frame.posted) return
    if (!(event.target instanceof HTMLIFrameElement) || event.target.name !== frame.name) return
    // A newly mounted blank document can finish after POST was scheduled.
    // Cross-origin content is opaque; only reject a readable blank document.
    try {
      if (event.target.contentDocument?.URL === 'about:blank') return
    } catch {
      // The authorized content origin is intentionally different from the host.
    }
    if (incoming.value?.id === id) {
      stopTimer()
      displayed.value = frame
      incoming.value = null
      failedIdentity.value = null
      navigation.value = 'loaded'
    }
    // A document reload keeps its frame but must establish a fresh runtime session.
    frame.runtime = 'unconfirmed'
    frame.runtimeError = ''
    runtimeWindow = event.target.contentWindow
    runtimeSession = crypto.randomUUID()
    sendRuntimeHello(frame)
  }

  function failed(id: number, event: Event) {
    const frame = incoming.value
    if (!frame || frame.id !== id || !frame.posted) return
    if (!(event.target instanceof HTMLIFrameElement) || event.target.name !== frame.name) return
    fail(t('work.room.preview.navigationFailed'))
  }

  function pause() {
    stopTimer()
    incoming.value = null
    if (navigation.value !== 'failed') navigation.value = displayed.value ? 'loaded' : 'idle'
  }

  function reset() {
    serial += 1
    runtimeWindow = null
    runtimeSession = ''
    stopTimer()
    displayed.value = null
    incoming.value = null
    failedIdentity.value = null
    attemptIdentity = null
    navigation.value = 'idle'
    error.value = ''
  }

  onBeforeUnmount(() => {
    reset()
    window.removeEventListener('message', runtimeMessage)
  })
  return {
    displayed,
    incoming,
    frames,
    navigation,
    error,
    failedIdentity,
    observeConnection,
    authorize,
    navigate,
    loaded,
    failed,
    fail,
    pause,
    reset,
  }
}
