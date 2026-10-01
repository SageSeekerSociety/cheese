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
    session: { url: string; grant: string },
    page: Pick<PreviewFrame, 'url' | 'label' | 'mime' | 'version' | 'live' | 'identity'>,
    stillCurrent: () => boolean,
    path?: string
  ) {
    stopTimer()
    attemptIdentity = page.identity ?? null
    const id = ++serial
    incoming.value = { ...page, id, name: `${frameName}-${id}`, posted: false }
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
    const frame = incoming.value
    if (!frame || frame.id !== id || !frame.posted) return
    if (!(event.target instanceof HTMLIFrameElement) || event.target.name !== frame.name) return
    // A newly mounted blank document can finish after POST was scheduled.
    // Cross-origin content is opaque; only reject a readable blank document.
    try {
      if (event.target.contentDocument?.URL === 'about:blank') return
    } catch {
      // The authorized content origin is intentionally different from the host.
    }
    stopTimer()
    displayed.value = frame
    incoming.value = null
    failedIdentity.value = null
    navigation.value = 'loaded'
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
    stopTimer()
    displayed.value = null
    incoming.value = null
    failedIdentity.value = null
    attemptIdentity = null
    navigation.value = 'idle'
    error.value = ''
  }

  onBeforeUnmount(reset)
  return {
    displayed,
    incoming,
    frames,
    navigation,
    error,
    failedIdentity,
    authorize,
    navigate,
    loaded,
    failed,
    fail,
    pause,
    reset,
  }
}
