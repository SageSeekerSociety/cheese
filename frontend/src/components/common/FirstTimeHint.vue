<script setup lang="ts">
// 第一次碰到时的一句说明，带一颗「知道了」。看过与否见 `useFirstTimeHint`。
//
// 只说这一处在等谁、点哪一下会发生什么；不讲平台机制。正文用默认插槽，好在句子里
// 放链接（比如「我的设备」页）。
import type { FirstTimeHintId } from '@/composables/useFirstTimeHint'

import BaseButton from '@/components/base/BaseButton.vue'
import { useFirstTimeHint } from '@/composables/useFirstTimeHint'
import { t } from '@/i18n'

const props = defineProps<{ id: FirstTimeHintId }>()
const { visible, dismiss } = useFirstTimeHint(props.id)
</script>

<template>
  <div v-if="visible" class="first-hint t-meta-read" role="note" :data-hint="id">
    <v-icon icon="mdi-lightbulb-on-outline" size="16" class="first-hint__icon" aria-hidden="true" />
    <span class="first-hint__text"><slot /></span>
    <BaseButton kind="ghost" size="sm" density="comfortable" class="first-hint__ok" @click="dismiss">
      {{ t('global.firstHint.ok') }}
    </BaseButton>
  </div>
</template>

<style scoped>
.first-hint {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 8px 10px;
  margin-bottom: 8px;
  border-radius: 8px;
  background: rgb(var(--v-theme-primary), 0.06);
}
.first-hint__icon {
  margin-top: 2px;
  color: rgb(var(--v-theme-primary));
}
.first-hint__text {
  flex: 1;
  min-width: 0;
}
.first-hint__ok {
  margin: -4px -4px -4px 0;
}
</style>
