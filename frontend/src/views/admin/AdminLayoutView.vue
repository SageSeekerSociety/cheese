<script setup lang="ts">
// 管理后台内容区**画的那一半**：门的三态、`<RouterView>` 画子页、那张快捷键表。
//
// 取数（`loadMeta` / `refreshCounts`）、读地址、全局键（`?` / `R` / `G` 序列）都在容器
// `AdminLayout.vue` 里。这里只吃 props、只往上发事件，所以它能被单独挂起来看。
import { useI18n } from 'vue-i18n'

import AdminShortcutSheet from '@/components/admin/AdminShortcutSheet.vue'
import BaseButton from '@/components/base/BaseButton.vue'

defineProps<{
  /** meta 到没到 —— 门画哪一档的依据。 */
  metaChecked: boolean
  /** 过没过门。 */
  canEnter: boolean
  /** `?` 那一层的开合（`v-model:shortcut-open`）。 */
  shortcutOpen: boolean
}>()

defineEmits<{
  'update:shortcutOpen': [open: boolean]
}>()

const { t } = useI18n()
</script>

<template>
  <div class="admin-shell">
    <div v-if="!metaChecked" class="admin-shell__gate">
      <div class="admin-shell__gate-inner">
        <v-icon size="28" class="mb-2">mdi-shield-account-outline</v-icon>
        <div class="t-body mb-1">{{ t('admin.layout.checking') }}</div>
      </div>
    </div>

    <div v-else-if="!canEnter" class="admin-shell__gate">
      <div class="admin-shell__gate-inner">
        <v-icon size="28" class="mb-2">mdi-shield-account-outline</v-icon>
        <div class="t-body mb-1">{{ t('admin.layout.deniedTitle') }}</div>
        <div class="t-body mb-3">{{ t('admin.layout.deniedBody') }}</div>
        <!-- 这一屏只有这一个动作，所以它是 `primary`。 -->
        <BaseButton kind="primary" size="sm" to="/feedback">{{ t('admin.layout.toFeedbackCenter') }}</BaseButton>
      </div>
    </div>

    <RouterView v-else />

    <AdminShortcutSheet :model-value="shortcutOpen" @update:model-value="$emit('update:shortcutOpen', $event)" />
  </div>
</template>

<style scoped>
.admin-shell {
  height: 100%;
  min-height: 0;
}

/* 门口那两态是居中一句话，不是一页内容。 */
.admin-shell__gate {
  display: flex;
  align-items: center;
  justify-content: center;
  box-sizing: border-box;
  height: 100%;
  padding: 48px 24px;
}

.admin-shell__gate-inner {
  display: flex;
  flex-direction: column;
  align-items: center;
  max-width: 420px;
  text-align: center;
}
</style>
