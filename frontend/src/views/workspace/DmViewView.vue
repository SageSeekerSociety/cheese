<script setup lang="ts">
// 私聊页的画面：左边对话，右边面板（#3828），中间一条可拖的分隔；面板可以收起、浮在上
// 面，或者铺满、把对话收成左边一条窄边。和频道页同一套（`topic-panes.css`）。
//
// 对话和面板是两个插槽，由页面（`DmView.vue`）放进来；这里只管怎么摆。宽度、开合、
// 专注模式这些状态也在页面那一层，这里收 props、往上报动作。手机上没有左右两栏：对话
// 是面板页签栏的第一格，页面把它放在面板里，这里只画面板。
import type { StyleValue } from 'vue'
import type { PanelTab } from '@/components/panels/PanelTabs.vue'

import { computed, ref } from 'vue'

import BaseButton from '@/components/base/BaseButton.vue'
import PanelTabs from '@/components/panels/PanelTabs.vue'
import { t } from '@/i18n'

const props = defineProps<{
  /** 取这段私聊失败时的那句话。 */
  error: string | null
  /** 私聊已经取到：没取到之前什么也不画。 */
  ready: boolean
  /** 桌面宽度：左右两栏。否则只有面板。 */
  desktop: boolean
  /** 面板并排开着（不是浮层，也没收起）。 */
  docked: boolean
  panelOpen: boolean
  /** 窄档：面板是从右边拉进来的浮层。 */
  panelFloat: boolean
  /** 专注模式：面板铺满，对话收成一条窄边。 */
  focusMode: boolean
  /** 对话收着的时候有没有新回复。 */
  chatNews: boolean
  focusLabel: string
  focusIcon: string
  /** 对话那一栏的宽度。 */
  chatStyle: StyleValue
}>()

const emit = defineEmits<{
  'toggle-focus': []
  'close-panel': []
  /** 按下分隔线开始拖。 */
  'start-drag': [event: MouseEvent]
  /** 双击分隔线：回到对半分。 */
  'reset-split': []
  /** 对话栏进出时把里面钉在落定的宽度上（`useTopicPanes.freezeChatWidth`）。 */
  'freeze-chat': [el: Element]
}>()

defineSlots<{ chat?: () => unknown; panel?: () => unknown }>()

// 面板只有「文件」一格；手机上没有左右两栏，对话是这条页签栏的第一格。
const FILES: PanelTab = { key: 'files', label: t('work.room.tabs.files'), icon: 'mdi-paperclip' }
const CHAT: PanelTab = { key: 'chat', label: t('work.room.tabs.chat'), icon: 'mdi-message-outline' }
const phoneTab = ref<'chat' | 'files'>('chat')
const tabs = computed(() => (props.desktop ? [FILES] : [CHAT, FILES]))
const active = computed(() => (props.desktop ? 'files' : phoneTab.value))
function select(key: string) {
  if (key === 'chat' || key === 'files') phoneTab.value = key
}
const closeLabel = t('work.room.panel.closeFiles')

// 面板开合按这一块的宽度分档（`useTopicPanel`），页面从这里拿到它。
const panes = ref<HTMLElement | null>(null)
/** 页面在对话里点开一份文件时叫它：手机上切到「文件」那一格。 */
function showFiles() {
  phoneTab.value = 'files'
}
defineExpose({ panes, showFiles })
</script>

<template>
  <div class="dm-view d-flex flex-column fill-height" :class="{ 'dm-view--desktop': desktop }" style="min-width: 0">
    <v-alert v-if="error" type="error" density="comfortable" class="ma-3">
      {{ error }}
    </v-alert>
    <div
      v-else-if="ready"
      ref="panes"
      class="panes d-flex flex-grow-1"
      style="min-width: 0; min-height: 0; position: relative"
    >
      <Transition
        name="focus-chat"
        @before-enter="emit('freeze-chat', $event)"
        @before-leave="emit('freeze-chat', $event)"
      >
        <div v-if="desktop" v-show="!focusMode" class="col col-chat dm-view__chat" :style="chatStyle">
          <slot name="chat" />
        </div>
      </Transition>
      <div v-if="desktop && focusMode" class="chat-rail">
        <BaseButton
          icon="mdi-message-outline"
          size="sm"
          :title="focusLabel"
          :aria-label="focusLabel"
          @click="emit('toggle-focus')"
        />
        <span v-if="chatNews" class="chat-rail__news" aria-hidden="true" />
      </div>
      <!-- eslint-disable-next-line vuejs-accessibility/no-static-element-interactions -- a drag handle, as on the channel page; without it the split stays where it is -->
      <div
        v-if="desktop && !focusMode && docked"
        class="pane-resizer"
        :title="t('work.topic.resize')"
        @mousedown.prevent="emit('start-drag', $event)"
        @dblclick="emit('reset-split')"
      />
      <Transition name="panel-scrim">
        <!-- eslint-disable-next-line vuejs-accessibility/click-events-have-key-events, vuejs-accessibility/no-static-element-interactions -- Esc closes the sheet too (useEscapeLayer) -->
        <div v-if="panelFloat && panelOpen" class="panel-scrim" @click="emit('close-panel')" />
      </Transition>
      <div
        v-show="!desktop || panelFloat || panelOpen"
        id="topic-panel"
        class="col col-doc panel-host"
        :class="{ 'panel-host--sheet': panelFloat, 'panel-host--open': panelFloat && panelOpen }"
        :style="panelFloat ? undefined : { flex: '1 1 0', minWidth: 0 }"
      >
        <PanelTabs :tabs="tabs" :active="active" :phone="!desktop" panel-id="dm-panel-body" @select="select">
          <template v-if="desktop && docked" #actions>
            <BaseButton
              :icon="focusIcon"
              size="sm"
              :title="focusLabel"
              :aria-label="focusLabel"
              @click="emit('toggle-focus')"
            />
            <BaseButton
              icon="mdi-close"
              size="sm"
              :title="closeLabel"
              :aria-label="closeLabel"
              @click="emit('close-panel')"
            />
          </template>
        </PanelTabs>
        <div id="dm-panel-body" class="dm-view__body" role="tabpanel">
          <slot v-if="!desktop && active === 'chat'" name="chat" />
          <slot v-else name="panel" />
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.dm-view {
  flex: 1 1 auto;
}

/* 对话的页头和面板的页签栏并排，都和侧栏的头一样高（`--app-page-header-height`，频道页
   的页头用的也是它），底下那条线连成一条。 */
.dm-view--desktop {
  --dm-head-h: var(--app-page-header-height);
}
.dm-view--desktop :deep(.pr-header) {
  box-sizing: border-box;
  height: var(--dm-head-h);
}
/* 不用 d-flex：那是 `!important` 的 display，会压过 v-show。 */
.dm-view__chat {
  display: flex;
  flex-direction: column;
}

.dm-view__body {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  min-height: 0;
}
.dm-view--desktop :deep(.tabbar-host) {
  box-sizing: border-box;
  height: var(--dm-head-h);
}
</style>

<style scoped src="./topic-panes.css"></style>
