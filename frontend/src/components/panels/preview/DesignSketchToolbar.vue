<script setup lang="ts">
import type { SketchTool } from './designSketch'
import type { ToolbarTier } from './designToolbar'

import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

import { DRAW_TOOLS, SKETCH_COLORS } from './designSketch'
import { toolbarTier } from './designToolbar'

import { t } from '@/i18n'

const props = defineProps<{
  tool: SketchTool
  color: string
  canUndo: boolean
  canRedo: boolean
  hasStrokes: boolean
  canSend: boolean
  /** 图片版本没验证过时框选没有意义，那颗按钮要跟着禁用。 */
  canSelect?: boolean
  busy?: boolean
  /** 图上正有一个文字框在编辑：这时发送是禁用的，原因要说得出。 */
  textEditing?: boolean
}>()
/** 图上的字说不清「改成什么」，所以那一句和「加入对话」摆在一起。 */
const note = defineModel<string>('note', { default: '' })
/** 区域选择是外面那颗按钮，它开着时按 Esc 取消之后焦点要回得来。 */
const selectButton = ref<HTMLButtonElement | null>(null)
defineExpose({ selectButton })
const emit = defineEmits<{
  pick: [tool: SketchTool]
  recolor: [color: string]
  undo: []
  redo: []
  clear: []
  send: []
}>()
/** 回车发送。输入法组字中的那一次回车是「选词」，不是「发送」：放它过去，
 *  否则用中文打字的人每选一个词就把标注发出去一次。 */
function onNoteEnter(event: KeyboardEvent) {
  if (event.isComposing || event.keyCode === 229) return
  event.preventDefault()
  emit('send')
}

/**
 * 三档自适配：盯自己的宽度，切 full / compact / minimal，再窄就整条收起。
 *
 * 窄面板（240px）里，中文标签比图标宽一倍多、横滚距离随之翻倍，所以窄的时候先把
 * 文字标签收掉；再窄就把颜色轮也收掉，只剩图标——这几件事各网站都用同一套图形，
 * 名字没丢，退到 `aria-label` 和 `title` 里。整条收起时（`concealed`）加 `inert`，
 * 收起来的按钮既点不到也 Tab 不到。
 */
const root = ref<HTMLElement | null>(null)
const tier = ref<ToolbarTier>('full')
let observer: ResizeObserver | null = null
onMounted(() => {
  const element = root.value
  if (!element || typeof ResizeObserver === 'undefined') return
  observer = new ResizeObserver((entries) => {
    const width = entries[0]?.contentRect?.width
    if (typeof width === 'number' && width > 0) tier.value = toolbarTier(width)
  })
  observer.observe(element)
})
onBeforeUnmount(() => observer?.disconnect())
const showLabels = computed(() => tier.value === 'full')
const showColors = computed(() => tier.value === 'full' || tier.value === 'compact')

/**
 * 禁用时 `title`/`aria-label` 要说清为什么按不动，而不是只灰着不响。
 * 「能不能按」正是这几颗按钮要回答的问题，光有一档浅灰等于没说。
 */
const selectTitle = computed(() =>
  props.canSelect === false ? t('design.regionUnavailable') : t('design.tools.select')
)
const undoTitle = computed(() => (props.canUndo ? t('design.undoShortcut') : t('design.reasonNoUndo')))
const redoTitle = computed(() => (props.canRedo ? t('design.redoShortcut') : t('design.reasonNoRedo')))
const clearTitle = computed(() => (props.hasStrokes ? t('design.clear') : t('design.reasonNoStrokes')))
const sendDisabled = computed(() => !props.canSend || !!props.busy)
const sendTitle = computed(() => {
  if (!sendDisabled.value) return t('design.addToChat')
  if (props.busy) return t('design.reasonBuilding')
  if (!props.hasStrokes) return t('design.reasonNoStrokes')
  if (props.textEditing) return t('design.reasonTextEditing')
  return t('design.reasonNoNote')
})

/**
 * 工具栏在窄档只画图标，名字一律走 `aria-label` 和 `title`；宽档（full）把工具名
 * 也摆出来，一行放得下就让人少猜图形。「加入对话」是提交动作不是工具，一直写着字。
 */
const TOOL_ICON: Record<SketchTool, string> = {
  select: 'mdi-cursor-default-outline',
  pen: 'mdi-pencil',
  line: 'mdi-vector-line',
  arrow: 'mdi-arrow-top-right',
  rect: 'mdi-rectangle-outline',
  ellipse: 'mdi-ellipse-outline',
  text: 'mdi-format-text',
  redact: 'mdi-eye-off-outline',
}
</script>

<template>
  <div
    ref="root"
    class="sketch-toolbar"
    :class="{ 'sketch-toolbar--concealed': tier === 'concealed' }"
    role="group"
    :data-tier="tier"
    :data-concealed="tier === 'concealed' ? '' : undefined"
    :inert="tier === 'concealed' || undefined"
    :aria-label="t('design.sketchTools')"
  >
    <!-- 工具、颜色、历史挤在这一段里横滚；「说一句要改什么」和「加入对话」钉在
         右边不跟着滚——它们是这一步的落点，滚出去就等于按不到。 -->
    <div class="sketch-toolbar__scroll">
      <button
        ref="selectButton"
        type="button"
        class="sketch-toolbar__tool"
        :class="{ 'is-active': props.tool === 'select' }"
        :disabled="props.canSelect === false"
        :aria-pressed="props.tool === 'select'"
        :aria-label="t('design.tools.select')"
        :title="selectTitle"
        @click="emit('pick', 'select')"
      >
        <i :class="`mdi ${TOOL_ICON.select}`" class="sketch-toolbar__icon" aria-hidden="true" />
        <span v-if="showLabels" class="sketch-toolbar__label">{{ t('design.tools.select') }}</span>
      </button>
      <button
        v-for="item in DRAW_TOOLS"
        :key="item"
        type="button"
        class="sketch-toolbar__tool"
        :class="{ 'is-active': props.tool === item }"
        :aria-pressed="props.tool === item"
        :aria-label="t(`design.tools.${item}`)"
        :title="t(`design.tools.${item}`)"
        @click="emit('pick', item)"
      >
        <i :class="`mdi ${TOOL_ICON[item]}`" class="sketch-toolbar__icon" aria-hidden="true" />
        <span v-if="showLabels" class="sketch-toolbar__label">{{ t(`design.tools.${item}`) }}</span>
      </button>
      <span class="sketch-toolbar__gap" />
      <!-- 窄到一定档就把颜色轮收掉：它一排五颗最占地方，工具和历史更要紧。 -->
      <template v-if="showColors">
        <button
          v-for="swatch in SKETCH_COLORS"
          :key="swatch"
          type="button"
          class="sketch-toolbar__color"
          :class="{ 'is-active': props.color === swatch }"
          :style="{ color: swatch }"
          :aria-pressed="props.color === swatch"
          :aria-label="t('design.sketchColor', { color: swatch })"
          @click="emit('recolor', swatch)"
        >
          ●
        </button>
      </template>
      <span class="sketch-toolbar__gap" />
      <button
        type="button"
        class="sketch-toolbar__tool"
        :disabled="!canUndo"
        aria-keyshortcuts="Meta+Z Control+Z"
        :aria-label="canUndo ? t('design.undo') : t('design.reasonNoUndo')"
        :title="undoTitle"
        @click="emit('undo')"
      >
        <i class="mdi mdi-undo sketch-toolbar__icon" aria-hidden="true" />
      </button>
      <button
        type="button"
        class="sketch-toolbar__tool"
        :disabled="!canRedo"
        aria-keyshortcuts="Meta+Shift+Z Control+Shift+Z Control+Y"
        :aria-label="canRedo ? t('design.redo') : t('design.reasonNoRedo')"
        :title="redoTitle"
        @click="emit('redo')"
      >
        <i class="mdi mdi-redo sketch-toolbar__icon" aria-hidden="true" />
      </button>
      <button
        type="button"
        class="sketch-toolbar__tool"
        :disabled="!hasStrokes"
        :aria-label="clearTitle"
        :title="clearTitle"
        @click="emit('clear')"
      >
        <i class="mdi mdi-delete-sweep-outline sketch-toolbar__icon" aria-hidden="true" />
      </button>
    </div>
    <input
      v-if="hasStrokes"
      v-model="note"
      type="text"
      class="sketch-toolbar__note"
      autocomplete="off"
      :placeholder="t('design.notePlaceholder')"
      :aria-label="t('design.notePlaceholder')"
      @keydown.enter="onNoteEnter"
    />
    <button
      type="button"
      class="is-primary"
      :disabled="sendDisabled"
      :aria-label="t('design.addToChat')"
      :title="sendTitle"
      @click="emit('send')"
    >
      {{ t('design.addToChat') }}
    </button>
  </div>
</template>

<style scoped>
.sketch-toolbar {
  display: flex;
  /* 窄面板（240px）里让它换行会占掉三四行，图就没地方了——排成一行，超出的横滚。 */
  flex-wrap: nowrap;
  align-items: center;
  gap: 4px;
  padding: 4px 8px;
  border-bottom: 1px solid var(--line);
}
/* 只有这一段横滚。右边那两样（说明和提交）留在外面，窄面板里也一直看得见。 */
.sketch-toolbar__scroll {
  display: flex;
  flex: 1 1 auto;
  min-width: 0;
  align-items: center;
  gap: 4px;
  overflow-x: auto;
}
.sketch-toolbar__gap {
  width: 8px;
}
/* 宽档（full）才摆工具名；窄档收进 aria-label / title，横滚距离不至于翻倍。 */
.sketch-toolbar__label {
  font-size: 13px;
}
/* 整条收起（concealed）：inert 之外再淡出，收起的过程看得见它是收起了而不是坏了。 */
.sketch-toolbar--concealed {
  opacity: 0;
  transition: opacity var(--dur-base) var(--ease-standard);
}
.sketch-toolbar button {
  padding: 4px 8px;
  border-radius: var(--radius-sm);
  font-size: 13px;
  line-height: var(--lh-13);
  white-space: nowrap;
}
.sketch-toolbar button:hover:not(:disabled) {
  background: var(--fill-2);
}
/* 禁用态光靠颜色深浅分不出来：#747a82 的灰和正文的灰在窄条上差别很小，而这几颗
 * 按钮「现在能不能按」正是撤销/重做要回答的问题。淡到看得见的一档，加上默认光标。 */
.sketch-toolbar button:disabled {
  color: var(--faint);
  opacity: 0.45;
  cursor: default;
}
.sketch-toolbar button.is-active {
  background: var(--fill-2);
  color: var(--accent);
}
.sketch-toolbar button.is-primary {
  flex: 0 0 auto;
  background: var(--accent);
  /* 和发送键同一对：琥珀底上的字 */
  color: rgb(var(--v-theme-on-primary));
}
.sketch-toolbar button.is-primary:disabled {
  background: var(--fill-2);
  color: var(--faint);
}
.sketch-toolbar__tool {
  display: inline-flex;
  align-items: center;
}
/* 图标是字体字形，尺寸跟着 font-size 走；不设为 1 的话行高会把这一条顶高几像素。 */
.sketch-toolbar__icon {
  font-size: 18px;
  line-height: 1;
}
.sketch-toolbar__color {
  font-size: 15px;
  line-height: 1;
}
.sketch-toolbar__color.is-active {
  outline: 2px solid var(--accent);
}
.sketch-toolbar__note {
  /* 宽的时候 160px，窄的时候让给提交键——它有下界，不会被压没。 */
  flex: 0 1 160px;
  min-width: 88px;
  padding: 4px 8px;
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  background: transparent;
  color: inherit;
  font-size: 13px;
  line-height: var(--lh-13);
}
.sketch-toolbar__note:focus-visible {
  outline: 2px solid var(--accent);
}
</style>
