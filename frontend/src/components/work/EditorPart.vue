<script setup lang="ts">
// One part of the task page's 编辑器 form that holds several things at once: the centre (open
// files, changes, 概览, 预览, outputs opened from the conversation) and the part below it
// (terminals and 现场). A row of tabs on top, and under it the body the work panel teleports
// each thing's content into (`body`); which content shows is the work panel's, as is what a tab
// is. Every tab closes from its own ×, shown at rest; a tab holding unsaved text shows a dot
// there instead, and the × when pointed at. A tab opened in passing is slanted, and a
// double-click keeps it.
//
// Two looks: the centre's row of editor tabs (`tabs`), and the lower part's row of pills
// (`pills`), where tabs after the `add` slot (`after`) sit apart from the ones before it.
import { onBeforeUnmount, ref, watch } from 'vue'

import FileKindMark from '../common/FileKindMark.vue'

import { t } from '@/i18n'

export interface EditorTab {
  key: string
  title: string
  /** Said on hover, when it says more than the title. */
  hint?: string
  /** The file's name, for the mark of its kind before the title. */
  file?: string
  /** A short word after the title (改动, 只读…). */
  tag?: string
  /** Text typed here and not saved yet. */
  held?: boolean
  /** Opened in passing: the next one opened in passing takes its place. */
  temporary?: boolean
  /** Has a × of its own. */
  closable?: boolean
  /** Something is happening in it. */
  pulse?: boolean
}

const props = withDefaults(
  defineProps<{
    tabs: EditorTab[]
    /** Tabs after the `add` slot. */
    after?: EditorTab[]
    active: string | null
    look?: 'tabs' | 'pills'
    /** What the row is, for a screen reader. */
    label: string
  }>(),
  { after: () => [], look: 'tabs' }
)

const emit = defineEmits<{
  select: [key: string]
  close: [key: string]
  keep: [key: string]
  /** Where the content goes: given once the part is drawn, and null when it goes. */
  body: [element: HTMLElement | null]
}>()

defineSlots<{
  /** After the tabs (the 「+」 of the lower part). */
  add?: () => unknown
  /** At the right end of the row. */
  end?: () => unknown
}>()

const bodyEl = ref<HTMLElement | null>(null)
watch(bodyEl, (element) => emit('body', element), { flush: 'post', immediate: true })
onBeforeUnmount(() => emit('body', null))

const closeLabel = (tab: EditorTab) => t('work.room.taskFiles.closeTab', { name: tab.title })
</script>

<template>
  <div class="editor-part" :class="`editor-part--${props.look}`">
    <div class="editor-part__row">
      <div class="editor-part__tabs" role="group" :aria-label="props.label">
        <template v-for="(list, part) in [props.tabs, props.after]" :key="part">
          <span v-if="part === 1 && $slots.add" class="editor-part__add"><slot name="add" /></span>
          <div
            v-for="tab in list"
            :key="tab.key"
            class="editor-tab"
            :class="{
              'editor-tab--on': tab.key === props.active,
              'editor-tab--held': tab.held,
              'editor-tab--apart': part === 1 && tab === list[0],
            }"
          >
            <button
              type="button"
              class="editor-tab__main"
              :title="tab.hint ?? tab.title"
              :aria-current="tab.key === props.active ? 'true' : undefined"
              @click="emit('select', tab.key)"
              @dblclick="tab.temporary && emit('keep', tab.key)"
            >
              <FileKindMark v-if="tab.file" :name="tab.file" />
              <span class="editor-tab__name" :class="{ 'editor-tab__name--passing': tab.temporary }">{{
                tab.title
              }}</span>
              <span v-if="tab.tag" class="editor-tab__tag">{{ tab.tag }}</span>
              <span v-if="tab.pulse" class="editor-tab__pulse" aria-hidden="true" />
            </button>
            <button
              v-if="tab.closable"
              type="button"
              class="editor-tab__close"
              :aria-label="closeLabel(tab)"
              :title="closeLabel(tab)"
              @click="emit('close', tab.key)"
            >
              <span class="editor-tab__dot" />
              <v-icon :size="props.look === 'pills' ? 13 : 14" class="editor-tab__x">mdi-close</v-icon>
            </button>
          </div>
        </template>
        <span v-if="!props.after.length && $slots.add" class="editor-part__add"><slot name="add" /></span>
      </div>
      <div v-if="$slots.end" class="editor-part__end"><slot name="end" /></div>
    </div>
    <div ref="bodyEl" class="editor-part__body" />
  </div>
</template>

<style scoped>
.editor-part {
  display: flex;
  flex-direction: column;
  width: 100%;
  height: 100%;
  min-width: 0;
  min-height: 0;
  background: var(--surface);
}
.editor-part__row {
  display: flex;
  flex: none;
  align-items: stretch;
  min-width: 0;
  border-bottom: 1px solid var(--line);
}
.editor-part__tabs {
  display: flex;
  flex: 1 1 auto;
  align-items: stretch;
  min-width: 0;
  overflow-x: auto;
  scrollbar-width: none;
}
.editor-part__end {
  display: flex;
  flex: none;
  gap: 2px;
  align-items: center;
  padding: 0 6px;
}
.editor-part__add {
  display: flex;
  flex: none;
  align-items: center;
}
.editor-part__body {
  position: relative;
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
}

/* ---- the tab ---- */
.editor-tab {
  position: relative;
  display: flex;
  flex: none;
  align-items: center;
  max-width: 220px;
  color: var(--muted);
}
.editor-tab__main {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  height: 100%;
  border: none;
  background: transparent;
  color: inherit;
  font-size: 13px;
  line-height: var(--lh-13);
  cursor: pointer;
}
.editor-tab__main:focus-visible,
.editor-tab__close:focus-visible {
  outline: 2px solid var(--focus-ring);
  outline-offset: -2px;
}
.editor-tab__name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.editor-tab__name--passing {
  font-style: italic;
}
.editor-tab__tag {
  flex: none;
  padding: 0 6px;
  border-radius: var(--radius-sm);
  background: var(--fill-2);
  color: var(--muted);
  font-size: 12px;
  font-weight: 400;
  line-height: var(--lh-12);
}
.editor-tab__pulse {
  flex: none;
  width: 6px;
  height: 6px;
  border-radius: var(--radius-pill);
  background: var(--accent);
  animation: editor-tab-pulse 1.6s var(--ease-standard) infinite;
}
@keyframes editor-tab-pulse {
  50% {
    opacity: 0.3;
  }
}
@media (prefers-reduced-motion: reduce) {
  .editor-tab__pulse {
    animation: none;
  }
}
.editor-tab__close {
  display: grid;
  flex: none;
  place-items: center;
  padding: 0;
  border: none;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--faint);
  cursor: pointer;
  transition:
    background-color var(--dur-quick) var(--ease-standard),
    color var(--dur-quick) var(--ease-standard);
}
.editor-tab__close:hover {
  background: var(--line-2);
  color: var(--ink);
}
/* Unsaved text: a dot where the × is; pointing at it shows the ×. */
.editor-tab__dot {
  display: none;
  width: 8px;
  height: 8px;
  border-radius: var(--radius-pill);
  background: var(--muted);
}
.editor-tab--held .editor-tab__dot {
  display: block;
}
.editor-tab--held .editor-tab__x,
.editor-tab--held .editor-tab__close:hover .editor-tab__dot,
.editor-tab--held .editor-tab__close:focus-visible .editor-tab__dot {
  display: none;
}
.editor-tab--held .editor-tab__close:hover .editor-tab__x,
.editor-tab--held .editor-tab__close:focus-visible .editor-tab__x {
  display: inline-flex;
}

/* ---- the centre: a row of editor tabs, the one shown open onto its content ---- */
.editor-part--tabs .editor-part__row {
  height: 34px;
  background: var(--canvas);
}
.editor-part--tabs .editor-tab {
  padding-right: 6px;
  border-right: 1px solid var(--line);
  transition:
    background-color var(--dur-quick) var(--ease-standard),
    color var(--dur-quick) var(--ease-standard);
}
.editor-part--tabs .editor-tab__main {
  padding: 0 6px 0 12px;
}
.editor-part--tabs .editor-tab:hover {
  background: var(--fill);
  color: var(--text);
}
.editor-part--tabs .editor-tab--on,
.editor-part--tabs .editor-tab--on:hover {
  background: var(--surface);
  color: var(--ink);
}
.editor-part--tabs .editor-tab--on::before {
  content: '';
  position: absolute;
  top: 0;
  right: 0;
  left: 0;
  height: 2px;
  background: var(--ink);
}
.editor-part--tabs .editor-tab--on::after {
  content: '';
  position: absolute;
  right: 0;
  bottom: -1px;
  left: 0;
  height: 1px;
  background: var(--surface);
}
.editor-part--tabs .editor-tab__close {
  width: 20px;
  height: 20px;
}

/* ---- the part below: a row of pills ---- */
.editor-part--pills .editor-part__row {
  height: 38px;
}
.editor-part--pills .editor-part__tabs {
  gap: 2px;
  align-items: center;
  padding: 0 4px;
}
.editor-part--pills .editor-tab {
  height: 26px;
  padding-right: 4px;
  border-radius: var(--radius-sm);
  transition:
    background-color var(--dur-quick) var(--ease-standard),
    color var(--dur-quick) var(--ease-standard);
}
.editor-part--pills .editor-tab:not(:has(.editor-tab__close)) {
  padding-right: 0;
}
.editor-part--pills .editor-tab__main {
  padding: 0 6px 0 10px;
}
.editor-part--pills .editor-tab:not(:has(.editor-tab__close)) .editor-tab__main {
  padding-right: 10px;
}
.editor-part--pills .editor-tab:hover {
  color: var(--text);
}
.editor-part--pills .editor-tab--on,
.editor-part--pills .editor-tab--on:hover {
  background: var(--fill-2);
  color: var(--ink);
  font-weight: 600;
}
.editor-part--pills .editor-tab--apart {
  margin-left: 8px;
}
.editor-part--pills .editor-tab__close {
  width: 16px;
  height: 16px;
}
</style>
