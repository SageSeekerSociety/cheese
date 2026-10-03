<template>
  <div>
    <v-dialog
      v-for="dialog in dialogs"
      :key="dialog.id"
      v-model="dialog.isOpen"
      width="500"
      @update:model-value="onDialogClose(dialog.id, $event)"
    >
      <v-card>
        <v-card-title>{{ dialog.title }}</v-card-title>
        <v-card-text>
          <component :is="dialog.content" v-if="typeof dialog.content === 'function'" />
          <span v-else>{{ dialog.content }}</span>
        </v-card-text>
        <v-card-actions>
          <v-spacer></v-spacer>
          <BaseButton v-if="dialog.showCancel" kind="ghost" @click="onCancel(dialog)">{{
            t('global.cancel')
          }}</BaseButton>
          <BaseButton kind="primary" @click="onConfirm(dialog)">{{ t('shell.dialog.ok') }}</BaseButton>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </div>
</template>

<script lang="ts" setup>
import type { DialogInstance } from '@/plugins/dialog'

import BaseButton from '@/components/base/BaseButton.vue'
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

const onCancel = (dialog: DialogInstance) => {
  if (dialog.onCancel) {
    dialog.onCancel()
  }
  closeDialog(dialog.id, false, true)
}
</script>
