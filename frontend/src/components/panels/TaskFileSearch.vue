<script setup lang="ts">
// The 搜索 view of the 编辑器 form's left column: one box for the text, and under it
// the lines of the task's files that hold it, by file. A line opens its file at that
// line in the centre; a file's row opens it at its first match. The line last opened
// (`opened`) stays marked. It draws what the page's
// search holds (`composables/useTaskSearch`) and fetches nothing. A search waiting for the task's
// environment says so as every surface of the task page does (`TaskEnvironmentNote`).
import type { FileSearchResult, TaskSearchState } from '../../composables/useTaskSearch'

import { computed, nextTick, onMounted, ref, watch } from 'vue'

import FileKindMark from '../common/FileKindMark.vue'
import TaskEnvironmentNote from '../common/TaskEnvironmentNote.vue'

import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'

const props = withDefaults(
  defineProps<{
    query: string
    state: TaskSearchState
    result: FileSearchResult | null
    /** What the server said when the search failed. */
    error?: string
    /** Searching again would answer the same: no 「重试」. */
    final?: boolean
    /** The task's environment, while the search waits for it. */
    environment?: { state: 'preparing' | 'failed' | 'unanswered'; since: number | null; detail: string } | null
    /** The longest text the box takes. */
    maxLength?: number
    /** The person just asked for the search (its view button, the shortcut): the box takes the focus. */
    focus?: boolean
    /** The match last opened from here. */
    opened?: { path: string; line: number } | null
  }>(),
  { error: '', final: false, environment: null, maxLength: 200, focus: false, opened: null }
)

const emit = defineEmits<{
  'update:query': [query: string]
  /** Search now (Enter). */
  submit: []
  retry: []
  /** Open this file at this line in the centre. */
  open: [path: string, line: number]
  /** The box took the focus that was asked for. */
  focused: []
}>()

const box = ref<HTMLInputElement | null>(null)
function takeFocus() {
  if (!props.focus) return
  void nextTick(() => {
    box.value?.focus()
    box.value?.select()
    emit('focused')
  })
}
onMounted(takeFocus)
watch(() => props.focus, takeFocus)

/** How much of a line is kept before its first match when the match sits far in: a few
 *  characters, so the match is in view in a narrow column (a Chinese character is two wide). */
const LEAD = 6

interface Segment {
  text: string
  hit: boolean
}
function segments(text: string, columns: [number, number][], cutBefore: boolean): Segment[] {
  const out: Segment[] = []
  const first = columns[0]?.[0] ?? 0
  // The line's indent says nothing here.
  let at = Math.min(text.length - text.trimStart().length, first)
  // A match far into the line: what precedes it is shortened, so the match is in view.
  if (first - at > LEAD * 2) {
    at = first - LEAD
    out.push({ text: '…', hit: false })
  } else if (cutBefore) out.push({ text: '…', hit: false })
  for (const [start, end] of columns) {
    if (end <= at) continue
    if (start > at) out.push({ text: text.slice(at, start), hit: false })
    out.push({ text: text.slice(Math.max(start, at), end), hit: true })
    at = end
  }
  if (at < text.length) out.push({ text: text.slice(at), hit: false })
  return out
}

const files = computed(() =>
  (props.result?.files ?? []).map((file) => {
    const at = file.path.lastIndexOf('/')
    return {
      path: file.path,
      name: file.path.slice(at + 1),
      folder: at < 0 ? '' : file.path.slice(0, at),
      lines: file.matches.map((m) => ({ line: m.line, parts: segments(m.text, m.columns, m.cut_before) })),
    }
  })
)
const matchCount = computed(() => files.value.reduce((sum, file) => sum + file.lines.length, 0))
const typed = computed(() => props.query.trim().length > 0)
const isOpened = (path: string, line: number) => props.opened?.path === path && props.opened.line === line
</script>

<template>
  <div class="task-search">
    <div class="task-search__box">
      <input
        ref="box"
        class="task-search__input"
        type="search"
        :value="props.query"
        :maxlength="props.maxLength"
        :placeholder="t('work.room.navigator.search.placeholder')"
        :aria-label="t('work.room.navigator.search.label')"
        autocomplete="off"
        spellcheck="false"
        @input="emit('update:query', ($event.target as HTMLInputElement).value)"
        @keydown.enter.prevent="emit('submit')"
      />
      <v-progress-circular
        v-if="props.state === 'searching'"
        class="task-search__spin"
        indeterminate
        size="12"
        width="2"
        role="status"
        :aria-label="t('work.room.navigator.search.label')"
      />
    </div>
    <template v-if="typed">
      <TaskEnvironmentNote
        v-if="props.state === 'environment' && props.environment"
        class="task-search__env"
        :state="props.environment.state"
        :since="props.environment.since"
        :detail="props.environment.detail"
        @retry="emit('retry')"
      />
      <div v-else-if="props.state === 'error'" class="task-search__error" role="alert">
        <div class="task-search__error-row">
          <!-- A search that cannot be made says why, and only that; one that failed offers to try again. -->
          <span class="task-search__error-text" :class="{ 'task-search__error-text--plain': props.final }">{{
            props.final && props.error ? props.error : t('work.room.navigator.search.failed')
          }}</span>
          <BaseButton v-if="!props.final" kind="ghost" size="sm" @click="emit('retry')">{{
            t('global.loadError.retry')
          }}</BaseButton>
        </div>
        <p v-if="props.error && !props.final" class="task-search__reason">{{ props.error }}</p>
      </div>
      <template v-else-if="props.result">
        <p v-if="!files.length" class="task-search__empty">{{ t('work.room.navigator.search.empty') }}</p>
        <template v-else>
          <p class="task-search__count">
            <span>{{ t('work.room.navigator.search.matches', { count: matchCount }) }}</span>
            <span>{{ t('work.room.navigator.search.files', { count: files.length }) }}</span>
          </p>
          <ul class="task-search__list" :aria-label="t('work.room.navigator.search.label')">
            <li v-for="file in files" :key="file.path">
              <button
                type="button"
                class="search-file"
                :title="file.path"
                @click="emit('open', file.path, file.lines[0]?.line ?? 1)"
              >
                <FileKindMark :name="file.name" />
                <span class="search-file__name">{{ file.name }}</span>
                <span v-if="file.folder" class="search-file__folder">{{ file.folder }}</span>
                <span
                  class="search-file__count"
                  :aria-label="t('work.room.navigator.search.matches', { count: file.lines.length })"
                  >{{ file.lines.length }}</span
                >
              </button>
              <ul class="search-file__lines">
                <li v-for="hit in file.lines" :key="hit.line">
                  <button
                    type="button"
                    class="search-line"
                    :class="{ 'search-line--on': isOpened(file.path, hit.line) }"
                    :aria-current="isOpened(file.path, hit.line) ? 'true' : undefined"
                    :data-line="hit.line"
                    @click="emit('open', file.path, hit.line)"
                  >
                    <template v-for="(part, i) in hit.parts" :key="i"
                      ><mark v-if="part.hit" class="search-line__hit">{{ part.text }}</mark
                      ><template v-else>{{ part.text }}</template></template
                    >
                  </button>
                </li>
              </ul>
            </li>
          </ul>
          <p v-if="props.result.truncated" class="task-search__note">
            {{ t('work.room.navigator.search.truncated') }}
          </p>
        </template>
      </template>
    </template>
  </div>
</template>

<style scoped>
.task-search {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
}
.task-search__box {
  position: relative;
  flex: none;
  margin: 8px;
}
.task-search__input {
  width: 100%;
  height: 30px;
  padding: 0 28px 0 8px;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--ink);
  background: var(--surface);
  border: 1px solid var(--line-2);
  border-radius: var(--radius-sm);
  outline: none;
}
.task-search__input::placeholder {
  color: var(--muted);
}
.task-search__input:focus-visible {
  border-color: var(--focus-ring);
  box-shadow: 0 0 0 1px var(--focus-ring);
}
/* The browser's own clear mark is not one of ours. */
.task-search__input::-webkit-search-cancel-button {
  display: none;
}
.task-search__spin {
  position: absolute;
  top: 9px;
  right: 9px;
  color: var(--muted);
}
.task-search__count {
  display: flex;
  flex: none;
  gap: 12px;
  margin: 0;
  padding: 0 12px 4px;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}
.task-search__empty,
.task-search__note,
.task-search__reason {
  margin: 0;
  padding: 4px 12px;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}
.task-search__note {
  padding: 8px 12px;
}
.task-search__error {
  flex: none;
}
.task-search__env {
  flex: none;
  padding: 0 4px 0 12px;
}
.task-search__error-row {
  display: flex;
  gap: 8px;
  align-items: center;
  padding: 0 4px 0 12px;
}
.task-search__error-text {
  flex: 1 1 auto;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--danger-ink);
}
.task-search__error-text--plain {
  color: var(--muted);
}
.task-search__list {
  flex: 1 1 auto;
  min-height: 0;
  margin: 0;
  padding: 0 0 4px;
  overflow-y: auto;
  list-style: none;
}
.search-file__lines {
  margin: 0;
  padding: 0;
  list-style: none;
}
.search-file,
.search-line {
  display: flex;
  align-items: center;
  width: 100%;
  text-align: left;
  cursor: pointer;
  background: transparent;
  border: none;
}
.search-file:hover,
.search-line:hover {
  background: var(--fill-2);
}
.search-file:focus-visible,
.search-line:focus-visible {
  outline: 2px solid var(--focus-ring);
  outline-offset: -2px;
}
.search-file {
  gap: 6px;
  height: 24px;
  padding: 0 12px;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--text);
}
.search-file__name {
  flex: none;
  max-width: 60%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.search-file__folder {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  color: var(--muted);
  text-overflow: ellipsis;
  white-space: nowrap;
}
.search-file__count {
  flex: none;
  margin-left: auto;
  font-family: var(--font-mono);
  font-size: 13px;
  font-variant-numeric: tabular-nums;
  color: var(--muted);
}
/* A line sits under its file's name, past the kind mark (12 + 16 + 6). */
.search-line {
  display: block;
  height: 22px;
  padding: 0 12px 0 34px;
  overflow: hidden;
  font-family: var(--font-mono);
  font-size: 13px;
  line-height: 22px;
  color: var(--muted);
  text-overflow: ellipsis;
  white-space: pre;
  tab-size: 2;
}
.search-line--on,
.search-line--on:hover {
  color: var(--ink);
  background: var(--line-2);
}
.search-line__hit {
  color: var(--accent-ink);
  background: var(--accent-wash);
}
</style>
