<script setup lang="ts">
/**
 * 「这块内容没读出来」的那一句话，加上一条重试的路。
 *
 * 在这之前，读失败只在右上角弹一条几秒的红条，页面接着渲染它的空状态
 * （「暂无 X」）——失败和「本来就没有」长得一模一样，错过那几秒就再也
 * 分不出来（`views/workspace/ProjectAccessNotice.vue` 的头部注释记着同一
 * 个毛病）。读失败要**留在它读的那块地方**：用错误替换掉空状态，并把人
 * 能做的下一步（重试）摆在那里。
 *
 * 服务端那句真实原因（`error`）有就照原样显示，没有就只显示这一块的标题。
 * 标题由调用方给（「加载总览失败」这类），默认是全局的「加载失败」。
 */
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import BaseButton from '@/components/base/BaseButton.vue'

const props = withDefaults(
  defineProps<{
    /** 服务端给的真实原因，有就照原样显示。 */
    error?: string | null
    /** 这一块读失败的那句话；不给就用全局的「加载失败」。 */
    title?: string
  }>(),
  { error: null, title: undefined }
)

defineEmits<{
  (e: 'retry'): void
}>()

const { t } = useI18n()
const resolvedTitle = computed(() => props.title ?? t('global.loadError.title'))
const reason = computed(() => props.error?.trim() || undefined)
</script>

<template>
  <!-- role="alert" so a reader is told this block is empty because it failed, not because nothing is there. -->
  <div class="load-error-notice" role="alert">
    <v-alert type="error" variant="tonal" :title="resolvedTitle" :text="reason" />
    <BaseButton kind="secondary" class="mt-3" @click="$emit('retry')">{{ t('global.loadError.retry') }}</BaseButton>
  </div>
</template>

<style scoped>
.load-error-notice {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
}
</style>
