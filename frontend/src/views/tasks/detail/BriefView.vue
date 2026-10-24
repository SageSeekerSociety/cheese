<template>
  <!-- 「说明」页签的画面：题目详情（富文本）、提交内容、评分要点、材料。阅读宽度。取数在 `Brief.vue`。 -->
  <div v-if="taskData" class="tb">
    <section class="tb__sec">
      <h2 class="tb__h t-title">{{ t('tasks.brief.details') }}</h2>
      <TaskDescription :source="taskData.description" :empty="t('tasks.brief.empty')" />
    </section>

    <section v-if="taskData.submissionSchema?.length" class="tb__sec" data-testid="task-brief-submission">
      <h2 class="tb__h t-title">{{ t('tasks.brief.submission') }}</h2>
      <ol class="tb__items">
        <li v-for="(entry, index) in taskData.submissionSchema" :key="index">
          <span data-user-content>{{ entry.prompt }}</span>
          <span class="tb__kind t-meta-read">{{
            t(entry.type === 'FILE' ? 'tasks.brief.kindFile' : 'tasks.brief.kindText')
          }}</span>
        </li>
      </ol>
    </section>

    <!-- The rubric reaches students only when the task makes it public; graders always get it,
         and for them a private one says so. -->
    <section v-if="taskData.rubric?.length" class="tb__sec" data-testid="task-brief-rubric">
      <div class="tb__head">
        <h2 class="tb__h t-title">{{ t('tasks.brief.rubric') }}</h2>
        <span v-if="taskData.rubricPublic === false" class="tb__private t-meta-read">{{
          t('tasks.brief.rubricPrivate')
        }}</span>
      </div>
      <div class="tb__table">
        <table class="tb__rubric">
          <thead>
            <tr>
              <th scope="col">{{ t('tasks.form.grading.lineItem') }}</th>
              <th scope="col" class="tb__pts">{{ t('tasks.form.grading.linePoints') }}</th>
              <th scope="col">{{ t('tasks.form.grading.lineCriteria') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(line, index) in taskData.rubric" :key="index">
              <td data-user-content>{{ line.item }}</td>
              <td class="tb__pts t-num">{{ line.points }}</td>
              <td data-user-content>{{ line.criteria }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <!-- Materials: the list is the server's, and so is whether they download. No section without materials. -->
    <section v-if="attachments.length" class="tb__sec">
      <h2 class="tb__h t-title">{{ t('tasks.brief.materials') }}</h2>
      <TaskAttachmentList
        :attachments="attachments"
        :can-download="canDownload"
        :downloading="downloading"
        @download="emit('download', $event)"
      />
    </section>
  </div>
</template>

<script setup lang="ts">
import type { Task } from '@/types'
import type { TaskFileData } from '@/types/taskBoard'

import { useI18n } from 'vue-i18n'

import TaskAttachmentList from '@/components/tasks/TaskAttachmentList.vue'
import TaskDescription from '@/components/tasks/TaskDescription.vue'

const props = defineProps<{
  taskData: Task | null
  attachments: TaskFileData[]
  canDownload: boolean
  /** 正在下载的那一份（名字）。 */
  downloading: string | null
}>()

const emit = defineEmits<{ download: [file: TaskFileData] }>()

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

.tb__head {
  display: flex;
  align-items: baseline;
  gap: 8px;
}

.tb__private {
  margin-bottom: 10px;
}

.tb__items {
  margin: 0;
  padding-left: 20px;
  color: var(--text);
  font-size: 14px;
  line-height: var(--lh-14-loose);
}

.tb__kind {
  margin-left: 8px;
}

.tb__table {
  overflow-x: auto;
}

.tb__rubric {
  width: 100%;
  border-collapse: collapse;
  font-size: 14px;
  line-height: var(--lh-14);
  color: var(--text);
}

.tb__rubric th {
  text-align: left;
  font-size: 12px;
  font-weight: 600;
  color: var(--muted);
  padding: 6px 8px;
  border-bottom: 1px solid var(--line);
}

.tb__rubric td {
  padding: 8px;
  border-bottom: 1px solid var(--line);
  vertical-align: top;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.tb__pts {
  width: 64px;
  white-space: nowrap;
}
</style>
