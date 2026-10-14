<script setup lang="ts">
// 用邀请码加入空间。加入之后直接进那个空间：刚加入的空间里通常还没有他的东西，
// 留在原地等于什么都没发生。
//
// 问后端、成了往哪跳，都在 `composables/useJoinSpace.ts`（首页那份目录和待办页共用
// 那一份）。这一件只收邀请码、把它报上去（A 档）：`busy` 和「上一次为什么没成」由
// 外面给。
import { ref, watch } from 'vue'

import AdaptiveDialog from '@/components/common/AdaptiveDialog.vue'
import { t } from '@/i18n'

const props = defineProps<{
  /** 正在加入：按钮转圈，按不动第二下。 */
  busy?: boolean
  /** 上一次没加进去的理由，原样画在输入框下面。 */
  error?: string
}>()
const emit = defineEmits<{ submit: [code: string] }>()

const open = defineModel<boolean>({ required: true })

const code = ref('')

// 每次打开都从空开始：上一次留下的半截邀请码不该跟到下一次。
watch(open, (now) => {
  if (now) code.value = ''
})

function submit() {
  const value = code.value.trim()
  if (!value || props.busy) return
  emit('submit', value)
}
</script>

<template>
  <AdaptiveDialog
    v-model="open"
    size="sm"
    :title="t('work.joinTitle')"
    :primary-label="t('work.joinSubmit')"
    :primary-loading="busy"
    :cancel-label="t('work.joinCancel')"
    @primary="submit"
  >
    <p class="t-body c-muted mb-3">{{ t('work.joinBody') }}</p>
    <v-text-field
      v-model="code"
      :label="t('work.joinLabel')"
      :error-messages="error"
      autocomplete="off"
      autofocus
      hide-details="auto"
      @keyup.enter="submit"
    />
  </AdaptiveDialog>
</template>
