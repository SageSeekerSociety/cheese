<script setup lang="ts">
// 项目设置里的「导出项目」。后端早就有一条把整个项目打包成 tar 的端点
// （docs/project-export.md），界面一直没有入口；这一块就是那个入口。
//
// 打包在服务端按调用者当前能看的范围现算，大项目要等一会儿，所以按下去之后按钮
// 进 loading、并禁用，避免连点发两遍；失败了就把那句话原样摆在这一块里，不动整页。
import { ref } from 'vue'

import { downloadFile, projectExportUrl } from '@/api'
import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'

const props = defineProps<{ projectId: string; projectName: string }>()

const busy = ref(false)
const error = ref('')

async function exportProject() {
  error.value = ''
  busy.value = true
  try {
    await downloadFile(projectExportUrl(props.projectId), `${props.projectName || props.projectId}.tar`)
  } catch (e) {
    error.value = e instanceof Error ? e.message : t('work.projectSettings.export.failed')
  } finally {
    busy.value = false
  }
}
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
          @click="exportProject"
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
