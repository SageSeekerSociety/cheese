<template>
  <!-- 「说明」页签的画面：题目详情（富文本）、材料。阅读宽度。取数在 `Brief.vue`。 -->
  <div v-if="taskData" class="tb">
    <section class="tb__sec">
      <h2 class="tb__h t-title">{{ t('tasks.brief.details') }}</h2>
      <TaskDescription :source="taskData.description" :empty="t('tasks.brief.empty')" />
    </section>

    <!-- 材料：清单与下载次数都是服务端的，点不点得动也由它说。没有材料就不画这一段。 -->
    <section v-if="attachments.length" class="tb__sec">
      <h2 class="tb__h t-title">{{ t('tasks.brief.materials') }}</h2>
      <TaskAttachmentList
        :attachments="attachments"
        :can-download="canDownload"
        :downloading-id="downloadingId"
        @download="emit('download', $event)"
      />
    </section>
  </div>
</template>

<script setup lang="ts">
import type { TaskAttachmentData } from '@/network/api/tasks/types'
import type { Task } from '@/types'

import { useI18n } from 'vue-i18n'

import TaskAttachmentList from '@/components/tasks/TaskAttachmentList.vue'
import TaskDescription from '@/components/tasks/TaskDescription.vue'

const props = defineProps<{
  taskData: Task | null
  attachments: TaskAttachmentData[]
  canDownload: boolean
  downloadingId: number | null
}>()

const emit = defineEmits<{ download: [file: TaskAttachmentData] }>()

const { t } = useI18n()
</script>

<style scoped>
.tb {
  max-width: 680px;
}

.tb__sec + .tb__sec {
  margin-top: 28px;
}

.tb__h {
  margin: 0 0 10px;
}
</style>
