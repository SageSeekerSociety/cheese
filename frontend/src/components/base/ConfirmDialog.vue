<script setup lang="ts">
/**
 * 确认框：一句话、两颗按钮（docs/design-system.md §3.7）。
 *
 *   <ConfirmDialog v-model="open" title="把爱丽丝移出项目？" confirm-label="移出" danger
 *                  :loading="removing" @confirm="remove">
 *     她将看不到这个项目的话题和资料。
 *   </ConfirmDialog>
 *
 * 和表单弹窗（AdaptiveDialog）的分别：
 * - 桌面和手机都是居中的小框（420px），不变成整页（§10.5：一两句话的确认框不在此列）。
 * - 没有右上角 ✕，点遮罩和按 Esc 也不关：要人在两颗按钮里明确选一颗。
 * - 确认键的字写动作本身（「移出」「删除」「替换」），不写「确定」。
 * - `danger` 时确认键是实心红（BaseButton kind="danger" solid），否则是琥珀主操作。
 */
import { useFocusReturn } from '@/composables/useFocusReturn'

import BaseButton from './BaseButton.vue'
import { DIALOG_WIDTH } from './dialogSize'

import { t } from '@/i18n'

const open = defineModel<boolean>({ default: false })

const props = withDefaults(
  defineProps<{
    title: string
    /** 确认键的字：动词，说清点下去会发生什么。 */
    confirmLabel: string
    cancelLabel?: string
    danger?: boolean
    loading?: boolean
    disabled?: boolean
  }>(),
  { cancelLabel: undefined, danger: false, loading: false, disabled: false }
)

const emit = defineEmits<{ confirm: []; cancel: [] }>()

defineSlots<{ default?: () => unknown }>()

// 和 AdaptiveDialog 一样：没有 activator 的 v-dialog，Vuetify 不会在关掉时还焦点。
// 还给打开确认框的那颗按钮，按钮没了就退到主内容区。
useFocusReturn(open, () => document.getElementById('main-content'))

function cancel() {
  if (props.loading) return
  open.value = false
  emit('cancel')
}

function confirm() {
  if (props.disabled || props.loading) return
  emit('confirm')
}
</script>

<template>
  <!-- eslint-disable-next-line vue/no-restricted-syntax -- the shared confirm primitive itself (design-system §3.7) -->
  <v-dialog v-model="open" :max-width="DIALOG_WIDTH.sm" persistent>
    <v-card rounded="lg" class="confirm-dialog" role="alertdialog" :aria-label="props.title">
      <v-card-title class="t-dialog-title confirm-dialog__title">{{ props.title }}</v-card-title>
      <v-card-text v-if="$slots.default" class="t-body-readable confirm-dialog__body"><slot /></v-card-text>
      <v-card-actions class="confirm-dialog__actions">
        <BaseButton kind="ghost" :disabled="props.loading" @click="cancel">{{
          props.cancelLabel ?? t('global.cancel')
        }}</BaseButton>
        <BaseButton
          :kind="props.danger ? 'danger' : 'primary'"
          :solid="props.danger"
          :loading="props.loading"
          :disabled="props.disabled"
          @click="confirm"
        >
          {{ props.confirmLabel }}
        </BaseButton>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<style scoped>
.confirm-dialog__title {
  padding: 20px 24px 8px;
  white-space: normal;
}
.confirm-dialog__body {
  padding: 0 24px 8px;
  color: var(--text);
}
.confirm-dialog__actions {
  justify-content: flex-end;
  gap: 8px;
  padding: 12px 16px 16px;
}
</style>
