<script setup lang="ts">
// 用邀请码加入空间。加入之后直接进那个空间：刚加入的空间里通常还没有他的东西，
// 留在原地等于什么都没发生。
//
// 「进哪儿」是页面的事，不是这里的：这一只只把码报上去（`submit`），请求、跳转、
// 失败时留下哪句话都在外面。所以 `joining` 和 `error` 是 props 进来的 —— 加入中它
// 只负责把按钮转起来，失败时把外面那句话挂在输入框下面。
import { ref, watch } from 'vue'

import AdaptiveDialog from '@/components/common/AdaptiveDialog.vue'
import { t } from '@/i18n'

const props = defineProps<{
  modelValue: boolean
  /** 正在加入：按钮转起来，也挡住第二次提交。 */
  joining?: boolean
  /** 上一次为什么没加成。 */
  error?: string
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  submit: [code: string]
}>()

const code = ref('')

// 关掉就把填了一半的码清掉：下一次打开是空白的一张表。
watch(
  () => props.modelValue,
  (value) => {
    if (!value) code.value = ''
  }
)

function submit() {
  const value = code.value.trim()
  if (!value || props.joining) return
  emit('submit', value)
}
</script>

<template>
  <AdaptiveDialog
    :model-value="modelValue"
    size="sm"
    :title="t('work.joinTitle')"
    :primary-label="t('work.joinSubmit')"
    :primary-loading="joining"
    :cancel-label="t('work.joinCancel')"
    @update:model-value="emit('update:modelValue', $event)"
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
