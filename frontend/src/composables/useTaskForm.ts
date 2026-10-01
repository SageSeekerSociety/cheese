/**
 * 发题表单里所有「会动」的东西：那张 zod 表、十六对 `defineField`、两道理性闸门
 * （实名信息、视频链接）和右栏那张「提交前」清单的接线。
 *
 * 为什么搬到这里（#2143）：1089 行的 `TaskForm.vue` 顶到了 `frontend/src` 那一千行
 * 的上限，而它里面真正不是「画」的部分全在这一个文件里。剩下的几张卡只吃 props、
 * 只往上发事件，于是每一张都能单独摆在预览站里（`views/demo/catalogTaskForm.ts`）。
 *
 * 形状和验收卡（`useAcceptCard.ts`）一致：容器把 props 和 emit 交进来，拿回一份可以
 * 直接铺在模板上的东西 —— 每个字段都是一对，`v-model:<字段>` 给值、`:control` 给
 * `defineField` 那另一半（`error-messages` / `error`）。
 *
 * 一处与验收卡不同的地方：这里还要读一次富文本编辑器里的正文，而读的**时机**是行为
 * 的一部分 —— 是「提交那一刻的正文」，不是每一次敲键盘。所以它以 `readDescriptionText`
 * 回调传进来（拿不到编辑器时给 `undefined`，与原来 `descriptionEditor.value?.…` 一样），
 * 而不是让编辑器往外发一串事件。
 */
import type { DomainGroup, SpaceCategory, TaskFormSubmitData, Topic } from '@/types'

import { computed, inject, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { toTypedSchema } from '@vee-validate/zod'
import { useForm } from 'vee-validate'
import { z } from 'zod'

import { truncateString, vuetifyConfig } from '@/utils/form'

import { evaluatePublishChecks, PUBLISH_CHECKS_SINK } from '@/lib/taskPublishChecks'

/** 容器那份 props 的形状。`TaskForm.vue` 里仍写着同一份，靠结构对上。 */
export interface TaskFormProps {
  initialData?: Partial<TaskFormSubmitData> | null
  submitButtonText: string
  isEditing?: boolean
  classificationTopics: Topic[]
  categories?: SpaceCategory[]
  selectedCategoryId?: number
  domainGroups?: DomainGroup[]
  descriptionFormat?: 'markdown' | 'tiptap'
  originalDescription?: string
  parametersOnly?: boolean
}

/** 富文本那一份内容：TipTap 的 JSON 文档（`description` 这个 ref 装的就是它）。 */
export interface DescriptionDoc {
  type: string
  content: { type: string }[]
}

/** 容器往外报的两件事。 */
export interface TaskFormEmits {
  (event: 'submit', data: TaskFormSubmitData): void
  (event: 'cancel'): void
}

export function useTaskForm(
  props: TaskFormProps,
  emit: TaskFormEmits,
  /** 读富文本编辑器现在的正文；Markdown 那条路上编辑器不在，给 `undefined`。 */
  readDescriptionText: () => string | undefined
) {
  const { t } = useI18n()
  const topicItems = computed(() => props.classificationTopics.map((topic) => ({ title: topic.name, value: topic.id })))
  const categoryItems = computed(
    () => props.categories?.map((category) => ({ title: category.name, value: category.id })) ?? []
  )

  const { handleSubmit, defineField, isSubmitting, values } = useForm({
    validationSchema: toTypedSchema(
      z
        .object({
          name: z.string().min(1).max(100),
          submitterType: z.enum(['USER', 'TEAM']),
          registrationStartAt: z.date().optional().nullable(),
          deadline: z.date().nullable(),
          defaultDeadline: z.number().int().default(30),
          rank: z.number().int().min(1).max(3),
          topics: z.array(z.number()).optional(),
          categoryId: z.number().int().min(1, t('tasks.form.validation.categoryRequired')),
          minTeamSize: z.number().int().min(1).optional(),
          maxTeamSize: z.number().int().min(1).optional(),
          requireRealName: z.boolean().optional().default(false),
          participantLimit: z.number().int().min(1).optional().nullable(),
          teamLockingPolicy: z.enum(['NO_LOCK', 'LOCK_ON_APPROVAL']).optional(),
          accessControlEnabled: z.boolean().optional().default(false),
          accessDomainGroupIds: z.array(z.number()).optional(),
          videoUrl: z
            .string()
            .optional()
            .refine(
              (v) => {
                if (!v) return true
                try {
                  const parsed = new URL(v)
                  return parsed.protocol === 'https:'
                } catch {
                  return false
                }
              },
              { message: t('tasks.form.validation.httpsRequired') }
            ),
        })
        .refine((arg) => !arg.maxTeamSize || !arg.minTeamSize || arg.maxTeamSize >= arg.minTeamSize, {
          message: t('tasks.form.validation.teamSizeOrder'),
          path: ['maxTeamSize'],
        })
    ),
    initialValues: {
      ...(props.initialData ?? {}),
      name: props.initialData?.name ?? (props.parametersOnly ? t('tasks.form.pdfParametersName') : ''),
      registrationStartAt: props.initialData?.registrationStartAt
        ? new Date(props.initialData.registrationStartAt)
        : null,
      deadline: props.initialData?.deadline
        ? new Date(props.initialData.deadline)
        : props.isEditing
          ? null
          : new Date(Date.now() + 14 * 24 * 60 * 60 * 1000),
      requireRealName: props.initialData?.requireRealName ?? false,
      categoryId: props.initialData?.categoryId ?? props.selectedCategoryId ?? undefined,
      minTeamSize: props.initialData?.minTeamSize ?? 1,
      maxTeamSize: props.initialData?.maxTeamSize ?? 10,
      defaultDeadline: props.initialData?.defaultDeadline ?? 30,
      // `||` 而不是 `??`：0 在这个表单里与「没有」是同一件事（后端不限时存的是 `null`，
      // 老行里可能是 0），留着它反而会被下面那条 `min(1)` 判成填错。
      participantLimit: props.initialData?.participantLimit || null,
      teamLockingPolicy: props.initialData?.teamLockingPolicy ?? 'NO_LOCK',
      accessControlEnabled: props.initialData?.accessControlEnabled ?? false,
      accessDomainGroupIds: props.initialData?.accessDomainGroupIds ?? [],
      videoUrl: props.initialData?.videoUrl ?? '',
    },
  })

  const [name, nameProps] = defineField('name', vuetifyConfig)
  const [submitterType, submitterTypeProps] = defineField('submitterType', vuetifyConfig)
  const [rank, rankProps] = defineField('rank', vuetifyConfig)
  const [registrationStartAt, registrationStartAtProps] = defineField('registrationStartAt', vuetifyConfig)
  const [deadline, deadlineProps] = defineField('deadline', vuetifyConfig)
  const [defaultDeadline, defaultDeadlineProps] = defineField('defaultDeadline', vuetifyConfig)
  const [topics, topicsProps] = defineField('topics', vuetifyConfig)
  const [minTeamSize, minTeamSizeProps] = defineField('minTeamSize', vuetifyConfig)
  const [maxTeamSize, maxTeamSizeProps] = defineField('maxTeamSize', vuetifyConfig)
  const [categoryId, categoryIdProps] = defineField('categoryId', vuetifyConfig)
  const [requireRealName, requireRealNameProps] = defineField('requireRealName', vuetifyConfig)
  const [participantLimit, participantLimitProps] = defineField('participantLimit', vuetifyConfig)
  const [teamLockingPolicy, teamLockingPolicyProps] = defineField('teamLockingPolicy', vuetifyConfig)
  const [accessControlEnabled, accessControlEnabledProps] = defineField('accessControlEnabled', vuetifyConfig)
  const [accessDomainGroupIds, accessDomainGroupIdsProps] = defineField('accessDomainGroupIds', vuetifyConfig)
  const [videoUrl, videoUrlProps] = defineField('videoUrl', vuetifyConfig)

  // 「不限」那一勾。不是表单字段（不进 zod、不进 payload）：它只描述旁边那个数现在算不算数。
  // 勾上 = 把那个数放回初始的那份 `null`（这个表单里「不填」就是不限，`min(1)` 那条只管
  // 填了的值），并把输入框锁上 —— 交出去的 payload 与「从头就没填过」一模一样
  // （`participantLimit` 那一项是 `undefined`，见 `submitFormData`）。
  //
  // 默认只在**改一道本来就上限为空的题**时勾上：那是它真实的状态（`null`，老行里也可能
  // 是 0）。新发一道题不勾 —— 与原型那张卡一样，框空着、想设上限直接填。
  const participantLimitUnlimited = ref(props.isEditing && !((props.initialData?.participantLimit ?? 0) > 0))

  watch(participantLimitUnlimited, (unlimited) => {
    if (unlimited) participantLimit.value = null
  })

  const domainGroupItems = computed(
    () => props.domainGroups?.map((g) => ({ title: g.name, value: g.id, subtitle: g.domains.join(', ') })) ?? []
  )

  const createEmptyDescription = () => ({
    type: 'doc',
    content: [{ type: 'paragraph' }],
  })

  const description = ref<string | DescriptionDoc>(props.initialData?.description || createEmptyDescription())
  // Markdown 格式的描述内容
  const markdownDescription = ref(props.originalDescription || '')

  const pendingSubmissionData = ref<{ descriptionText: string | undefined; values: any } | null>(null)

  const isBilibiliUrl = (v: string): boolean => {
    if (!v) return true
    return /bilibili\.com\/video\/BV[\w]+/.test(v)
  }

  const submitForm = handleSubmit((values) => {
    if (requireRealName.value && !wasRealNameEnabled.value) {
      privacyDialogOpen.value = true
      pendingSubmissionData.value = {
        descriptionText: readDescriptionText(),
        values,
      }
      return
    }
    if (videoUrl.value && !isBilibiliUrl(videoUrl.value)) {
      videoUrlDialogOpen.value = true
      pendingSubmissionData.value = {
        descriptionText: readDescriptionText(),
        values,
      }
      return
    }
    submitFormData(values)
  })

  const submitFormData = (values: any) => {
    const descriptionText = pendingSubmissionData.value?.descriptionText ?? readDescriptionText()
    const deadlineDate = values.deadline ? new Date(values.deadline) : null
    const registrationStartAtDate = values.registrationStartAt ? new Date(values.registrationStartAt) : null
    deadlineDate?.setHours(23, 59, 59, 999)

    // 根据原始格式决定保存的描述内容
    let savedDescription: string
    let introText: string
    if (props.parametersOnly) {
      savedDescription = ''
      introText = ''
    } else if (props.descriptionFormat === 'markdown') {
      // 如果原始是 markdown 格式，保存纯文本内容
      savedDescription = markdownDescription.value || ''
      introText = markdownDescription.value || ''
    } else {
      // 如果原始是 TipTap JSON 格式，保存 JSON
      savedDescription = JSON.stringify(description.value)
      introText = descriptionText || ''
    }

    const submissionData: TaskFormSubmitData = {
      ...values,
      description: savedDescription,
      intro: truncateString(introText, 255),
      registrationStartAt: registrationStartAtDate ? registrationStartAtDate.getTime() : null,
      deadline: deadlineDate?.getTime() ?? null,
      ...(props.isEditing ? { hasDeadline: deadlineDate !== null } : {}),
      resubmittable: true,
      editable: true,
      requireRealName: requireRealName.value,
      categoryId: categoryId.value || undefined,
      minTeamSize: submitterType.value === 'TEAM' ? minTeamSize.value : undefined,
      maxTeamSize: submitterType.value === 'TEAM' ? maxTeamSize.value : undefined,
      participantLimit: participantLimit.value || undefined,
      teamLockingPolicy: submitterType.value === 'TEAM' ? teamLockingPolicy.value : undefined,
      accessControlEnabled: accessControlEnabled.value,
      accessDomainGroupIds: accessControlEnabled.value ? accessDomainGroupIds.value : undefined,
      videoUrl: videoUrl.value || null,
    }
    emit('submit', submissionData)
  }

  // --- 发题页右栏那张「提交前」清单 ------------------------------------------------
  //
  // 表单把自己现在**拦着你的**那几条报给挂着这一页的外壳（`lib/taskPublishChecks.ts`
  // 里那份规则表就是上面这份 zod schema 的逐条对译），并把自己的提交交出去 ——
  // 清单那张卡上的「提交审核」按钮走的就是它，不是另开一条假路。
  //
  // 不 provide 就没有这一段（老树、改题页都照旧）：规则表只读 `values`，一个字段都
  // 不动，也不替表单校验 —— vee-validate 该什么时候标红还是什么时候标红。
  const publishChecksSink = inject(PUBLISH_CHECKS_SINK, null)
  if (publishChecksSink) {
    watch(values, (current) => publishChecksSink.report(evaluatePublishChecks(current)), {
      deep: true,
      immediate: true,
    })
    publishChecksSink.handOverSubmit(submitForm)
    onBeforeUnmount(() => publishChecksSink.handOverSubmit(null))
  }

  const privacyDialogOpen = ref(false)
  const wasRealNameEnabled = ref(false)

  const cancelSubmitWithRealName = () => {
    requireRealName.value = false
    wasRealNameEnabled.value = false
    privacyDialogOpen.value = false
    pendingSubmissionData.value = null
  }

  const confirmSubmitWithRealName = () => {
    wasRealNameEnabled.value = true
    privacyDialogOpen.value = false
    if (pendingSubmissionData.value) {
      submitFormData(pendingSubmissionData.value.values)
      pendingSubmissionData.value = null
    }
  }

  const videoUrlDialogOpen = ref(false)

  const cancelVideoUrlDialog = () => {
    videoUrlDialogOpen.value = false
    pendingSubmissionData.value = null
  }

  const confirmVideoUrlDialog = () => {
    videoUrlDialogOpen.value = false
    if (pendingSubmissionData.value) {
      submitFormData(pendingSubmissionData.value.values)
      pendingSubmissionData.value = null
    }
  }

  // 初始化wasRealNameEnabled
  wasRealNameEnabled.value = !!props.initialData?.requireRealName

  watch(
    () => props.initialData?.name,
    (value) => {
      if (props.parametersOnly && value && value !== name.value) {
        name.value = value
      }
    },
    { immediate: true }
  )

  const handleCancel = () => {
    emit('cancel')
  }

  return {
    topicItems,
    categoryItems,
    domainGroupItems,
    name,
    nameProps,
    submitterType,
    submitterTypeProps,
    rank,
    rankProps,
    registrationStartAt,
    registrationStartAtProps,
    deadline,
    deadlineProps,
    defaultDeadline,
    defaultDeadlineProps,
    topics,
    topicsProps,
    minTeamSize,
    minTeamSizeProps,
    maxTeamSize,
    maxTeamSizeProps,
    categoryId,
    categoryIdProps,
    requireRealName,
    requireRealNameProps,
    participantLimit,
    participantLimitProps,
    teamLockingPolicy,
    teamLockingPolicyProps,
    accessControlEnabled,
    accessControlEnabledProps,
    accessDomainGroupIds,
    accessDomainGroupIdsProps,
    videoUrl,
    videoUrlProps,
    participantLimitUnlimited,
    description,
    markdownDescription,
    isSubmitting,
    submitForm,
    handleCancel,
    privacyDialogOpen,
    cancelSubmitWithRealName,
    confirmSubmitWithRealName,
    videoUrlDialogOpen,
    cancelVideoUrlDialog,
    confirmVideoUrlDialog,
  }
}
