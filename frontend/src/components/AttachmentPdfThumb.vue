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
      const tile = el.clientWidth || el.width || 56
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
  <span class="pdf-thumb" :title="title()">
    <canvas ref="canvas" class="pdf-thumb__page" :class="{ 'pdf-thumb__page--ready': drawn }" />
    <v-icon v-if="!drawn" size="16" class="pdf-thumb__mark">mdi-file-pdf-box</v-icon>
  </span>
</template>

<style scoped>
/* Size comes from --att-tile (style.css): uploading, image, PDF and plain file
   are four states of one position, so none of them may name its own number. */
.pdf-thumb {
  position: relative;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: var(--att-tile);
  height: var(--att-tile);
  border-radius: var(--radius-md);
  border: 1px solid var(--line);
  background: var(--fill);
  overflow: hidden;
}
.pdf-thumb__page {
  width: 100%;
  height: 100%;
  object-fit: cover;
  /* Hidden until a page is actually on it, so an empty canvas does not flash
     as a white square inside the tile. */
  display: none;
}
.pdf-thumb__page--ready {
  display: block;
}
.pdf-thumb__mark {
  color: var(--muted);
}
/* No "PDF" badge over the page: a rendered cover page already reads as a
   document, and the label would have needed a font size outside the scale. */
</style>
