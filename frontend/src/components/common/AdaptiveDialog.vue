<script setup lang="ts">
// 一张表单弹窗：桌面上是居中的对话框，手机上（< 960px）是一整页。
//
//   <AdaptiveDialog v-model="open" title="接入邮箱" primary-label="接入"
//                   :primary-loading="saving" :primary-disabled="!valid" @primary="save">
//     ……表单……
//   </AdaptiveDialog>
//
// 手机上那一页自带页头：左边 ✕ 关上，中间标题，右边主操作（给了 primary-icon 就画
// 图标，否则画字）。页头钉住、正文自己滚，所以键盘弹起来时「保存」不会被压到键盘
// 底下；整页高度和外壳一样扣掉键盘（--app-height − --keyboard-inset）。打开时从底下
// 升上来（--dur-base），减弱动效时直接出现。
//
// 桌面上是标题、正文、底部一行「取消 + 主操作」。主操作按钮是这一组里唯一的琥珀。
import { useDisplay } from 'vuetify'

import { t } from '@/i18n'

const open = defineModel<boolean>({ default: false })

const props = withDefaults(
  defineProps<{
    title: string
    /** 主操作的字，例如「保存」。不给就没有主操作，只能关上。 */
    primaryLabel?: string
    /** 手机页头上主操作画成图标时用的 mdi 名；不给就画字。 */
    primaryIcon?: string
    primaryLoading?: boolean
    primaryDisabled?: boolean
    /** 桌面上关闭按钮的字，默认「取消」。 */
    cancelLabel?: string
    /** 桌面宽度。 */
    maxWidth?: number | string
    /** 点遮罩、按 Esc 不关（表单填到一半时）。 */
    persistent?: boolean
  }>(),
  {
    primaryLabel: undefined,
    primaryIcon: undefined,
    primaryLoading: false,
    primaryDisabled: false,
    cancelLabel: undefined,
    maxWidth: 480,
    persistent: false,
  }
)

const emit = defineEmits<{ primary: [] }>()

defineSlots<{
  default?: () => unknown
}>()

const { mdAndUp } = useDisplay()

function close() {
  open.value = false
}

function primary() {
  if (props.primaryDisabled || props.primaryLoading) return
  emit('primary')
}
</script>

<template>
  <v-dialog v-if="mdAndUp" v-model="open" :max-width="props.maxWidth" :persistent="props.persistent" scrollable>
    <v-card rounded="lg">
      <v-card-title class="t-dialog-title">{{ props.title }}</v-card-title>
      <v-card-text class="adaptive-dialog__desktop-body"><slot /></v-card-text>
      <v-card-actions class="px-4 pb-3">
        <v-spacer />
        <v-btn variant="text" @click="close">{{ props.cancelLabel ?? t('global.cancel') }}</v-btn>
        <v-btn
          v-if="props.primaryLabel"
          color="primary"
          variant="flat"
          :loading="props.primaryLoading"
          :disabled="props.primaryDisabled"
          @click="primary"
        >
          {{ props.primaryLabel }}
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>

  <v-dialog
    v-else
    v-model="open"
    fullscreen
    :persistent="props.persistent"
    transition="adaptive-dialog-page"
    content-class="adaptive-dialog__page-content"
  >
    <div class="adaptive-dialog__page" role="document">
      <header class="adaptive-dialog__head">
        <v-btn
          icon
          variant="text"
          color="on-surface-variant"
          size="44"
          :aria-label="t('navigation.shell.close')"
          :title="t('navigation.shell.close')"
          @click="close"
        >
          <v-icon size="22">mdi-close</v-icon>
        </v-btn>
        <h2 class="adaptive-dialog__title t-title">{{ props.title }}</h2>
        <template v-if="props.primaryLabel">
          <v-btn
            v-if="props.primaryIcon"
            icon
            variant="text"
            color="primary"
            size="44"
            :loading="props.primaryLoading"
            :disabled="props.primaryDisabled"
            :aria-label="props.primaryLabel"
            :title="props.primaryLabel"
            @click="primary"
          >
            <v-icon size="22">{{ props.primaryIcon }}</v-icon>
          </v-btn>
          <v-btn
            v-else
            variant="text"
            color="primary"
            class="adaptive-dialog__primary"
            :loading="props.primaryLoading"
            :disabled="props.primaryDisabled"
            @click="primary"
          >
            {{ props.primaryLabel }}
          </v-btn>
        </template>
      </header>
      <div class="adaptive-dialog__body"><slot /></div>
    </div>
  </v-dialog>
</template>

<style scoped>
/* 第一个字段的浮动标签探出自己的盒子一半（frontend.md「A field's label sits OUTSIDE
   its box」），滚动容器的上沿要留出它。 */
.adaptive-dialog__desktop-body {
  padding-top: 12px;
}
.adaptive-dialog__page {
  display: flex;
  flex-direction: column;
  height: calc(var(--app-height, 100dvh) - var(--keyboard-inset, 0px));
  background: var(--surface);
}
.adaptive-dialog__head {
  display: flex;
  flex: none;
  align-items: center;
  gap: 4px;
  height: 56px;
  padding: 0 4px;
  padding-top: env(safe-area-inset-top);
  box-sizing: content-box;
  border-bottom: 1px solid var(--line);
}
.adaptive-dialog__title {
  flex: 1 1 auto;
  min-width: 0;
  margin: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.adaptive-dialog__primary {
  min-height: 44px;
  font-size: 15px;
  font-weight: 600;
}
.adaptive-dialog__body {
  flex: 1 1 auto;
  min-height: 0;
  padding: 16px 16px calc(24px + env(safe-area-inset-bottom));
  overflow-y: auto;
  overscroll-behavior: contain;
}
</style>

<style>
/* 整页从底下升上来。v-dialog 把内容传送到 body 下，这几条写不进 scoped。 */
.adaptive-dialog__page-content.adaptive-dialog-page-enter-active {
  transition: transform var(--dur-base) var(--ease-standard);
}
.adaptive-dialog__page-content.adaptive-dialog-page-leave-active {
  transition: transform var(--dur-quick) var(--ease-in);
}
.adaptive-dialog__page-content.adaptive-dialog-page-enter-from,
.adaptive-dialog__page-content.adaptive-dialog-page-leave-to {
  transform: translateY(100%);
}
@media (prefers-reduced-motion: reduce) {
  .adaptive-dialog__page-content.adaptive-dialog-page-enter-active,
  .adaptive-dialog__page-content.adaptive-dialog-page-leave-active {
    transition: none;
  }
}
</style>
