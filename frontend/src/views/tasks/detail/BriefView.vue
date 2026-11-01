<template>
  <!-- 「说明」页签的画面：题目详情（富文本）、提交内容、评分要点、材料。阅读宽度。取数在 `Brief.vue`。 -->
  <div v-if="taskData" class="tb">
    <section class="tb__sec">
      <h2 class="tb__h t-title">{{ t('tasks.brief.details') }}</h2>
      <TaskDescription :source="taskData.description" :empty="t('tasks.brief.empty')" />
    </section>

    <!-- Stages: the timeline, each with what it asks for. A student reads where their own claim
         stands on each; whoever grades reads how the class stands. -->
    <section v-if="stages.length" class="tb__sec" data-testid="task-brief-stages">
      <h2 class="tb__h t-title">{{ t('tasks.brief.stages') }}</h2>
      <TaskStageTimeline :stages="stages" :statuses="progress?.mine?.stages" :counts="progress?.distribution" />
      <dl class="tb__stages">
        <template v-for="(stage, index) in stages" :key="index">
          <dt data-user-content>{{ t('tasks.stages.numbered', { n: index + 1, name: stage.name }) }}</dt>
          <dd v-if="stage.description" data-user-content>{{ stage.description }}</dd>
        </template>
      </dl>
    </section>

    <section v-if="taskData.submissionSchema?.length" class="tb__sec" data-testid="task-brief-submission">
      <h2 class="tb__h t-title">{{ t('tasks.brief.submission') }}</h2>
      <template v-for="group in itemGroups" :key="group.index">
        <h3 v-if="group.title" class="tb__stage t-eyebrow-read" data-user-content>{{ group.title }}</h3>
        <ol class="tb__items">
          <li v-for="(entry, index) in group.rows" :key="index">
            <span data-user-content>{{ entry.prompt }}</span>
            <span class="tb__kind t-meta-read">{{
              t(entry.type === 'FILE' ? 'tasks.brief.kindFile' : 'tasks.brief.kindText')
            }}</span>
          </li>
        </ol>
      </template>
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
      <template v-for="group in lineGroups" :key="group.index">
        <h3 v-if="group.title" class="tb__stage t-eyebrow-read" data-user-content>{{ group.title }}</h3>
        <div v-if="group.rows.length" class="tb__table">
          <table class="tb__rubric">
            <thead>
              <tr>
                <th scope="col">{{ t('tasks.form.grading.lineItem') }}</th>
                <th scope="col" class="tb__pts">{{ t('tasks.form.grading.linePoints') }}</th>
                <th scope="col">{{ t('tasks.form.grading.lineCriteria') }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(line, index) in group.rows" :key="index">
                <td data-user-content>{{ line.item }}</td>
                <td class="tb__pts t-num">{{ line.points }}</td>
                <td data-user-content>{{ line.criteria }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </template>
    </section>

    <!-- Materials: the list is the server's, and so is whether they download. No section without materials. -->
    <section v-if="attachments.length" class="tb__sec">
      <h2 class="tb__h t-title">{{ t('tasks.brief.materials') }}</h2>
      <TaskAttachmentList
        :attachments="attachments"
        :can-download="canDownload"
        :downloading="downloading"
        previewable
        @download="emit('download', $event)"
        @open="emit('open', $event)"
      />
    </section>
  </div>
</template>

<script setup lang="ts">
import type { Task, TaskProgress } from '@/types'
import type { TaskFileData } from '@/types/taskBoard'

import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import TaskAttachmentList from '@/components/tasks/TaskAttachmentList.vue'
import TaskDescription from '@/components/tasks/TaskDescription.vue'
import TaskStageTimeline from '@/components/tasks/TaskStageTimeline.vue'
import { settledStage } from '@/lib/taskStages'

const props = withDefaults(
  defineProps<{
    taskData: Task | null
    attachments: TaskFileData[]
    canDownload: boolean
    /** 正在下载的那一份（名字）。 */
    downloading: string | null
    /** 每个阶段上的进度：我的，和批改的人才有的全体分布。 */
    progress?: TaskProgress | null
  }>(),
  { progress: null }
)

const emit = defineEmits<{ download: [file: TaskFileData]; open: [file: TaskFileData] }>()

const { t } = useI18n()

const stages = computed(() => props.taskData?.stages ?? [])

/** 分了阶段时按阶段分组（带阶段的标题，按阶段的 id 归组）；不分阶段是一组，没有标题。 */
function grouped<T extends { stage?: string | null }>(rows: T[]) {
  if (!stages.value.length) return [{ index: 0, title: null as string | null, rows }]
  return stages.value.map((stage, index) => ({
    index,
    title: t('tasks.stages.numbered', { n: index + 1, name: stage.name }) as string | null,
    rows: rows.filter((row) => settledStage(stages.value, row.stage) === stage.id),
  }))
}

const itemGroups = computed(() => grouped(props.taskData?.submissionSchema ?? []))
const lineGroups = computed(() => grouped(props.taskData?.rubric ?? []))
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

.tb__stage {
  margin: 12px 0 6px;
  color: var(--muted);
}

.tb__stage:first-of-type {
  margin-top: 0;
}

.tb__stages {
  margin: 16px 0 0;
  color: var(--text);
  font-size: 14px;
  line-height: var(--lh-14-loose);
}

.tb__stages dt {
  color: var(--ink);
  font-weight: 600;
}

.tb__stages dd {
  margin: 0 0 8px;
  white-space: pre-wrap;
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
