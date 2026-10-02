<script setup lang="ts">
import type { RegionNoteGeometry } from './designRegionNotePosition'

import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'

import { designRegionNotePosition } from './designRegionNotePosition'

import { t } from '@/i18n'

const props = defineProps<{
  target: { label: string; quote: string }
  note: string
  geometry: RegionNoteGeometry | null
  resourceKey: string
  focusOrigin: Element | null
  restoreFocus: (resourceKey: string) => void
}>()
const emit = defineEmits<{ 'update:note': [value: string]; send: []; cancel: [] }>()
const card = ref<HTMLElement | null>(null)
const input = ref<HTMLInputElement | null>(null)
const size = ref({ width: 300, height: 100 })
const expanded = ref(false)
const position = computed(() => designRegionNotePosition(props.geometry, size.value))
const compact = computed(() => position.value?.compact && !expanded.value)
const style = computed(() => {
  const placed = position.value
  return placed
    ? {
        left: `${placed.left}px`,
        top: `${placed.top}px`,
        width: `${placed.width}px`,
        maxHeight: `${placed.maxHeight}px`,
      }
    : {}
})
let observer: ResizeObserver | null = null
let handledTarget: typeof props.target | null = null
watch(
  card,
  (element) => {
    observer?.disconnect()
    if (!element) return
    observer = new ResizeObserver(() => {
      const rect = element.getBoundingClientRect()
      // The collapsed entry must not become the full composer's required height:
      // otherwise its smaller measurement flips a short viewport back to expanded.
      size.value = { width: 300, height: Math.max(100, rect.height, element.scrollHeight) }
    })
    observer.observe(element)
  },
  { flush: 'post' }
)
watch(
  [() => props.target, position],
  async () => {
    if (handledTarget === props.target || !position.value) return
    const target = props.target
    handledTarget = target
    await nextTick()
    if (target !== props.target || !input.value?.isConnected) return
    const document = input.value.ownerDocument
    if (
      !document.hasFocus() ||
      (document.activeElement !== props.focusOrigin && document.activeElement !== document.body)
    )
      return
    if (compact.value)
      card.value?.querySelector<HTMLButtonElement>('.design-region-note__expand')?.focus({ preventScroll: true })
    else input.value.focus({ preventScroll: true })
  },
  { immediate: true }
)
async function expand(event: MouseEvent) {
  const opener = event.currentTarget
  expanded.value = true
  await nextTick()
  const document = input.value?.ownerDocument
  if (document?.hasFocus() && (document.activeElement === opener || document.activeElement === document.body))
    input.value?.focus({ preventScroll: true })
}
function close(action: 'send' | 'cancel', event: KeyboardEvent | MouseEvent) {
  if ('isComposing' in event && (event.isComposing || event.keyCode === 229)) return
  if (action === 'send' && !props.note.trim()) return
  event.preventDefault()
  const owned = !!card.value?.contains(card.value.ownerDocument.activeElement)
  const key = props.resourceKey
  const restore = props.restoreFocus
  if (action === 'send') emit('send')
  else emit('cancel')
  if (owned) void nextTick(() => restore(key))
}
onBeforeUnmount(() => observer?.disconnect())
</script>

<template>
  <div
    ref="card"
    data-region-note
    class="design-region-note"
    :class="{ 'design-region-note--compact': position?.compact }"
    :data-placement="position?.placement"
    :style="style"
    @keydown.esc="close('cancel', $event)"
  >
    <div v-show="!compact" class="design-region-note__where">
      <span class="t-meta">{{ target.label }}</span>
      <span class="design-region-note__quote t-meta">{{ target.quote }}</span>
      <span v-if="geometry && !geometry.region" class="t-meta" role="status">{{ t('design.regionOutsideView') }}</span>
    </div>
    <div class="design-region-note__actions">
      <button v-if="compact" type="button" class="design-region-note__expand" @click="expand">
        {{ t('work.room.preview.locatorPlaceholder') }}
      </button>
      <input
        v-show="!compact"
        ref="input"
        :value="note"
        autocomplete="off"
        :aria-label="t('work.room.preview.locatorPlaceholder')"
        :placeholder="t('work.room.preview.locatorPlaceholder')"
        @input="emit('update:note', ($event.target as HTMLInputElement).value)"
        @keydown.enter="close('send', $event)"
      />
      <button type="button" class="design-region-note__send" :disabled="!note.trim()" @click="close('send', $event)">
        {{ t('work.room.preview.send') }}
      </button>
      <button
        type="button"
        :aria-label="t('work.room.preview.cancel')"
        :title="t('work.room.preview.cancel')"
        @click="close('cancel', $event)"
      >
        ×
      </button>
    </div>
  </div>
</template>

<style scoped>
.design-region-note {
  position: absolute;
  display: flex;
  flex-direction: column;
  gap: 8px;
  box-sizing: border-box;
  overflow: hidden;
  padding: 8px;
  color: var(--text);
  background: var(--raised);
  border: 1px solid var(--line-2);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-2);
  pointer-events: auto;
}
.design-region-note__where {
  display: flex;
  flex-direction: column;
  min-height: 0;
  overflow: auto;
  color: var(--muted);
}
.design-region-note__quote {
  overflow-wrap: anywhere;
}
.design-region-note__actions {
  display: flex;
  flex: none;
  align-items: center;
  gap: 4px;
}
.design-region-note input {
  width: 0;
  min-width: 0;
  flex: 1;
  padding: 4px;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-sm);
}
.design-region-note button {
  flex: none;
  padding: 4px 8px;
  border-radius: var(--radius-sm);
  font-size: 13px;
  line-height: var(--lh-13);
}
.design-region-note input:focus-visible,
.design-region-note button:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}
.design-region-note button:hover:not(:disabled) {
  background: var(--fill-2);
}
.design-region-note__send {
  color: var(--accent-ink);
  background: var(--accent-wash);
}
.design-region-note button:disabled {
  color: var(--faint);
}
.design-region-note__expand {
  min-width: 0;
  flex: 1;
}
.design-region-note--compact {
  gap: 0;
  padding: 0;
}
.design-region-note--compact button {
  padding: 0 4px;
}
</style>
