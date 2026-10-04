<script setup lang="ts">
// 项目设置最后一块：归档。只有所有者看得到——后端也只认所有者，给别人画一颗点了
// 必然被拒的按钮没有意义。
import { ref } from 'vue'

import ArchiveProjectDialog from '@/components/ArchiveProjectDialog.vue'
import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'

defineProps<{ projectId: string; projectName: string }>()

const open = ref(false)
</script>

<template>
  <section class="page-section">
    <div class="page-section-head">
      <v-icon size="14" class="c-faint">mdi-archive-outline</v-icon>
      <span class="page-section-title">{{ t('work.projectSettings.archive.title') }}</span>
    </div>
    <div class="page-section-body">
      <div class="archive-row">
        <span class="t-body c-muted">{{ t('work.projectSettings.archive.hint') }}</span>
        <BaseButton kind="ghost" size="sm" @click="open = true">{{
          t('work.projectSettings.archive.action')
        }}</BaseButton>
      </div>
    </div>
    <ArchiveProjectDialog v-model="open" :project-id="projectId" :project-name="projectName" />
  </section>
</template>

<style scoped>
/* 说明和按钮一行；放不下时按钮换到下一行，而不是把说明挤成一列。 */
.archive-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  justify-content: space-between;
}
</style>
