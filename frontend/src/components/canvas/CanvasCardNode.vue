<script setup lang="ts">
// 画布上的一个格子：一段 Markdown，可以关联一个任务。
//
// 双击就地改字：格子变成一块和它一样大的输入区，点到别处或按 ⌘↵ 存下，Esc 放弃。
// 关联了任务的格子顶上一行是任务的状态圆点和标题，点标题去任务那里。改了什么都往上
// 发，存不存、怎么存是画布那一层的事；连线的圆点和改大小的边角也是画布那一层加的。
import type { BoardColumn, BoardPhrase } from '@/cx_types'
import type { CanvasCard } from '@/types/canvas'

import { computed, nextTick, ref, watch } from 'vue'

import MarkdownView from '@/components/common/MarkdownView.vue'
import { t } from '@/i18n'
import { teammateName } from '@/lib/agentNames'
import { columnDotStyle, phraseLabel } from '@/lib/board'

const props = withDefaults(
  defineProps<{
    card: CanvasCard
    editable: boolean
    selected?: boolean
    /** 正在改这个格子的字（刚加出来的格子一出现就是这样）。 */
    editing?: boolean
  }>(),
  { selected: false, editing: false }
)

const emit = defineEmits<{
  (e: 'edit'): void
  (e: 'save', content: string): void
  (e: 'cancel'): void
  (e: 'remove'): void
  (e: 'open-task', taskId: string): void
}>()

const draft = ref('')
const input = ref<HTMLTextAreaElement | null>(null)

watch(
  () => props.editing,
  async (on) => {
    if (!on) return
    draft.value = props.card.content
    await nextTick()
    input.value?.focus()
    fit()
  },
  { immediate: true }
)

function fit() {
  const el = input.value
  if (!el) return
  el.style.height = 'auto'
  el.style.height = `${el.scrollHeight}px`
}

function save() {
  if (!props.editing) return
  if (draft.value === props.card.content) emit('cancel')
  else emit('save', draft.value)
}

function onKey(event: KeyboardEvent) {
  if (event.key === 'Escape') {
    event.preventDefault()
    emit('cancel')
  } else if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) {
    event.preventDefault()
    save()
  }
}

const task = computed(() => props.card.task)
/** 卡上那句状态说到的队友（「{agent}在做」）：做这件任务的那位，按读者的语言叫它。 */
const phrase = computed(() =>
  task.value
    ? phraseLabel(task.value.phrase as BoardPhrase, teammateName(task.value.agent_name, task.value.agent_name_source))
    : ''
)
const empty = computed(() => !props.card.content.trim())
</script>

<template>
  <!-- eslint-disable-next-line vuejs-accessibility/no-static-element-interactions -- the focusable element is the board's node around this card; Enter on it opens the card for writing (CanvasBoard) -->
  <div
    class="canvas-card"
    :class="{ 'canvas-card--selected': selected, 'canvas-card--editing': editing }"
    @dblclick.stop="editable && !editing && emit('edit')"
  >
    <button
      v-if="task"
      type="button"
      class="canvas-card__task nodrag"
      :title="t('work.room.doc.canvas.openTask')"
      @click.stop="emit('open-task', task.id)"
    >
      <span class="canvas-card__dot" :style="columnDotStyle(task.column as BoardColumn)" aria-hidden="true" />
      <span class="canvas-card__task-title">{{ task.title }}</span>
      <span class="canvas-card__phrase">{{ phrase }}</span>
    </button>
    <div v-else-if="card.task_hidden" class="canvas-card__task canvas-card__task--hidden">
      <span class="canvas-card__dot" aria-hidden="true" />
      <span>{{ t('work.room.doc.canvas.taskHidden') }}</span>
    </div>

    <textarea
      v-if="editing"
      ref="input"
      v-model="draft"
      autocomplete="off"
      class="canvas-card__input nodrag nowheel nopan"
      :aria-label="t('work.room.doc.canvas.cardText')"
      rows="2"
      @input="fit"
      @keydown.stop="onKey"
      @blur="save"
    />
    <p v-else-if="empty" class="canvas-card__empty">{{ t('work.room.doc.canvas.emptyCard') }}</p>
    <MarkdownView v-else class="canvas-card__body" :source="card.content" />

    <button
      v-if="editable && selected && !editing"
      type="button"
      class="canvas-card__remove nodrag"
      :aria-label="t('work.room.doc.canvas.removeCard')"
      :title="t('work.room.doc.canvas.removeCard')"
      @click.stop="emit('remove')"
    >
      <i class="mdi mdi-close" aria-hidden="true" />
    </button>
  </div>
</template>

<style scoped>
.canvas-card {
  position: relative;
  box-sizing: border-box;
  width: 100%;
  height: 100%;
  min-height: 48px;
  padding: 10px 12px;
  overflow: hidden;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-md);
  font-size: 14px;
  line-height: var(--lh-14);
  color: var(--text);
  background: var(--surface);
  transition: border-color var(--dur-quick) var(--ease-standard);
}

.canvas-card:hover {
  border-color: var(--faint);
}

.canvas-card--selected,
.canvas-card--editing {
  border-color: var(--muted);
}

.canvas-card__body :deep(> :first-child) {
  margin-top: 0;
}

.canvas-card__body :deep(> :last-child) {
  margin-bottom: 0;
}

.canvas-card__body :deep(p),
.canvas-card__body :deep(ul),
.canvas-card__body :deep(ol) {
  margin: 0 0 4px;
}

.canvas-card__body :deep(ul),
.canvas-card__body :deep(ol) {
  padding-left: 18px;
}

.canvas-card__empty {
  margin: 0;
  color: var(--faint);
}

.canvas-card__input {
  display: block;
  box-sizing: border-box;
  width: 100%;
  min-height: 40px;
  padding: 0;
  border: 0;
  outline: none;
  resize: none;
  font: inherit;
  color: var(--ink);
  background: transparent;
}

.canvas-card__task {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  margin: 0 0 6px;
  padding: 0;
  border: 0;
  font: inherit;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
  text-align: left;
  background: none;
  cursor: pointer;
}

.canvas-card__task:hover .canvas-card__task-title {
  color: var(--ink);
  text-decoration: underline;
}

.canvas-card__task--hidden {
  color: var(--faint);
  cursor: default;
}

.canvas-card__dot {
  flex: 0 0 auto;
  width: 8px;
  height: 8px;
  border: 1.5px solid var(--faint);
  border-radius: 50%;
}

.canvas-card__task-title {
  min-width: 0;
  overflow: hidden;
  font-weight: 600;
  color: var(--text);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.canvas-card__phrase {
  flex: 0 0 auto;
  color: var(--faint);
}

.canvas-card__remove {
  position: absolute;
  top: 4px;
  right: 4px;
  display: grid;
  place-items: center;
  width: 24px;
  height: 24px;
  padding: 0;
  border: 0;
  border-radius: var(--radius-sm);
  font-size: 15px;
  color: var(--faint);
  background: var(--surface);
  cursor: pointer;
}

.canvas-card__remove:hover {
  color: var(--ink);
  background: var(--fill);
}
</style>
