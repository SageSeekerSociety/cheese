<script setup lang="ts">
// 私密频道和私聊里，保存到资料库之前说清楚：现在只有这里的几个人看得到，保存后项目里的
// 人都看得到（#3828）。「文件」那一格和消息的 ⋯ 菜单用的是这同一个确认；两个人数由外面
// 算好交下来（`useKeepAudience`）。
import { computed } from 'vue'

import ConfirmDialog from '@/components/base/ConfirmDialog.vue'
import { t } from '@/i18n'

const props = defineProps<{
  /** 要保存的那一份叫什么；空 = 弹窗关着。 */
  name: string | null
  /** 现在看得到的人数。 */
  people: number
  /** 保存后看得到的人数。 */
  projectPeople: number
}>()

const emit = defineEmits<{
  confirm: []
  cancel: []
}>()

const open = computed({
  get: () => props.name !== null,
  set: (value: boolean) => {
    if (!value) emit('cancel')
  },
})
</script>

<template>
  <ConfirmDialog
    v-model="open"
    :title="t('work.room.files.confirmTitle')"
    :confirm-label="t('work.room.files.keep')"
    @confirm="emit('confirm')"
  >
    <p class="t-body">
      {{ t('work.room.files.confirmBody', { name: name ?? '', people, projectPeople }) }}
    </p>
  </ConfirmDialog>
</template>
