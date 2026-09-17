<script setup lang="ts">
// A PDF's first page, drawn at tile size, for the composer's attachment strip.
//
// Without this a PDF was a filename on a chip, which says nothing about which
// document it is — and the one thing a person checks before sending is that
// they attached the right file. A name like `report.pdf` does not answer that;
// the cover page does.
//
// The bytes come from `previewFileBytes`, not the image URL helper: the raw
// endpoint only serves a non-image with `download=true`, which that helper does
// not set. The same reason PreviewPages uses it.
import { onBeforeUnmount, ref, watch } from 'vue'

import { previewFileBytes } from '../api'

type PdfLib = typeof import('pdfjs-dist')

const props = defineProps<{
  topicId: string | null
  path: string
}>()

let lib: PdfLib | null = null
// Every load gets a number, so a reply that arrives after the props moved on
// cannot draw over the tile that replaced it.
let generation = 0

const canvas = ref<HTMLCanvasElement | null>(null)
const failed = ref(false)
const drawn = ref(false)

const title = () => props.path.split('/').pop() || 'PDF'

/** pdf.js needs its worker pinned before the first getDocument, or it guesses
 *  an address it cannot reach. Shared across tiles — loading it is the
 *  expensive part, and it is the same worker either way. */
async function library(): Promise<PdfLib> {
  if (lib) return lib
  const [pdfjs, workerUrl] = await Promise.all([
    import('pdfjs-dist'),
    import('pdfjs-dist/build/pdf.worker.min.mjs?url'),
  ])
  pdfjs.GlobalWorkerOptions.workerSrc = workerUrl.default
  lib = pdfjs
  return lib
}

async function draw() {
  const mine = ++generation
  failed.value = false
  drawn.value = false
  if (!props.topicId || !props.path) return
  try {
    const [bytes, pdfjs] = await Promise.all([previewFileBytes(props.topicId, props.path), library()])
    if (mine !== generation) return
    // pdf.js takes ownership of the buffer it is handed, so it gets a copy —
    // otherwise re-opening the same bytes finds them empty.
    const task = pdfjs.getDocument({ data: bytes.slice(0) })
    try {
      const doc = await task.promise
      if (mine !== generation) return
      const page = await doc.getPage(1)
      const el = canvas.value
      if (!el || mine !== generation) return
      // Cover the square: scale by the LONGER side so a portrait page fills the
      // tile and gets cropped, the way an image thumbnail does, rather than
      // sitting in the middle of two grey bars.
      const base = page.getViewport({ scale: 1 })
      // The box's own width, so the page is rendered at the size it is shown
      // at. The fallback is for an environment with no layout at all (jsdom),
      // where nothing is drawn anyway and 0 would only make `scale` collapse.
      const tile = el.clientWidth || 40
      const ratio = Math.min(window.devicePixelRatio || 1, 2)
      const scale = (tile / Math.min(base.width, base.height)) * ratio
      const viewport = page.getViewport({ scale })
      el.width = Math.max(1, Math.round(viewport.width))
      el.height = Math.max(1, Math.round(viewport.height))
      const context = el.getContext('2d')
      if (!context) return
      await page.render({ canvas: el, canvasContext: context, viewport }).promise
      if (mine !== generation) return
      drawn.value = true
    } finally {
      void task.destroy()
    }
  } catch {
    // A thumbnail that cannot be drawn is not an error worth interrupting the
    // composer for — the tile falls back to naming the file.
    if (mine === generation) failed.value = true
  }
}

watch(() => [props.topicId, props.path], draw, { immediate: true })
onBeforeUnmount(() => {
  generation += 1
})
</script>

<template>
  <span class="att-face" :title="title()">
    <canvas ref="canvas" class="pdf-thumb__page" :class="{ 'pdf-thumb__page--ready': drawn }" />
    <v-icon v-if="!drawn" size="16">mdi-file-pdf-box</v-icon>
  </span>
</template>

<style scoped>
/* The box itself is `.att-face` (style.css), shared by every attachment state;
   only what sits inside it is this component's.
   Absolute, and hidden with opacity rather than `display: none`: an undrawn
   canvas would otherwise measure 0 wide, and `draw()` reads that width to
   decide how large a page to render. */
.pdf-thumb__page {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  object-fit: cover;
  opacity: 0;
}
.pdf-thumb__page--ready {
  opacity: 1;
}
/* No "PDF" badge over the page: a rendered cover page already reads as a
   document, and the label would have needed a font size outside the scale. */
</style>
