<template>
  <!-- Drawn over the form, in the form's own coordinates: 芝士's one pointer, the field it is
       writing, and the fields it filled. Nothing here takes a click but the undo buttons. -->
  <div class="al" aria-hidden="false">
    <div
      v-if="outline"
      class="al__outline"
      :class="{ 'al__outline--glide': gliding }"
      :style="{ left: `${outline.x}px`, top: `${outline.y}px`, width: `${outline.w}px`, height: `${outline.h}px` }"
    />

    <!-- What 芝士 is about to write over: the text it selected, before it types the new. -->
    <div
      v-for="(box, i) in selection"
      :key="`sel-${i}`"
      class="al__sel"
      :style="{ left: `${box.x}px`, top: `${box.y}px`, width: `${box.w}px`, height: `${box.h}px` }"
    />

    <div
      v-for="mark in marks"
      :key="mark.key"
      class="al__mark"
      :style="{ left: `${mark.x}px`, top: `${mark.y}px` }"
      :data-testid="`agent-mark-${mark.key}`"
    >
      <CheeseAvatar :size="14" aria-hidden="true" />
      <span>{{ t('tasks.agentLayer.filled') }}</span>
      <button
        type="button"
        class="al__undo"
        :aria-label="t('tasks.assistant.changes.undoOne', { field: mark.label })"
        @click="emit('undo', mark.key)"
      >
        {{ t('tasks.agentLayer.undo') }}
      </button>
    </div>

    <div
      v-if="pointer"
      class="al__pointer"
      :class="`al__pointer--${pointer.mode}`"
      :style="{ transform: `translate(${pointer.x}px, ${pointer.y}px)` }"
      aria-hidden="true"
    >
      <svg v-if="pointer.mode === 'arrow'" class="al__arrow" width="18" height="18" viewBox="0 0 18 18">
        <path d="M2 1.5 L15.5 8.2 L9.2 9.6 L6.4 15.8 Z" />
      </svg>
      <span v-else class="al__caret" :style="{ height: `${pointer.height ?? 20}px` }" />
      <span class="al__name">{{ name }}</span>
    </div>
  </div>
</template>

<script setup lang="ts">
// 芝士在表单上的那一层（发布页、改题页）：整张表单只有一个指针。去点选项时是箭头，
// 滑过去；点进要打字的格子后变成带名字的文字光标，跟着字走。正在写的那一格描一圈，
// 写过的格子标「芝士已填写」，旁边能撤销。要覆盖原来的字时，先把那段字选中一下（同一个
// 颜色），再换上新的。
//
// 位置都由外面量好交进来（相对这一层的左上角）；这里只画、只往上报撤销。
import { onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import CheeseAvatar from '@/components/CheeseAvatar.vue'

/** 和 `--dur-base` 一样长。 */
const GLIDE_MS = 220

export interface AgentPointer {
  x: number
  y: number
  mode: 'arrow' | 'caret'
  /** 文字光标的高度：那一行字有多高。 */
  height?: number
}

export interface AgentBox {
  x: number
  y: number
  w: number
  h: number
  /** 描的是哪一格：换了格子时描边滑过去，同一格里长高、挪动时立刻跟上。 */
  key?: string
}

export interface AgentMark {
  /** 芝士的字段名。 */
  key: string
  label: string
  x: number
  y: number
}

const props = withDefaults(
  defineProps<{
    pointer: AgentPointer | null
    outline: AgentBox | null
    marks: AgentMark[]
    /** 芝士选中、要换掉的那段字（每行一块）。 */
    selection?: AgentBox[]
    /** 光标旁边写的名字。 */
    name: string
  }>(),
  { selection: () => [] }
)

const emit = defineEmits<{ undo: [key: string] }>()

const { t } = useI18n()

/** 描边正从一格滑到另一格。只在这时带过渡：同一格里每一帧都在重量，带过渡就会慢半拍。 */
const gliding = ref(false)
let glideTimer: ReturnType<typeof setTimeout> | null = null
watch(
  () => props.outline?.key,
  (now, was) => {
    if (!now || !was || now === was) return
    gliding.value = true
    if (glideTimer) clearTimeout(glideTimer)
    glideTimer = setTimeout(() => (gliding.value = false), GLIDE_MS)
  }
)
onBeforeUnmount(() => {
  if (glideTimer) clearTimeout(glideTimer)
})
</script>

<style scoped>
.al {
  position: absolute;
  inset: 0;
  z-index: 3;
  pointer-events: none;
}

.al__outline {
  position: absolute;
  border: 2px solid var(--accent);
  border-radius: var(--radius-md);
  box-shadow: 0 0 0 4px var(--accent-wash);
}

.al__outline--glide {
  transition:
    left var(--dur-base) var(--ease-standard),
    top var(--dur-base) var(--ease-standard),
    width var(--dur-base) var(--ease-standard),
    height var(--dur-base) var(--ease-standard);
}

.al__sel {
  position: absolute;
  background: color-mix(in srgb, var(--agent-tone-0) 22%, transparent);
  border-radius: 2px;
}

.al__mark {
  position: absolute;
  display: inline-flex;
  gap: 6px;
  align-items: center;
  padding: 0 2px 0 4px;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
  white-space: nowrap;
  transform: translateX(-100%);
  pointer-events: auto;
}

.al__undo {
  padding: 0 2px;
  color: var(--accent-ink);
  border-radius: var(--radius-sm);
}

.al__undo:hover {
  text-decoration: underline;
}

.al__pointer {
  position: absolute;
  top: 0;
  left: 0;
  display: flex;
  align-items: flex-start;
  transition: transform var(--dur-slow) var(--ease-standard);
}

/* While typing the caret is measured every frame and sits at the end of the words, as a text
   caret does; easing it would leave it behind the text it follows. */
.al__pointer--caret {
  transition: none;
}

.al__arrow path {
  fill: var(--agent-tone-0);
  stroke: var(--surface);
  stroke-width: 1.2;
}

.al__caret {
  display: block;
  width: 2px;
  background: var(--agent-tone-0);
  border-radius: 1px;
}

.al__name {
  padding: 1px 6px;
  margin-top: -14px;
  margin-left: 2px;
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--agent-ink);
  white-space: nowrap;
  background: var(--agent-tone-0);
  border-radius: var(--radius-sm);
}

.al__pointer--arrow .al__name {
  margin-top: 14px;
  margin-left: -2px;
}

@media (prefers-reduced-motion: reduce) {
  .al__pointer,
  .al__outline {
    transition: none;
  }
}
</style>
