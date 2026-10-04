<script setup lang="ts">
import { useI18n } from 'vue-i18n'

import ConfirmDialog from '@/components/base/ConfirmDialog.vue'

const { t } = useI18n()

// 「视频链接提示」那一段：填了一个解析不了的地址时弹出来问一句 —— 现在只有 B 站能
// 嵌着放，别的站存下来就是一条点开能看、但题面里放不出来的链接。
//
// 它只画这一段话，点哪颗按钮往外报一声（`confirm` 继续保存 / `cancel` 不存了）。
// 判断那个地址算不算数、以及这份提交随后怎么办，都在 `useTaskForm.ts` 里（第二道
// 闸门），所以这一件自己不知道什么是 Bilibili。
const open = defineModel<boolean>('open', { required: true })

const emit = defineEmits<{
  (e: 'confirm'): void
  (e: 'cancel'): void
}>()
</script>

<template>
  <ConfirmDialog
    v-model="open"
    :title="t('tasks.form.video.dialogTitle')"
    :confirm-label="t('tasks.form.video.dialogContinue')"
    :cancel-label="t('global.cancel')"
    @confirm="emit('confirm')"
    @cancel="emit('cancel')"
  >
    <v-alert type="warning" variant="tonal" class="mb-0">
      {{ t('tasks.form.video.dialogBody') }}
    </v-alert>
  </ConfirmDialog>
</template>
