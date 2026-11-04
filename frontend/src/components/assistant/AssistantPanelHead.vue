<template>
  <!-- 浮层上这一行就是拖把：按住它拖面板，双击回右下角。停在右边时这两样都不做——
       那时它只是一行标题。 -->
  <!-- eslint-disable-next-line vuejs-accessibility/no-static-element-interactions -- on a floating panel the header is the drag handle, as on any window; double-click puts it back in the corner. Both move the panel, neither is a control, and the panel's own buttons keep their keyboard paths. -->
  <header
    class="aph"
    :class="{ 'aph--draggable': draggable }"
    :title="draggable ? t('tasks.assistant.dragHint') : undefined"
    @pointerdown="onDown"
    @dblclick="draggable && emit('recenter')"
  >
    <span class="aph__mark" aria-hidden="true"><CheeseAvatar :size="22" :state="busy ? 'think' : null" /></span>
    <span class="aph__name">{{ t('work.agentAvatar.defaultName') }}</span>
    <span class="aph__grow" />
    <BaseButton
      icon="mdi-pencil-outline"
      size="sm"
      :disabled="busy || newTopicDisabled"
      :title="t('tasks.assistant.newTopic')"
      :aria-label="t('tasks.assistant.newTopic')"
      @click="!busy && emit('new')"
    />
    <BaseButton
      v-if="dockable"
      :icon="docked ? 'mdi-dock-window' : 'mdi-dock-right'"
      size="sm"
      :title="docked ? t('tasks.assistant.undock') : t('tasks.assistant.dock')"
      :aria-label="docked ? t('tasks.assistant.undock') : t('tasks.assistant.dock')"
      @click="emit('dock', !docked)"
    />
    <BaseButton
      v-if="closable"
      icon="mdi-close"
      size="sm"
      :title="t('tasks.assistant.close')"
      :aria-label="t('tasks.assistant.close')"
      @click="emit('close')"
    />
  </header>
</template>

<script setup lang="ts">
// 面板的头部：是谁在答、开新话题、固定到右侧 / 取消固定、关上。取消固定之后整块面板是一张
// 能拖的浮层，拖把就是这一行——按在行里的接钮上不算拖。
import { useI18n } from 'vue-i18n'

import BaseButton from '@/components/base/BaseButton.vue'
import CheeseAvatar from '@/components/CheeseAvatar.vue'

const props = withDefaults(
  defineProps<{
    /** 芝士正在答：别处不能用，头像在转。 */
    busy: boolean
    /** 这一轮还没说完、或者一句话都还没说过：「新话题」没有可分开的东西。 */
    newTopicDisabled?: boolean
    closable?: boolean
    /** 桌面上能停在右边：这一行里给一颗「固定到右侧」或「取消固定」。 */
    dockable?: boolean
    docked?: boolean
    /** 取消固定之后是一块能挪的浮层：这一行是拖把。 */
    draggable?: boolean
  }>(),
  { newTopicDisabled: true, closable: true, dockable: false, docked: false, draggable: false }
)

const emit = defineEmits<{
  new: []
  dock: [docked: boolean]
  close: []
  /** 按住这一行拖了起来（只有浮层能拖）：位置由外面那头管。 */
  dragStart: [event: PointerEvent]
  /** 双击这一行：回到默认的角落。 */
  recenter: []
}>()

const { t } = useI18n()

/** 按下的是这一行里的按钮时不算拖：那是点按钮，不是拖面板。 */
function onDown(event: PointerEvent) {
  if (!props.draggable) return
  if ((event.target as Element | null)?.closest?.('button, a[href], input, textarea, [role="button"]')) return
  emit('dragStart', event)
}
</script>

<style scoped>
.aph {
  display: flex;
  gap: 4px;
  align-items: center;
  height: 48px;
  padding: 0 8px 0 12px;
  border-bottom: 1px solid var(--line);
}

/* 浮层：这一行是拖把。`touch-action: none` 让触屏上的拖动交给这里，而不是滚页面。 */
.aph--draggable {
  cursor: grab;
  touch-action: none;
  user-select: none;
}

.aph--draggable:active {
  cursor: grabbing;
}

.aph__mark {
  display: inline-flex;
  flex-shrink: 0;
}

.aph__name {
  padding: 0 6px;
  font-size: 14px;
  font-weight: 600;
  line-height: var(--lh-14);
  color: var(--ink);
  white-space: nowrap;
}

.aph__grow {
  flex: 1;
}
</style>
