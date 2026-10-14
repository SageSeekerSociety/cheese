/**
 * 发题表单里所有「会动」的东西：那张 zod 表、各个字段的 `defineField`、实名那一道
 * 确认，以及「点了发布还有几项没过」那个数。
 *
 * 形状和验收卡（`useAcceptCard.ts`）一致：容器把 props 和 emit 交进来，拿回一份可以
 * 直接铺在模板上的东西 —— 每个字段都是一对，`v-model:<字段>` 给值、`:control` 给
 * `defineField` 那另一半（`error-messages` / `error`）。
 *
 * 这里还要读一次富文本编辑器里的正文，而读的**时机**是行为的一部分 —— 是「提交那一刻
 * 的正文」，不是每一次敲键盘。所以它以 `readDescriptionText` 回调传进来。
 */
import type { JSONContent } from '@tiptap/core'
import type { DomainGroup, SpaceCategory, TaskFormSubmitData, Topic } from '@/types'

import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { toTypedSchema } from '@vee-validate/zod'
import { useForm } from 'vee-validate'
import { z } from 'zod'

import { truncateString, vuetifyConfig } from '@/utils/form'

import { EMPTY_DOC, jsonContent, markdownContent } from '@/components/common/Editor/richText'

/** 容器那份 props 的形状。`TaskForm.vue` 里仍写着同一份，靠结构对上。 */
export interface TaskFormProps {
  initialData?: Partial<TaskFormSubmitData> | null
  isEditing?: boolean
  classificationTopics: Topic[]
  categories?: SpaceCategory[]
  selectedCategoryId?: number
  domainGroups?: DomainGroup[]
  /** 一次发好几道：名称和描述逐道在外面填，这张表只管共用的那些。 */
  parametersOnly?: boolean
}

/** 富文本那一份内容：编辑器的 JSON 文档（`description` 这个 ref 装的就是它）。 */
export type DescriptionDoc = JSONContent

/** 容器往外报的事。 */
export interface TaskFormEmits {
  (event: 'submit', data: TaskFormSubmitData): void
  (event: 'invalid', count: number): void
}

/**
 * 存下来的描述读成编辑器的文档：编辑器的 JSON（对象或字符串）照原样，其余的字符串是从
 * PDF 导入时存下的 Markdown。
 */
export function descriptionDoc(value: unknown): DescriptionDoc {
  if (typeof value === 'string') {
    if (!value.trim()) return EMPTY_DOC
    try {
      const parsed: unknown = JSON.parse(value)
      if (typeof parsed === 'object' && parsed !== null && (parsed as JSONContent).type === 'doc') {
        return jsonContent(parsed)
      }
    } catch {
      // 不是 JSON：是 Markdown。
    }
    return markdownContent(value)
  }
  return jsonContent(value)
}

export function useTaskForm(
  props: TaskFormProps,
  emit: TaskFormEmits,
  /** 读富文本编辑器现在的正文；编辑器不在时给 `undefined`。 */
  readDescriptionText: () => string | undefined
) {
  const { t } = useI18n()
  const topicItems = computed(() => props.classificationTopics.map((topic) => ({ title: topic.name, value: topic.id })))
  const categoryItems = computed(
    () => props.categories?.map((category) => ({ title: category.name, value: category.id })) ?? []
  )

  // 跟着 `parametersOnly` 走：表单挂着的时候从文件里读出了好几道，名称这一格就不再归它管。
  const validationSchema = computed(() =>
    toTypedSchema(
      z
        .object({
          // 一次发好几道时名称逐道在外面填，这张表里没有这一格。
          name: props.parametersOnly
            ? z.string().optional()
            : z
                .string({ required_error: t('tasks.form.validation.nameRequired') })
                .trim()
                .min(1, t('tasks.form.validation.nameRequired'))
                .max(100),
          submitterType: z.enum(['USER', 'TEAM'], { required_error: t('tasks.form.validation.submitterTypeRequired') }),
          registrationStartAt: z.date().optional().nullable(),
          deadline: z.date().nullable(),
          defaultDeadline: z
            .number({
              required_error: t('tasks.form.validation.completionRequired'),
              invalid_type_error: t('tasks.form.validation.completionRequired'),
            })
            .int()
            .min(1, t('tasks.form.validation.completionRequired')),
          rank: z
            .number({
              required_error: t('tasks.form.validation.rankRequired'),
              invalid_type_error: t('tasks.form.validation.rankRequired'),
            })
            .int()
            .min(1, t('tasks.form.validation.rankRequired'))
            .max(3),
          topics: z.array(z.number()).optional(),
          categoryId: z
            .number({
              required_error: t('tasks.form.validation.categoryRequired'),
              invalid_type_error: t('tasks.form.validation.categoryRequired'),
            })
            .int()
            .min(1, t('tasks.form.validation.categoryRequired')),
          minTeamSize: z.number().int().min(1).optional(),
          maxTeamSize: z.number().int().min(1).optional(),
          requireRealName: z.boolean().optional().default(false),
          participantLimit: z.number().int().min(1).optional().nullable(),
          teamLockingPolicy: z.enum(['NO_LOCK', 'LOCK_ON_APPROVAL']).optional(),
          accessControlEnabled: z.boolean().optional().default(false),
          accessDomainGroupIds: z.array(z.number()).optional(),
        })
        .refine((arg) => !arg.maxTeamSize || !arg.minTeamSize || arg.maxTeamSize >= arg.minTeamSize, {
          message: t('tasks.form.validation.teamSizeOrder'),
          path: ['maxTeamSize'],
        })
    )
  )

  const { handleSubmit, defineField, isSubmitting, errors, submitCount } = useForm({
    validationSchema,
    initialValues: {
      ...(props.initialData ?? {}),
      name: props.initialData?.name ?? '',
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
    },
  })

  const [name, nameProps] = defineField('name', vuetifyConfig)
  const [submitterType, submitterTypeProps] = defineField('submitterType', vuetifyConfig)
  const [rank, rankProps] = defineField('rank', vuetifyConfig)
  const [registrationStartAt, registrationStartAtProps] = defineField('registrationStartAt', vuetifyConfig)
  const [deadline, deadlineProps] = defineField('deadline', vuetifyConfig)
  const [defaultDeadline, defaultDeadlineProps] = defineField('defaultDeadline', vuetifyConfig)
  const [topics] = defineField('topics', vuetifyConfig)
  const [minTeamSize, minTeamSizeProps] = defineField('minTeamSize', vuetifyConfig)
  const [maxTeamSize, maxTeamSizeProps] = defineField('maxTeamSize', vuetifyConfig)
  const [categoryId, categoryIdProps] = defineField('categoryId', vuetifyConfig)
  const [requireRealName] = defineField('requireRealName', vuetifyConfig)
  const [participantLimit, participantLimitProps] = defineField('participantLimit', vuetifyConfig)
  const [teamLockingPolicy] = defineField('teamLockingPolicy', vuetifyConfig)
  const [accessControlEnabled] = defineField('accessControlEnabled', vuetifyConfig)
  const [accessDomainGroupIds] = defineField('accessDomainGroupIds', vuetifyConfig)

  // 「不限」那一勾。不是表单字段（不进 zod、不进 payload）：它只描述旁边那个数现在算不算数。
  // 勾上 = 把那个数放回 `null`（这个表单里「不填」就是不限），并把输入框锁上 —— 交出去的
  // payload 与「从头就没填过」一模一样。
  //
  // 新发一道题和改一道本来就不限的题时勾着：那是它真实的状态。
  const participantLimitUnlimited = ref(!((props.initialData?.participantLimit ?? 0) > 0))

  watch(participantLimitUnlimited, (unlimited) => {
    if (unlimited) participantLimit.value = null
  })

  const domainGroupItems = computed(
    () => props.domainGroups?.map((g) => ({ title: g.name, value: g.id, subtitle: g.domains.join(', ') })) ?? []
  )

  const description = ref<DescriptionDoc>(descriptionDoc(props.initialData?.description))

  /** 点过发布之后还拦着的项数；没点过就是 0（打开页面不该满屏红字）。 */
  const invalidCount = computed(() => (submitCount.value > 0 ? Object.keys(errors.value).length : 0))
  watch(invalidCount, (count) => emit('invalid', count), { immediate: true })

  const pendingSubmission = ref<{ descriptionText: string | undefined; values: any } | null>(null)

  const submitForm = handleSubmit((values) => {
    if (requireRealName.value && !wasRealNameEnabled.value) {
      privacyDialogOpen.value = true
      pendingSubmission.value = { descriptionText: readDescriptionText(), values }
      return
    }
    submitFormData(values)
  })

  const submitFormData = (values: any) => {
    const descriptionText = pendingSubmission.value?.descriptionText ?? readDescriptionText()
    const deadlineDate = values.deadline ? new Date(values.deadline) : null
    const registrationStartAtDate = values.registrationStartAt ? new Date(values.registrationStartAt) : null
    deadlineDate?.setHours(23, 59, 59, 999)

    const submissionData: TaskFormSubmitData = {
      ...values,
      name: props.parametersOnly ? '' : values.name.trim(),
      description: props.parametersOnly ? '' : JSON.stringify(description.value),
      intro: props.parametersOnly ? '' : truncateString(descriptionText || '', 255),
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
    }
    emit('submit', submissionData)
  }

  const privacyDialogOpen = ref(false)
  // 本来就要求实名的题，改的时候不再问一遍。
  const wasRealNameEnabled = ref(!!props.initialData?.requireRealName)

  const cancelSubmitWithRealName = () => {
    requireRealName.value = false
    wasRealNameEnabled.value = false
    privacyDialogOpen.value = false
    pendingSubmission.value = null
  }

  const confirmSubmitWithRealName = () => {
    wasRealNameEnabled.value = true
    privacyDialogOpen.value = false
    if (pendingSubmission.value) {
      submitFormData(pendingSubmission.value.values)
      pendingSubmission.value = null
    }
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
    minTeamSize,
    minTeamSizeProps,
    maxTeamSize,
    maxTeamSizeProps,
    categoryId,
    categoryIdProps,
    requireRealName,
    participantLimit,
    participantLimitProps,
    teamLockingPolicy,
    accessControlEnabled,
    accessDomainGroupIds,
    participantLimitUnlimited,
    description,
    isSubmitting,
    submitForm,
    privacyDialogOpen,
    cancelSubmitWithRealName,
    confirmSubmitWithRealName,
  }
}
