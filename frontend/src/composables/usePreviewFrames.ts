import type { PreviewSession } from '../api'

import { computed, nextTick, onBeforeUnmount, ref } from 'vue'

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
}

export type PreviewNavigation = 'idle' | 'authorizing' | 'navigating' | 'loaded' | 'failed'

/** Incoming navigation never destroys the last observed loaded browsing context. */
export function usePreviewFrames(frameName: string) {
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

  function runtimeMessage(event: MessageEvent) {
    const frame = displayed.value
    if (!frame || !runtimeWindow || event.source !== runtimeWindow || event.origin !== new URL(frame.url).origin) return
    const data = event.data
    if (!data || data.channel !== 'cheese-preview-runtime' || data.version !== 1 || data.sessionId !== runtimeSession)
      return
    if (data.type === 'ready') {
      frame.runtime = 'ready'
      frame.runtimeError = ''
    } else if (data.type === 'error' && typeof data.message === 'string') {
      frame.runtime = 'failed'
      frame.runtimeError = data.message.slice(0, 1000)
    }
  }
  window.addEventListener('message', runtimeMessage)

  function observeConnection(instance: string | null | undefined, online: boolean) {
    const frame = displayed.value
    if (!frame?.live) return
    frame.connection = !online ? 'disconnected' : frame.instance && instance !== frame.instance ? 'gone' : 'online'
    // Recovery updates status only: never POST/remount a loaded browsing context.
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
    page: Pick<PreviewFrame, 'url' | 'label' | 'mime' | 'version' | 'live' | 'identity'>,
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
    timer = setInterval(() => {
      accountTime()
      if (elapsed >= 30_000) fail(t('work.room.preview.navigationTimeout'))
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
    try {
      runtimeWindow?.postMessage(
        {
          channel: 'cheese-preview-runtime',
          version: 1,
          type: 'hello',
          sessionId: runtimeSession,
          resourceId: frame.resourceId ?? null,
        },
        new URL(frame.url).origin
      )
    } catch {
      // A replaced or opaque document cannot acknowledge; remain unconfirmed.
      runtimeWindow = null
    }
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
