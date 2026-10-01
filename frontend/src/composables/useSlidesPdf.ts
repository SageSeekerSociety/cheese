import type { PDFDocumentLoadingTask, PDFDocumentProxy } from 'pdfjs-dist'

import { onBeforeUnmount, ref, shallowRef, watch, type WatchSource } from 'vue'

import { t } from '@/i18n'

type PdfLib = typeof import('pdfjs-dist/legacy/build/pdf.mjs')
type Paint = { cancel: () => void }
export type SlidePaintOptions = { width: number; height?: number; text?: boolean }

let libraryPromise: Promise<PdfLib> | null = null
function library(): Promise<PdfLib> {
  libraryPromise ??= Promise.all([
    import('pdfjs-dist/legacy/build/pdf.mjs'),
    import('pdfjs-dist/legacy/build/pdf.worker.min.mjs?url'),
  ])
    .then(([pdfjs, worker]) => {
      pdfjs.GlobalWorkerOptions.workerSrc = worker.default
      return pdfjs
    })
    .catch((error) => {
      libraryPromise = null
      throw error
    })
  return libraryPromise
}

/** One parse per byte identity. Resizing or presenting only replaces paints. */
export function useSlidesPdf(data: WatchSource<ArrayBuffer | null>) {
  const count = ref(0)
  const loading = ref(false)
  const failure = ref('')
  const revision = ref(0)
  const document = shallowRef<PDFDocumentProxy | null>(null)
  let generation = 0
  let task: PDFDocumentLoadingTask | null = null
  const paints = new Map<HTMLElement, Paint>()

  function cancelPaint(host: HTMLElement) {
    paints.get(host)?.cancel()
    paints.delete(host)
    host.replaceChildren()
  }

  function retire() {
    generation += 1
    for (const host of paints.keys()) cancelPaint(host)
    document.value = null
    const old = task
    task = null
    void old?.destroy().catch(() => {})
    count.value = 0
    revision.value += 1
  }

  async function open(bytes: ArrayBuffer | null) {
    retire()
    const mine = generation
    loading.value = !!bytes
    failure.value = ''
    if (!bytes) return
    try {
      const pdfjs = await library()
      if (mine !== generation) return
      const pending = pdfjs.getDocument({ data: bytes.slice(0) })
      task = pending
      const loaded = await pending.promise
      if (mine !== generation) return
      document.value = loaded
      count.value = loaded.numPages
      revision.value += 1
    } catch (error) {
      if (mine === generation) {
        failure.value = error instanceof Error ? error.message : t('slides.openFailed')
      }
    } finally {
      if (mine === generation) loading.value = false
    }
  }

  async function paint(number: number, host: HTMLElement, options: SlidePaintOptions) {
    cancelPaint(host)
    const pdf = document.value
    if (!pdf || number < 1 || number > count.value) return
    const mine = generation
    let cancelled = false
    let render: { cancel: () => void } | null = null
    let layer: { cancel: () => void } | null = null
    const job: Paint = {
      cancel: () => {
        cancelled = true
        render?.cancel()
        layer?.cancel()
      },
    }
    paints.set(host, job)
    const current = () => !cancelled && mine === generation && paints.get(host) === job
    try {
      const page = await pdf.getPage(number)
      if (!current()) return
      const base = page.getViewport({ scale: 1 })
      const scale = Math.max(
        0.05,
        Math.min(options.width / base.width, options.height ? options.height / base.height : 2, 2)
      )
      const viewport = page.getViewport({ scale })
      // Cap both DPR and pixel area: a large slide must not allocate an unbounded bitmap.
      const ratio = Math.min(window.devicePixelRatio || 1, 2, Math.sqrt(4_000_000 / (viewport.width * viewport.height)))
      const canvas = window.document.createElement('canvas')
      canvas.width = Math.max(1, Math.floor(viewport.width * ratio))
      canvas.height = Math.max(1, Math.floor(viewport.height * ratio))
      canvas.style.width = `${viewport.width}px`
      canvas.style.height = `${viewport.height}px`
      host.style.width = `${viewport.width}px`
      host.style.height = `${viewport.height}px`
      host.replaceChildren(canvas)
      const rendering = page.render({
        canvas,
        viewport,
        transform: ratio === 1 ? undefined : [ratio, 0, 0, ratio, 0, 0],
      })
      render = rendering
      await rendering.promise
      if (!current() || !options.text) return
      const pdfjs = await library()
      if (!current()) return
      const text = window.document.createElement('div')
      text.className = 'slide-text-layer'
      text.style.setProperty('--total-scale-factor', String(viewport.scale))
      text.style.setProperty('--scale-round-x', '1px')
      text.style.setProperty('--scale-round-y', '1px')
      host.appendChild(text)
      const textLayer = new pdfjs.TextLayer({
        container: text,
        viewport,
        textContentSource: page.streamTextContent(),
      })
      layer = textLayer
      await textLayer.render()
    } catch (error) {
      if (!current()) return
      const message = window.document.createElement('div')
      message.className = 'slide-paint-error'
      message.setAttribute('role', 'alert')
      message.textContent = t('slides.pageFailed', {
        page: number,
        error: error instanceof Error ? error.message : t('slides.openFailed'),
      })
      host.replaceChildren(message)
    }
  }

  async function pageText(number: number): Promise<string | null> {
    const pdf = document.value
    const mine = generation
    if (!pdf || number < 1 || number > count.value) return null
    const page = await pdf.getPage(number)
    if (mine !== generation) return null
    const content = await page.getTextContent()
    if (mine !== generation) return null
    return content.items
      .map((item) => ('str' in item ? item.str + (item.hasEOL ? '\n' : ' ') : ''))
      .join('')
      .trim()
  }

  watch(data, (bytes) => void open(bytes), { immediate: true })
  onBeforeUnmount(retire)
  return { count, loading, failure, revision, paint, cancelPaint, pageText }
}
