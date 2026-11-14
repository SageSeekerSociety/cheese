<template>
  <div>
    <div class="d-flex flex-row align-center">
      <v-icon v-if="content.type === 'TEXT'">mdi-text</v-icon>
      <v-icon v-else-if="content.type === 'FILE'">mdi-file-document-outline</v-icon>
      <span class="text-h6">{{ content.title }}</span>
    </div>
    <div v-if="content.contentText">{{ content.contentText }}</div>
    <!-- 一项可以交几份文件（一份报告和它引用的图片），每份一张卡片；Markdown 报告在它的卡片
         下面按文档画出来，图片引用指向同一项的其余几份。 -->
    <div v-else-if="files.length" class="d-flex flex-column ga-2">
      <template v-for="file in files" :key="file.id">
        <v-card
          border
          flat
          class="file-card d-flex flex-row align-center"
          :href="getFullAttachmentUrl(file.url)"
          target="_blank"
        >
          <v-icon class="text-medium-emphasis" size="36">mdi-file-document-outline</v-icon>
          <div class="d-flex flex-column">
            <div class="text-body-1">{{ (file.meta as FileMeta).name }}</div>
            <div class="text-caption">{{ fmtFileSize(file.meta.size) }}</div>
          </div>
        </v-card>
        <SubmissionMarkdownFile v-if="isMarkdownName((file.meta as FileMeta).name)" :file="file" :siblings="files" />
      </template>
    </div>
  </div>
</template>

<script setup lang="ts">
import type { FileMeta, TaskSubmissionContent } from '@/types'

import { computed } from 'vue'

import { getFullAttachmentUrl } from '@/utils/materials'

import SubmissionMarkdownFile from './SubmissionMarkdownFile.vue'

import { isMarkdownName } from '@/lib/submissionFiles'
import { fmtFileSize } from '@/lib/usageFormat'

const props = defineProps<{
  content: TaskSubmissionContent
}>()

const files = computed(() => props.content.contentAttachments ?? [])
</script>

<style scoped>
.file-card {
  gap: 4px;
  padding: 8px;
}
</style>
