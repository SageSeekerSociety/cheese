<template>
  <ul class="ta">
    <li v-for="file in attachments" :key="file.id" class="ta__row" data-testid="task-attachment">
      <v-icon size="16" class="ta__icon">mdi-file-outline</v-icon>
      <span class="ta__name">{{ file.name }}</span>
      <span class="ta__meta t-num">{{
        t('tasks.attachments.meta', { size: formatFileSize(file.size), n: file.downloadCount })
      }}</span>
      <!-- 看得见清单不等于拿得到文件。这一行由服务端的 canDownload 决定，
           前端不拿自己的角色去猜 —— 判据只有一份。 -->
      <BaseButton
        v-if="canDownload"
        kind="ghost"
        size="sm"
        prepend-icon="mdi-download"
        :loading="downloadingId === file.id"
        @click="emit('download', file)"
      >
        {{ t('tasks.attachments.download') }}
      </BaseButton>
      <span v-else class="ta__locked">{{ t('tasks.attachments.locked') }}</span>
    </li>
  </ul>
</template>

<script setup lang="ts">
import type { TaskAttachmentData } from '@/network/api/tasks/types'

import { useI18n } from 'vue-i18n'

import { formatFileSize } from '@/utils/materials'

import BaseButton from '@/components/base/BaseButton.vue'

/** 一道题的材料清单。清单和能不能下载都由外面给，这里只画和报「要下载哪一个」。 */
defineProps<{
  attachments: TaskAttachmentData[]
  canDownload: boolean
  downloadingId?: number | null
}>()

const emit = defineEmits<{ download: [file: TaskAttachmentData] }>()

const { t } = useI18n()
</script>

<style scoped>
.ta {
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-width: 520px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.ta__row {
  display: flex;
  gap: 8px;
  align-items: center;
  min-height: 40px;
  padding: 4px 4px 4px 10px;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  font-size: 13px;
}

.ta__icon {
  color: var(--faint);
}

.ta__name {
  min-width: 0;
  overflow: hidden;
  color: var(--ink);
  white-space: nowrap;
  text-overflow: ellipsis;
}

.ta__meta {
  flex: 1;
  color: var(--faint);
  font-size: 12px;
  white-space: nowrap;
}

.ta__locked {
  padding-right: 6px;
  color: var(--muted);
  font-size: 12px;
  white-space: nowrap;
}
</style>
