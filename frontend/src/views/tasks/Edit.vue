<script setup lang="ts">
// 改题页：和发题页同一张表（`TaskForm`），参与方式改不了，附件直接传到这道题上、从这道
// 题上摘，AI 指导和发题时一样单独写或沿用空间的默认。没有导入和模板。
import type { SpaceTeaching, TaskFormSubmitData } from '@/types'

import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import { toast } from 'vuetify-sonner'
import { storeToRefs } from 'pinia'

import { useAttachmentUploads } from '@/composables/useAttachmentUploads'
import { useSpaceData } from '@/composables/useSpaceData'
import { useSpaceMaterials } from '@/composables/useSpaceMaterials'

import { useTaskData } from './composables'

import { clearTaskExternalProblem, getTaskExternalProblem, setTaskExternalProblem } from '@/api/externalProblem'
import { TaskBoardApi } from '@/api/taskBoard'
import BaseButton from '@/components/base/BaseButton.vue'
import BaseLoadError from '@/components/base/BaseLoadError.vue'
import PageHeader from '@/components/common/PageHeader.vue'
import TeachingFields from '@/components/common/TeachingFields.vue'
import TaskAttachmentPicker from '@/components/tasks/TaskAttachmentPicker.vue'
import TaskForm from '@/components/tasks/TaskForm.vue'
import { closeOverlay } from '@/lib/backOut'
import { spaceLibraryPath } from '@/lib/spaceRouteNames'
import { isTeachingBlank } from '@/lib/teaching'
import errorHandler from '@/services/ErrorHandler'
import { useSpaceStore } from '@/stores/space'

const { t } = useI18n()

const router = useRouter()
const route = useRoute()
const taskId = Number(route.params.taskId)

const { taskData, editTaskData, loading, error, loadTaskData } = useTaskData()

const spaceStore = useSpaceStore()
const spaceData = useSpaceData()
const { domainGroups, categories } = storeToRefs(spaceStore)

const spaceId = computed(() => taskData.value?.space?.id ?? Number(route.params.spaceId))
const listTo = computed(() => ({ name: 'SpacesDetailTasksList', params: { spaceId: spaceId.value } }))
const detailTo = computed(() => ({ name: 'TasksDetail', params: { spaceId: spaceId.value, taskId } }))

/** 活跃（未归档）的分类，加上这道题现在所在的那一个（哪怕已经归档，也得显示得出来）。 */
const editCategories = computed(() =>
  categories.value
    .filter((category) => !category.archivedAt || category.id === taskData.value?.category?.id)
    .sort((a, b) => a.displayOrder - b.displayOrder)
)

const { materials, state: materialsState } = useSpaceMaterials(spaceId)

/** 附件直接传到这道题上、从这道题上摘：改题页上的增删当场生效，不等保存。 */
const uploads = useAttachmentUploads({
  upload: async (file) => (await TaskBoardApi.uploadAttachment(taskId, file)).attachment.id,
  remove: async (id) => {
    await TaskBoardApi.removeAttachment(taskId, id)
  },
})

const teaching = ref<SpaceTeaching>({})
const teachingOwn = ref(false)

const form = ref<{ submit: () => void } | null>(null)
const formInvalid = ref(0)
const attempted = ref(false)
const saving = ref(false)
const resubmit = ref(false)

// 外站题号（YOJ）。它不在题目自己那份数据里，所以单独读一次（见 `api/externalProblem.ts` 开头）。
// 读回来之前不画那张表：先画成空的，一保存就会拿空值去删掉已经挂着的题号和结果。
// 读失败时这一格干脆不画（`unknown`）—— 拿不准的事不写回去，比猜一个空的安全。
const externalProblemState = ref<'loading' | 'ready' | 'unknown'>('loading')
/** 已经存下的那个题号；保存时拿它比对，没变就不发那一次写。 */
const externalProblemNo = ref('')
const formInitialData = computed(() => ({ ...editTaskData.value, externalProblemNo: externalProblemNo.value }))

/** 审核没通过的题，改完可以一并重新提交审核。 */
const rejected = computed(() => taskData.value?.approved === 'DISAPPROVED')

function save(again: boolean) {
  attempted.value = true
  resubmit.value = again
  form.value?.submit()
}

/**
 * 题号那一次写。`undefined` = 这张表没有这一格（读不到原来那份），什么都不做；
 * 和存下的一样也不做 —— 换成一个新题号会把上一次的结果删掉，白写一次就是白删一次。
 */
async function saveExternalProblem(problemNo: string | undefined) {
  if (problemNo === undefined || problemNo === externalProblemNo.value) return
  if (problemNo) await setTaskExternalProblem(taskId, problemNo)
  else await clearTaskExternalProblem(taskId)
  externalProblemNo.value = problemNo
}

async function onSubmit(data: TaskFormSubmitData) {
  if (saving.value) return
  saving.value = true
  // 题号跟着题目一起保存，但走的不是同一条接口（`PATCH /tasks` 不认它）。
  const { externalProblemNo: problemNo, ...taskFields } = data
  try {
    await TaskBoardApi.update(taskId, {
      ...taskFields,
      // 整份替换：沿用默认就交一份空的，让空间（或项目集）那一层重新生效。
      teaching: teachingOwn.value && !isTeachingBlank(teaching.value) ? teaching.value : {},
    })
    // 题目存住了再写题号：题号那次失败时下面会说出来，页面留着让人重试（题目已经是新的了）。
    await saveExternalProblem(problemNo)
    if (resubmit.value) {
      await TaskBoardApi.resubmitTask(taskId)
      toast.success(t('tasks.edit.resubmitted'))
    } else {
      toast.success(t('tasks.detail.updateSuccess'))
    }
    navigateToDetail()
  } catch (err) {
    void errorHandler.handle(err as Error, { defaultMessage: t('global.updateFailed') })
  } finally {
    saving.value = false
  }
}

// 保存 / 取消之后回题目详情。**不是 push**：进来时就是从详情 push 过来的，出去再 push
// 一次，身后就多一条详情，按 ← 会落回那张刚保存过的表单。去向是定的（详情），所以走
// closeOverlay：身后正是它就退一格，否则 replace 过去。
const navigateToDetail = () => closeOverlay(router, detailTo.value)

onMounted(async () => {
  // 和题目那份一起读：等两件都到齐才画表格，免得先画一版空的题号。
  const [, binding] = await Promise.all([
    loadTaskData(),
    getTaskExternalProblem(taskId).then(
      (data) => data,
      () => null
    ),
  ])
  if (binding) {
    externalProblemNo.value = binding.problemNo ?? ''
    externalProblemState.value = 'ready'
  } else {
    // 读不到就不画这一格（也就不用管它）。说一句让改题的人知道少了什么。
    externalProblemState.value = 'unknown'
  }
  const task = taskData.value
  if (!task) return
  teaching.value = { ...(task.teaching ?? {}) }
  teachingOwn.value = !isTeachingBlank(teaching.value)
  if (task.space?.id) {
    spaceStore.setCurrentSpaceId(task.space.id)
    await Promise.all([spaceData.fetchDomainGroups(), spaceData.fetchCategories()])
  }
  try {
    const data = await TaskBoardApi.listAttachments(taskId)
    uploads.reset(data.attachments.map(({ id, name, size }) => ({ id, name, size })))
  } catch {
    // 读不出来就当没有：增删仍然走得通，只是这一页不列已有的那几份。
  }
})
</script>

<template>
  <PageHeader show-on-mobile>
    <nav class="te__crumb">
      <router-link :to="listTo" class="te__crumb-link">{{ t('spaces.detail.allContests') }}</router-link>
      <v-icon size="16" class="te__crumb-sep">mdi-chevron-right</v-icon>
      <router-link :to="detailTo" class="te__crumb-link" data-user-content>{{ taskData?.name ?? '' }}</router-link>
      <v-icon size="16" class="te__crumb-sep">mdi-chevron-right</v-icon>
      <span class="te__crumb-here">{{ t('tasks.edit.crumb') }}</span>
    </nav>
    <template #actions>
      <span v-if="attempted && formInvalid" class="te__blocking">{{
        t('spaces.detail.publishTask.blocking', { n: formInvalid })
      }}</span>
      <BaseButton kind="ghost" :disabled="saving" @click="navigateToDetail">{{ t('global.cancel') }}</BaseButton>
      <BaseButton
        v-if="rejected"
        kind="secondary"
        :loading="saving && resubmit"
        :disabled="saving || uploads.uploading.value"
        @click="save(true)"
        >{{ t('tasks.edit.saveAndResubmit') }}</BaseButton
      >
      <BaseButton
        kind="primary"
        :loading="saving && !resubmit"
        :disabled="saving || uploads.uploading.value || !taskData"
        @click="save(false)"
        >{{ t('tasks.edit.saveChanges') }}</BaseButton
      >
    </template>
  </PageHeader>

  <div class="te">
    <div v-if="loading" class="py-12 text-center">
      <v-progress-circular indeterminate color="primary" />
    </div>
    <!-- A failed read trades the form for the error (docs/design-system.md §3.10). -->
    <BaseLoadError v-else-if="error" :title="t('tasks.loadError.title')" :error="error" @retry="loadTaskData" />
    <template v-else-if="taskData && externalProblemState !== 'loading'">
      <!-- 题号没读出来时这一格不画（见上面 `externalProblemState`），但要说一句：
           不然改题的人只会发现少了一格，不知道是自己记错了还是页面坏了。 -->
      <p v-if="externalProblemState === 'unknown'" class="te__ext-missing t-meta-read">
        {{ t('tasks.edit.externalProblemUnavailable') }}
      </p>
      <TaskForm
        ref="form"
        :initial-data="formInitialData"
        :external-problem-enabled="externalProblemState === 'ready'"
        is-editing
        :classification-topics="taskData.space?.classificationTopics || []"
        :categories="editCategories"
        :domain-groups="domainGroups"
        :teaching-custom="teachingOwn"
        @invalid="formInvalid = $event"
        @submit="onSubmit"
      >
        <template #attachments>
          <TaskAttachmentPicker
            :files="uploads.files.value"
            :uploading="uploads.uploading.value"
            :max-file-bytes="uploads.maxFileBytes.value"
            @add="uploads.add"
            @remove="uploads.remove"
          />
        </template>
        <template #teaching>
          <div>
            <p class="te__label">{{ t('spaces.detail.publishTask.teaching.title') }}</p>
            <p class="te__hint t-meta-read">{{ t('spaces.detail.publishTask.teaching.subtitle') }}</p>
            <v-radio-group v-model="teachingOwn" hide-details density="compact" class="te__choices">
              <v-radio :value="false" :label="t('spaces.detail.publishTask.teaching.inherit')" />
              <v-radio :value="true" :label="t('spaces.detail.publishTask.teaching.own')" />
            </v-radio-group>
            <TeachingFields
              v-if="teachingOwn"
              v-model="teaching"
              class="te__teaching"
              :materials="materials"
              :materials-state="materialsState"
              :library-to="spaceLibraryPath(spaceId)"
            />
          </div>
        </template>
      </TaskForm>
    </template>
  </div>
</template>

<style scoped>
.te {
  width: min(var(--page-w), 100%);
  margin: 0 auto;
  padding: 8px 16px 64px;
}

.te__crumb {
  display: flex;
  gap: 4px;
  align-items: center;
  min-width: 0;
  font-size: 14px;
  line-height: var(--lh-14);
}

.te__crumb-link {
  overflow: hidden;
  color: var(--muted);
  text-decoration: none;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.te__crumb-link:hover {
  color: var(--ink);
}

.te__crumb-sep {
  color: var(--faint);
}

.te__crumb-here {
  flex: none;
  color: var(--ink);
  font-weight: 600;
}

.te__ext-missing {
  margin: 0 0 8px;
}

.te__blocking {
  color: var(--danger-ink);
  font-size: 12px;
  line-height: var(--lh-12);
}

.te__label {
  margin: 0;
  color: var(--text);
  font-size: 13px;
  font-weight: 500;
  line-height: var(--lh-13);
}

.te__hint {
  margin: 4px 0 8px;
}

.te__choices :deep(.v-label) {
  color: var(--text);
  font-size: 14px;
  line-height: var(--lh-14);
  opacity: 1;
}

.te__teaching {
  margin-top: 12px;
}
</style>
