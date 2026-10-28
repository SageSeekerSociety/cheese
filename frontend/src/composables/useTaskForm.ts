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
import type {
  DomainGroup,
  SpaceCategory,
  TaskDraftFields,
  TaskFormSubmitData,
  TaskRubricLine,
  TaskStage,
  TaskSubmissionSchemaEntry,
  Topic,
} from '@/types'

import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useForm } from 'vee-validate'
import { z } from 'zod'

import { truncateString, vuetifyConfig } from '@/utils/form'

import { EMPTY_DOC, jsonContent, markdownContent } from '@/components/common/Editor/richText'
import { settledStage } from '@/lib/taskStages'
import { toTypedSchema } from '@/lib/zodTypedSchema'

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
  /** 新发一道题时导师默认是谁：出题人自己。 */
  defaultMentor?: string
}

/** 新发一道题时学生要交的东西：一个文件。 */
export const DEFAULT_SUBMISSION_SCHEMA: TaskSubmissionSchemaEntry[] = [{ prompt: '提交文件', type: 'FILE' }] // i18n-data: 存进题目数据的提交项名，和出题人写的题面一样不随界面语言变

/** 评分要点里一行还空着（加了没填）：提交时去掉，不算错。 */
const blankLine = (line: TaskRubricLine) => !line.item.trim() && !line.criteria.trim()

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
                .string({ error: t('tasks.form.validation.nameRequired') })
                .trim()
                .min(1, t('tasks.form.validation.nameRequired'))
                .max(100),
          submitterType: z.enum(['USER', 'TEAM'], { error: t('tasks.form.validation.submitterTypeRequired') }),
          registrationStartAt: z.date().optional().nullable(),
          deadline: z.date().nullable(),
          startMode: z.enum(['DIRECT', 'APPLY']),
          defaultDeadline: z
            .number({ error: t('tasks.form.validation.completionRequired') })
            .int()
            .min(1, t('tasks.form.validation.completionRequired')),
          rank: z
            .number({ error: t('tasks.form.validation.rankRequired') })
            .int()
            .min(1, t('tasks.form.validation.rankRequired'))
            .max(3),
          topics: z.array(z.number()).optional(),
          categoryId: z
            .number({ error: t('tasks.form.validation.categoryRequired') })
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
      // 新题目默认直接开始；改题时是它原来的。
      startMode: props.initialData?.startMode ?? 'DIRECT',
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
  const [startMode] = defineField('startMode', vuetifyConfig)
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

  // 导师（#3830）：勾上「把导师加入领取者的项目」才带上这份名单。新发的题默认是出题人
  // 自己，改题时是这道题现在的那几位。
  const initialMentors = props.initialData?.mentors ?? []
  const mentorsJoin = ref(initialMentors.length > 0)
  const mentors = ref<string[]>(
    initialMentors.length ? [...initialMentors] : props.defaultMentor ? [props.defaultMentor] : []
  )

  watch(participantLimitUnlimited, (unlimited) => {
    if (unlimited) participantLimit.value = null
  })

  const domainGroupItems = computed(
    () => props.domainGroups?.map((g) => ({ title: g.name, value: g.id, subtitle: g.domains.join(', ') })) ?? []
  )

  const description = ref<DescriptionDoc>(descriptionDoc(props.initialData?.description))

  // 提交与评分：学生交哪几样东西，评分要点，以及评分要点给不给学生看。
  const submissionSchema = ref<TaskSubmissionSchemaEntry[]>(
    (props.initialData?.submissionSchema?.length ? props.initialData.submissionSchema : DEFAULT_SUBMISSION_SCHEMA).map(
      (entry) => ({ ...entry })
    )
  )
  const rubric = ref<TaskRubricLine[]>((props.initialData?.rubric ?? []).map((line) => ({ ...line })))
  const rubricPublic = ref(props.initialData?.rubricPublic ?? true)
  /** 点过发布后，有写了标准却没写名称的那一行。 */
  const rubricMissingName = ref(false)
  watch(rubric, () => (rubricMissingName.value = false), { deep: true })

  // 阶段：每个已保存的阶段带着题目给它的 id，挪了位置也带着，已交的提交跟着它走
  // （`app/domain/task/stages.py`）。
  const stages = ref<TaskStage[]>((props.initialData?.stages ?? []).map((stage) => ({ ...stage })))
  /** 点过发布后，有没写名称的阶段。 */
  const stageMissingName = ref(false)
  watch(stages, () => (stageMissingName.value = false), { deep: true })

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

  /** 只校验、标出缺的格子，不提交（一次发几道时，切到没发出去的那道上用）。 */
  const check = handleSubmit(() => undefined)

  const submitFormData = (values: any) => {
    const lines = rubric.value.filter((line) => !blankLine(line))
    if (lines.some((line) => !line.item.trim())) {
      rubricMissingName.value = true
      return
    }
    if (stages.value.some((stage) => !stage.name.trim())) {
      stageMissingName.value = true
      return
    }
    const written = submissionSchema.value
      .map((entry) => ({ ...entry, stage: settledStage(stages.value, entry.stage), prompt: entry.prompt.trim() }))
      .filter((entry) => entry.prompt)
    // 每个阶段都至少交一样东西：一样都没写的阶段交一个文件。
    const stageIds = stages.value.length ? stages.value.map((stage) => stage.id ?? null) : [null]
    const items = stageIds
      .map((stage) => {
        const own = written.filter((entry) => entry.stage === stage)
        return own.length ? own : DEFAULT_SUBMISSION_SCHEMA.map((entry) => ({ ...entry, stage }))
      })
      .flat()
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
      // As the form holds them whichever form is chosen: publishing the draft
      // leaves them out of a 个人 题目 (`published_form`).
      minTeamSize: minTeamSize.value,
      maxTeamSize: maxTeamSize.value,
      participantLimit: participantLimit.value || undefined,
      teamLockingPolicy: teamLockingPolicy.value,
      accessControlEnabled: accessControlEnabled.value,
      accessDomainGroupIds: accessControlEnabled.value ? accessDomainGroupIds.value : undefined,
      mentors: mentorsJoin.value ? mentors.value : [],
      submissionSchema: items,
      rubric: lines.map((line) => ({
        item: line.item.trim(),
        points: Number(line.points) || 0,
        criteria: line.criteria.trim(),
        stage: settledStage(stages.value, line.stage),
      })),
      rubricPublic: rubricPublic.value,
      stages: stages.value.map((stage) => ({
        name: stage.name.trim(),
        description: stage.description.trim(),
        dueAt: stage.dueAt,
        id: stage.id ?? null,
      })),
    }
    emit('submit', submissionData)
  }

  /** 表单现在的样子，原样存进草稿：人写到哪就是哪，不修整、不校验。 */
  function snapshot(): TaskDraftFields {
    const ms = (value: unknown) => (value ? new Date(value as string | number | Date).getTime() : null)
    return {
      name: name.value ?? '',
      submitterType: submitterType.value,
      rank: rank.value,
      registrationStartAt: ms(registrationStartAt.value),
      deadline: ms(deadline.value),
      defaultDeadline: defaultDeadline.value,
      startMode: startMode.value,
      topics: topics.value ?? [],
      categoryId: categoryId.value,
      minTeamSize: minTeamSize.value,
      maxTeamSize: maxTeamSize.value,
      requireRealName: requireRealName.value ?? false,
      participantLimit: participantLimit.value ?? null,
      teamLockingPolicy: teamLockingPolicy.value,
      accessControlEnabled: accessControlEnabled.value ?? false,
      accessDomainGroupIds: accessDomainGroupIds.value ?? [],
      mentors: mentorsJoin.value ? mentors.value : [],
      description: JSON.stringify(description.value),
      submissionSchema: submissionSchema.value,
      rubric: rubric.value,
      rubricPublic: rubricPublic.value,
      stages: stages.value,
    }
  }

  /** 从外面填进来的几格（草稿里存的、芝士写的）：只动给出的那几格。 */
  function apply(fields: TaskDraftFields) {
    const date = (value: number | null | undefined) => (value ? new Date(value) : null)
    if ('name' in fields) name.value = fields.name ?? ''
    if ('description' in fields) description.value = descriptionDoc(fields.description ?? '')
    if (fields.submitterType) submitterType.value = fields.submitterType
    if ('rank' in fields) rank.value = fields.rank as number
    if ('registrationStartAt' in fields) registrationStartAt.value = date(fields.registrationStartAt)
    if ('deadline' in fields) deadline.value = date(fields.deadline)
    if ('defaultDeadline' in fields) defaultDeadline.value = fields.defaultDeadline as number
    if (fields.startMode) startMode.value = fields.startMode
    if ('topics' in fields) topics.value = fields.topics ?? []
    if ('categoryId' in fields) categoryId.value = fields.categoryId as number
    if ('minTeamSize' in fields) minTeamSize.value = fields.minTeamSize
    if ('maxTeamSize' in fields) maxTeamSize.value = fields.maxTeamSize
    if ('requireRealName' in fields) requireRealName.value = !!fields.requireRealName
    if ('participantLimit' in fields) {
      participantLimit.value = fields.participantLimit ?? null
      participantLimitUnlimited.value = !((fields.participantLimit ?? 0) > 0)
    }
    if ('teamLockingPolicy' in fields) teamLockingPolicy.value = fields.teamLockingPolicy
    if ('accessControlEnabled' in fields) accessControlEnabled.value = !!fields.accessControlEnabled
    if ('accessDomainGroupIds' in fields) accessDomainGroupIds.value = fields.accessDomainGroupIds ?? []
    if ('mentors' in fields) {
      mentors.value = [...(fields.mentors ?? [])]
      mentorsJoin.value = mentors.value.length > 0
    }
    if ('submissionSchema' in fields) submissionSchema.value = (fields.submissionSchema ?? []).map((e) => ({ ...e }))
    if ('rubric' in fields) rubric.value = (fields.rubric ?? []).map((line) => ({ ...line }))
    if ('rubricPublic' in fields) rubricPublic.value = fields.rubricPublic ?? true
    if ('stages' in fields) stages.value = (fields.stages ?? []).map((stage) => ({ ...stage }))
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
    startMode,
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
    mentorsJoin,
    mentors,
    description,
    submissionSchema,
    rubric,
    rubricPublic,
    rubricMissingName,
    stages,
    stageMissingName,
    isSubmitting,
    submitForm,
    check,
    snapshot,
    apply,
    privacyDialogOpen,
    cancelSubmitWithRealName,
    confirmSubmitWithRealName,
  }
}
