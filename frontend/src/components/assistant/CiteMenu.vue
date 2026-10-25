<script setup lang="ts">
// 芝士输入框上方的 `@` 菜单：每行一个图标、名字（打的字对上的那段加粗）、右边灰字写在哪。
// 候选和高亮都在外面算好（`useCitePicker`），这里只画、只往外报点了哪一行。
import type { CitePicker } from '@/composables/useCitePicker'
import type { CitableKind } from '@/types/citable'

import { computed, nextTick, onMounted, ref } from 'vue'

import { t } from '@/i18n'

const props = defineProps<{
  /** 候选、高亮、在哪一类：都在 `useCitePicker` 里算好，点哪一行也交回给它。 */
  picker: CitePicker
  /** 眼前这个项目叫什么；不在项目里是 null。 */
  project: string | null
  most: number
}>()

const rows = computed(() => props.picker.rows.value)
const active = computed(() => props.picker.active.value)
const kind = computed(() => props.picker.kind.value)
const loading = computed(() => props.picker.loading.value)

function pick(index: number) {
  props.picker.choose(props.picker.choices.value[index])
}

function point(index: number) {
  if (index !== active.value) props.picker.point(index)
}

const ICON: Record<CitableKind, string> = {
  task: 'mdi-checkbox-marked-outline',
  channel: 'mdi-pound',
  doc: 'mdi-file-document-outline',
  file: 'mdi-paperclip',
  problem: 'mdi-trophy-outline',
}

/** 每一行在能挑的几行里排第几；分组标题是 -1。 */
const order = computed(() => {
  let n = 0
  return rows.value.map((row) => (row.type === 'item' || row.type === 'kind' ? n++ : -1))
})

function groupLabel(label: 'recent' | 'kinds' | CitableKind): string {
  if (label === 'recent') return t('tasks.assistant.cite.recent')
  if (label === 'kinds')
    return props.project
      ? t('tasks.assistant.cite.inProject', { name: props.project })
      : t('tasks.assistant.cite.everywhere')
  return t(`tasks.assistant.cite.kinds.${label}`)
}

function split(title: string, range: [number, number] | null): [string, string, string] {
  if (!range) return [title, '', '']
  return [title.slice(0, range[0]), title.slice(range[0], range[1]), title.slice(range[1])]
}

const list = ref<HTMLElement | null>(null)
const root = ref<HTMLElement | null>(null)
/** 菜单往上长，最高到外面那个会裁掉它的框（面板）的顶上：矮的面板里不顶出去。 */
const room = ref<number | null>(null)

function measure() {
  const el = root.value
  if (!el) return
  let clip: HTMLElement | null = el.parentElement
  while (clip && getComputedStyle(clip).overflowY === 'visible') clip = clip.parentElement
  const top = clip ? clip.getBoundingClientRect().top : 0
  room.value = Math.max(160, el.getBoundingClientRect().bottom - top - 8)
}

onMounted(measure)

async function scrollActiveIntoView() {
  await nextTick()
  list.value?.querySelector('.cite__row.is-on')?.scrollIntoView?.({ block: 'nearest' })
}

defineExpose({ scrollActiveIntoView })
</script>

<template>
  <div
    ref="root"
    class="cite"
    role="listbox"
    :aria-label="t('tasks.assistant.cite.label')"
    :style="room ? { maxHeight: `min(360px, ${room}px)` } : undefined"
  >
    <div v-if="kind" class="cite__head">
      <button
        type="button"
        class="cite__back"
        :aria-label="t('tasks.assistant.cite.back')"
        @mousedown.prevent="picker.back()"
      >
        <v-icon size="16">mdi-chevron-left</v-icon>
      </button>
      <span class="cite__title">{{ t(`tasks.assistant.cite.kinds.${kind}`) }}</span>
    </div>
    <div ref="list" class="cite__list">
      <p v-if="loading" class="cite__empty">{{ t('tasks.assistant.cite.loading') }}</p>
      <template v-else>
        <template v-for="(row, i) in rows" :key="i">
          <p v-if="row.type === 'group'" class="cite__group">{{ groupLabel(row.label) }}</p>
          <p v-else-if="row.type === 'full'" class="cite__empty">{{ t('tasks.assistant.cite.tooMany', { most }) }}</p>
          <div
            v-else
            class="cite__row"
            :class="{ 'is-on': order[i] === active }"
            role="option"
            tabindex="-1"
            :aria-selected="order[i] === active"
            @mousedown.prevent="pick(order[i])"
            @mousemove="point(order[i])"
          >
            <template v-if="row.type === 'kind'">
              <v-icon size="16" class="cite__icon">{{ ICON[row.kind] }}</v-icon>
              <span class="cite__name">{{ t(`tasks.assistant.cite.kinds.${row.kind}`) }}</span>
              <v-icon size="16" class="cite__icon">mdi-chevron-right</v-icon>
            </template>
            <template v-else>
              <v-icon size="16" class="cite__icon">{{ ICON[row.item.kind] }}</v-icon>
              <span class="cite__name" data-user-content
                >{{ split(row.item.title, row.range)[0] }}<b>{{ split(row.item.title, row.range)[1] }}</b
                >{{ split(row.item.title, row.range)[2] }}</span
              >
              <span class="cite__where" data-user-content
                ><span>{{ row.item.where }}</span
                ><span v-if="row.item.place">{{ row.item.place }}</span></span
              >
            </template>
          </div>
        </template>
        <p v-if="!rows.length" class="cite__empty">{{ t('tasks.assistant.cite.empty') }}</p>
      </template>
    </div>
  </div>
</template>

<style scoped>
.cite {
  display: flex;
  flex-direction: column;
  max-height: 360px;
  overflow: hidden;
  background: var(--surface);
  border: 1px solid var(--line-2);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-2);
}

.cite__head {
  display: flex;
  gap: 8px;
  align-items: center;
  padding: 8px 12px;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
  border-bottom: 1px solid var(--line);
}

.cite__back {
  display: grid;
  place-items: center;
  padding: 0 2px;
  color: var(--muted);
  cursor: pointer;
  background: none;
  border: 0;
  border-radius: var(--radius-sm);
}

.cite__back:hover {
  color: var(--ink);
  background: var(--fill-2);
}

.cite__title {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.cite__list {
  min-height: 0;
  padding: 4px;
  overflow-y: auto;
}

.cite__group {
  padding: 8px 8px 4px;
  margin: 0;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}

.cite__row {
  display: grid;
  grid-template-columns: 20px 1fr auto;
  gap: 8px;
  align-items: center;
  padding: 6px 8px;
  cursor: pointer;
  border-radius: var(--radius-md);
}

.cite__row.is-on {
  background: var(--line-2);
}

.cite__icon {
  color: var(--muted);
}

.cite__name {
  overflow: hidden;
  color: var(--ink);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.cite__name b {
  font-weight: 600;
}

.cite__where {
  display: flex;
  gap: 8px;
  max-width: 160px;
  overflow: hidden;
  font-size: 13px;
  color: var(--muted);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.cite__empty {
  padding: 12px;
  margin: 0;
  color: var(--muted);
}
</style>
