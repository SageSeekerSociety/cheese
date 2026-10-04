<script setup lang="ts">
// 项目设置里的「导出项目」。后端有一条把整个项目打包成 tar 的端点
// （docs/project-export.md），界面一直没入口；这一块就是那个入口。
//
// 这一块是哑的：只从 props 画「打包中 / 失败」，点下去往外 emit `export`。发请求与
// 状态在 `composables/useProjectExport.ts`，由设置页面接线（`src/components` 下的
// 组件不许碰 API 层，见 guards 的 import-boundary）。
import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'

defineProps<{ busy: boolean; error: string }>()
defineEmits<{ (e: 'export'): void }>()
</script>

<template>
  <section class="page-section">
    <div class="page-section-head">
      <v-icon size="14" class="c-faint">mdi-download-outline</v-icon>
      <span class="page-section-title">{{ t('work.projectSettings.export.title') }}</span>
    </div>
    <div class="page-section-body">
      <div class="export-row">
        <span class="t-body c-muted">{{ t('work.projectSettings.export.hint') }}</span>
        <BaseButton
          kind="secondary"
          size="sm"
          prepend-icon="mdi-download-outline"
          :loading="busy"
          :disabled="busy"
          @click="$emit('export')"
        >
          {{ t('work.projectSettings.export.action') }}
        </BaseButton>
      </div>
      <v-alert v-if="error" type="error" density="comfortable" variant="tonal">{{ error }}</v-alert>
    </div>
  </section>
</template>

<style scoped>
/* 说明和按钮一行；放不下时按钮换到下一行，而不是把说明挤成一列（同归档那一块）。 */
.export-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  justify-content: space-between;
}
</style>
