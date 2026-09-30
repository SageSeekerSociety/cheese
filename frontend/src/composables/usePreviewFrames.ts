import { computed, nextTick, onBeforeUnmount, ref } from 'vue'

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
}

export type PreviewNavigation = 'idle' | 'authorizing' | 'navigating' | 'loaded' | 'failed'

/** Incoming navigation never destroys the last observed loaded browsing context. */
export function usePreviewFrames(frameName: string) {
  const displayed = ref<PreviewFrame | null>(null)
  const incoming = ref<PreviewFrame | null>(null)
  const navigation = ref<PreviewNavigation>('idle')
  const error = ref('')
  const frames = computed(() => [displayed.value, incoming.value].filter((frame): frame is PreviewFrame => !!frame))
  let serial = 0
  let timer: ReturnType<typeof setInterval> | null = null
  let elapsed = 0
  let lastTick = 0

  function stopTimer() {
    if (timer) clearInterval(timer)
    timer = null
  }

  function fail(message: string) {
    stopTimer()
    incoming.value = null
    navigation.value = 'failed'
    error.value = message
  }

  function authorize() {
    stopTimer()
    incoming.value = null
    navigation.value = 'authorizing'
    error.value = ''
  }

  async function navigate(
    session: { url: string; grant: string },
    page: Pick<PreviewFrame, 'url' | 'label' | 'mime' | 'version' | 'live'>,
    stillCurrent: () => boolean,
    path?: string
  ) {
    stopTimer()
    const id = ++serial
    incoming.value = { ...page, id, name: `${frameName}-${id}`, posted: false }
    navigation.value = 'navigating'
    error.value = ''
    await nextTick()
    if (!stillCurrent() || incoming.value?.id !== id) return
    // about:blank's mount load is not evidence of the authorized navigation.
    incoming.value.posted = true
    try {
      postPreviewSession(session, { target: incoming.value.name, ...(path ? { path } : {}) })
    } catch (cause) {
      fail(cause instanceof Error ? cause.message : String(cause))
      throw cause
    }
    elapsed = 0
    lastTick = performance.now()
    timer = setInterval(() => {
      const now = performance.now()
      if (!document.hidden) elapsed += now - lastTick
      lastTick = now
      if (elapsed >= 30_000) fail('页面导航等待超时；尚未确认加载完成。')
    }, 500)
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
    navigation.value = 'loaded'
  }

  function failed(id: number, event: Event) {
    const frame = incoming.value
    if (!frame || frame.id !== id || !frame.posted) return
    if (!(event.target instanceof HTMLIFrameElement) || event.target.name !== frame.name) return
    fail('页面导航失败；可重试当前目标。')
  }

  function pause() {
    stopTimer()
    incoming.value = null
    navigation.value = displayed.value ? 'loaded' : 'idle'
  }

  function reset() {
    serial += 1
    stopTimer()
    displayed.value = null
    incoming.value = null
    navigation.value = 'idle'
    error.value = ''
  }

  onBeforeUnmount(reset)
  return { displayed, incoming, frames, navigation, error, authorize, navigate, loaded, failed, fail, pause, reset }
}
