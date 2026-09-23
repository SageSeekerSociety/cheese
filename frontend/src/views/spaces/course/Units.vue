<template>
  <!--
    教学单元（老师）。课程的时间线：按周次排开，每一周写着讲什么、要交什么、什么时候
    发出去。

    **发布是一个动作，不是建单元时的一个勾。** 未发布的单元对学生不存在（接口那一侧
    就查不到），所以老师可以先把整学期排好，到了那一周再放出去 —— 这正是「随课程推进
    才能得到更多知识」的默认，而不是一份要每周记得改的配置。
  -->
  <v-sheet flat rounded="lg" class="pa-4">
    <div class="d-flex align-center mb-4">
      <h1 class="text-h6 mb-0">{{ t('spaces.course.units.title') }}</h1>
      <v-spacer></v-spacer>
      <v-btn color="primary" prepend-icon="mdi-plus" @click="openCreate">
        {{ t('spaces.course.units.add') }}
      </v-btn>
    </div>

    <v-alert v-if="error" type="error" variant="tonal" density="comfortable" class="mb-4">
      {{ error }}
    </v-alert>

    <div v-if="loading" class="text-center pa-4">
      <v-progress-circular indeterminate color="primary"></v-progress-circular>
    </div>

    <v-sheet v-else-if="!units.length" flat rounded="lg" class="section pa-6 text-center">
      <p class="text-medium-emphasis mb-0">{{ t('spaces.course.units.empty') }}</p>
    </v-sheet>

    <v-list v-else rounded="lg">
      <v-list-item v-for="unit in units" :key="unit.id" class="unit">
        <template #prepend>
          <v-avatar color="primary" variant="tonal" size="46" class="me-3">
            <span class="text-caption">{{ weekLabel(unit.week) }}</span>
          </v-avatar>
        </template>

        <v-list-item-title class="d-flex align-center">
          {{ unit.title }}
          <v-chip class="ms-2" size="x-small" variant="tonal" :color="unit.publishedAt ? 'success' : 'warning'">
            {{ unit.publishedAt ? t('spaces.course.units.published') : t('spaces.course.units.draft') }}
          </v-chip>
        </v-list-item-title>

        <v-list-item-subtitle>
          <div v-if="unit.summary">{{ unit.summary }}</div>
          <div>{{ assignmentLabel(unit) }}</div>
          <div>{{ dueLabel(unit) }}</div>
        </v-list-item-subtitle>

        <template #append>
          <v-btn
            :prepend-icon="unit.quizId ? 'mdi-pencil-outline' : 'mdi-plus'"
            variant="text"
            size="small"
            :to="{ name: 'SpacesCourseQuiz', params: { spaceId }, query: { unit: String(unit.id) } }"
          >
            {{ unit.quizId ? t('spaces.course.units.editQuiz') : t('spaces.course.units.addQuiz') }}
          </v-btn>
          <v-btn
            :prepend-icon="unit.publishedAt ? 'mdi-eye-off-outline' : 'mdi-send-outline'"
            variant="text"
            size="small"
            @click="togglePublished(unit)"
          >
            {{ unit.publishedAt ? t('spaces.course.units.unpublish') : t('spaces.course.units.publish') }}
          </v-btn>
          <v-btn icon="mdi-pencil" variant="text" size="small" @click="openEdit(unit)"></v-btn>
          <v-btn icon="mdi-delete-outline" variant="text" size="small" @click="removeUnit(unit)"></v-btn>
        </template>
      </v-list-item>
    </v-list>

    <v-dialog v-model="dialogOpen" max-width="640">
      <v-card>
        <v-card-title>
          {{ editing ? t('spaces.course.units.form.editTitle') : t('spaces.course.units.form.createTitle') }}
        </v-card-title>
        <v-card-text>
          <v-text-field
            v-model.number="form.week"
            autocomplete="off"
            type="number"
            min="1"
            :label="t('spaces.course.units.form.week')"
            :hint="t('spaces.course.units.form.weekHint')"
            persistent-hint
            class="mb-4"
          ></v-text-field>

          <v-text-field
            v-model="form.title"
            autocomplete="off"
            :label="t('spaces.course.units.form.title')"
            :hint="t('spaces.course.units.form.titleHint')"
            persistent-hint
            class="mb-4"
          ></v-text-field>

          <v-textarea
            v-model="form.summary"
            autocomplete="off"
            :label="t('spaces.course.units.form.summary')"
            :hint="t('spaces.course.units.form.summaryHint')"
            rows="2"
            auto-grow
            persistent-hint
            class="mb-4"
          ></v-textarea>

          <v-select
            v-model="form.assignmentTaskId"
            autocomplete="off"
            :items="assignmentItems"
            item-title="label"
            item-value="value"
            :label="t('spaces.course.units.form.assignment')"
            :hint="t('spaces.course.units.form.assignmentHint')"
            persistent-hint
            class="mb-4"
          ></v-select>

          <v-text-field
            v-model="form.dueAt"
            type="datetime-local"
            :label="t('spaces.course.units.form.dueAt')"
            :hint="t('spaces.course.units.form.dueAtHint')"
            persistent-hint
            class="mb-4"
          ></v-text-field>

          <v-checkbox
            v-if="!editing"
            v-model="form.published"
            :label="t('spaces.course.units.form.publishNow')"
            :hint="t('spaces.course.units.form.publishNowHint')"
            persistent-hint
            hide-details="auto"
          ></v-checkbox>
        </v-card-text>
        <v-card-actions>
          <v-spacer></v-spacer>
          <v-btn variant="text" @click="dialogOpen = false">
            {{ t('spaces.course.units.form.cancel') }}
          </v-btn>
          <v-btn color="primary" :loading="saving" @click="submit">
            {{ t('spaces.course.units.form.save') }}
          </v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </v-sheet>
</template>

<script setup lang="ts">
import type { SpaceMyPublishedTask, TeachingUnit } from '@/network/api/spaces/types'

import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'

import { SpacesApi } from '@/network/api/spaces'
import { useDialog } from '@/plugins/dialog'

const { t } = useI18n()
const route = useRoute()
const { confirm } = useDialog()

const spaceId = Number(route.params.spaceId)

const units = ref<TeachingUnit[]>([])
const publishedTasks = ref<SpaceMyPublishedTask[]>([])
const loading = ref(true)
const saving = ref(false)
const error = ref('')

const dialogOpen = ref(false)
const editing = ref<TeachingUnit | null>(null)
const form = ref({
  week: 1,
  title: '',
  summary: '',
  assignmentTaskId: null as number | null,
  dueAt: '',
  published: false,
})

// 作业只能从**这个版里已经发布的题**里挑：接口那一侧还会再核一次「这道题属于这个
// 版」，前端只是别让人去挑一个必然被拒的东西。
const assignmentItems = computed(() => [
  { label: t('spaces.course.units.form.noAssignment'), value: null as number | null },
  ...publishedTasks.value.map((task) => ({
    label: task.taskName,
    value: task.taskId as number | null,
  })),
])

const taskNames = computed(() => {
  const map = new Map<number, string>()
  for (const task of publishedTasks.value) map.set(task.taskId, task.taskName)
  return map
})

function weekLabel(week: number): string {
  return t('spaces.course.units.weekShort', { week })
}

function assignmentLabel(unit: TeachingUnit): string {
  if (!unit.assignmentTaskId) return t('spaces.course.units.noAssignment')
  const name = taskNames.value.get(unit.assignmentTaskId)
  return name
    ? t('spaces.course.units.assignment', { name })
    : t('spaces.course.units.assignmentUnknown', { id: unit.assignmentTaskId })
}

function dueLabel(unit: TeachingUnit): string {
  if (!unit.dueAt) return t('spaces.course.units.noDue')
  return t('spaces.course.units.due', { date: new Date(unit.dueAt).toLocaleString() })
}

function toLocalInput(ms: number | null): string {
  if (!ms) return ''
  const d = new Date(ms)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function toEpoch(value: string): number | null {
  const ms = Date.parse(value)
  return Number.isFinite(ms) ? ms : null
}

async function reload() {
  loading.value = true
  try {
    const { data } = await SpacesApi.listUnits(spaceId)
    units.value = data.units
    error.value = ''
  } catch {
    units.value = []
    error.value = t('spaces.course.units.loadFailed')
  }
  loading.value = false
}

async function loadTasks() {
  try {
    const { data } = await SpacesApi.getMyPublishedTasks(spaceId)
    publishedTasks.value = data.tasks
  } catch {
    publishedTasks.value = []
  }
}

function openCreate() {
  editing.value = null
  const nextWeek = units.value.length ? Math.max(...units.value.map((u) => u.week)) + 1 : 1
  form.value = {
    week: nextWeek,
    title: '',
    summary: '',
    assignmentTaskId: null,
    dueAt: '',
    published: false,
  }
  dialogOpen.value = true
}

function openEdit(unit: TeachingUnit) {
  editing.value = unit
  form.value = {
    week: unit.week,
    title: unit.title,
    summary: unit.summary,
    assignmentTaskId: unit.assignmentTaskId,
    dueAt: toLocalInput(unit.dueAt),
    published: Boolean(unit.publishedAt),
  }
  dialogOpen.value = true
}

async function submit() {
  if (!form.value.title.trim() || form.value.week < 1) {
    error.value = t('spaces.course.units.form.required')
    return
  }
  saving.value = true
  const dueAt = form.value.dueAt ? toEpoch(form.value.dueAt) : null
  try {
    if (editing.value) {
      await SpacesApi.updateUnit(spaceId, editing.value.id, {
        week: form.value.week,
        title: form.value.title,
        summary: form.value.summary,
        assignmentTaskId: form.value.assignmentTaskId,
        clearAssignment: form.value.assignmentTaskId === null,
        dueAt,
        clearDueAt: dueAt === null,
      })
    } else {
      await SpacesApi.createUnit(spaceId, {
        week: form.value.week,
        title: form.value.title,
        summary: form.value.summary,
        assignmentTaskId: form.value.assignmentTaskId,
        dueAt,
        published: form.value.published,
      })
    }
    dialogOpen.value = false
    error.value = ''
    await reload()
  } catch {
    error.value = t('spaces.course.units.saveFailed')
  }
  saving.value = false
}

async function togglePublished(unit: TeachingUnit) {
  await SpacesApi.updateUnit(spaceId, unit.id, { published: !unit.publishedAt })
  await reload()
}

async function removeUnit(unit: TeachingUnit) {
  const ok = await confirm(t('spaces.course.units.deleteConfirm', { week: unit.week, title: unit.title }), {
    title: t('spaces.course.units.deleteTitle'),
  }).wait()
  if (!ok) return
  await SpacesApi.deleteUnit(spaceId, unit.id)
  await reload()
}

onMounted(async () => {
  if (!Number.isFinite(spaceId) || spaceId <= 0) {
    loading.value = false
    return
  }
  await Promise.all([reload(), loadTasks()])
})
</script>

<style scoped lang="scss">
.section {
  border: 1px solid var(--line);
}

.unit + .unit {
  border-top: 1px solid var(--line);
}
</style>
