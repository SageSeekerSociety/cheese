<template>
  <div>
    <v-dialog
      v-for="dialog in dialogs"
      :key="dialog.id"
      v-model="dialog.isOpen"
      :max-width="DIALOG_WIDTH.sm"
      :persistent="dialog.showCancel"
      @update:model-value="onDialogClose(dialog.id, $event)"
    >
      <v-card rounded="lg">
        <v-card-title class="t-dialog-title">{{ dialog.title }}</v-card-title>
        <v-card-text class="t-body-readable">
          <component :is="dialog.content" v-if="typeof dialog.content === 'function'" />
          <span v-else>{{ dialog.content }}</span>
        </v-card-text>
        <v-card-actions>
          <v-spacer></v-spacer>
          <BaseButton v-if="dialog.showCancel" kind="ghost" @click="onCancel(dialog)">{{
            t('global.cancel')
          }}</BaseButton>
          <BaseButton :kind="dialog.danger ? 'danger' : 'primary'" :solid="dialog.danger" @click="onConfirm(dialog)">{{
            dialog.confirmLabel ?? t('shell.dialog.ok')
          }}</BaseButton>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </div>
</template>

<script lang="ts" setup>
import type { DialogInstance } from '@/plugins/dialog'

import BaseButton from '@/components/base/BaseButton.vue'
import { DIALOG_WIDTH } from '@/components/base/dialogSize'
import { t } from '@/i18n'
import { closeDialog, dialogs } from '@/plugins/dialog'

const onDialogClose = (id: number, isOpen: boolean) => {
  if (!isOpen) {
    closeDialog(id, false)
  }
}

const onConfirm = (dialog: DialogInstance) => {
  const result = dialog.onConfirm ? dialog.onConfirm() : true
  closeDialog(dialog.id, result)
}

// 取消是「不做」这个答案，不是一次失败。`confirm()` 早就把答案写好了
// （`onCancel: () => false`），这里就把它交回去；从前的写法是把它整个丢掉、改成
// reject 一个 `CancelError`，而绝大多数调用点写的是 `if (!confirmed) return` ——
// 没人接的那个 reject 一路冒到场景的兜底（`views/spaces/Detail.vue` 外的
// `ErrorBoundary`），整块内容被换成「无法显示这块内容」：点一下「取消」就得重试才
// 能接着看这一页。只有没写 `onCancel` 的那几种（`prompt`、`custom`）仍然 reject：
// 它们要分得出「取消了」和「交上来一个空值」。
const onCancel = (dialog: DialogInstance) => {
  if (dialog.onCancel) {
    closeDialog(dialog.id, dialog.onCancel())
    return
  }
  closeDialog(dialog.id, false, true)
}
</script>
