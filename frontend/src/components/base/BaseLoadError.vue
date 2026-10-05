<script setup lang="ts">
/**
 * 「这块内容没读出来」的那一句话，加上一条重试的路（docs/design-system.md §3.10）。
 *
 * 在这之前，读失败常只弹一条几秒的红条，页面接着渲染它的空状态（「暂无 X」）——
 * 失败和「本来就没有」长得一模一样，错过那几秒就再也分不出来。读失败要**留在它
 * 读的那块地方**：用错误替换掉那块内容，并把人能做的下一步（重试）摆在那里。
 *
 * 服务端那句真实原因（`error`，来自 `ApiError.message`）有就照原样显示，没有就
 * 只显示这一块的标题。标题由调用方给（「加载总览失败」这类），默认是全局的
 * 「加载失败」。
 */
import BaseButton from './BaseButton.vue'

import { t } from '@/i18n'

const props = withDefaults(
  defineProps<{
    /** 服务端给的真实原因，有就照原样显示。 */
    error?: string | null
    /** 这一块读失败的那句话；不给就用全局的「加载失败」。 */
    title?: string
    /** 重试那颗按钮的字。不给就用全局的「重试」；页面另有说法时传它，别为此改写既有文案。 */
    retryLabel?: string
  }>(),
  { error: null, title: undefined, retryLabel: undefined }
)

defineEmits<{ retry: [] }>()
</script>

<template>
  <!-- role="alert" so a reader is told this block is empty because it failed, not because nothing is there. -->
  <div class="base-load-error" role="alert">
    <v-alert
      type="error"
      variant="tonal"
      :title="props.title ?? t('global.loadError.title')"
      :text="props.error?.trim() || undefined"
    />
    <BaseButton kind="secondary" class="mt-3" @click="$emit('retry')">
      {{ props.retryLabel ?? t('global.loadError.retry') }}
    </BaseButton>
  </div>
</template>

<style scoped>
.base-load-error {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
}
</style>
