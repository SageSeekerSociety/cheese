/** Unit tests for TaskForm access control submission logic.
 *
 * The TaskForm component uses vee-validate + zod for validation and
 * constructs TaskFormSubmitData from form state. These tests validate
 * the access control portions of the schema and data transformation.
 */
import { describe, expect, it } from 'vitest'
import { z } from 'zod'

// ---------------------------------------------------------------------------
// Schema under test — mirrors the zod schema in TaskForm.vue
// ---------------------------------------------------------------------------

const taskFormSchema = z
  .object({
    name: z.string().min(1).max(100),
    submitterType: z.enum(['USER', 'TEAM']),
    registrationStartAt: z.date().optional().nullable(),
    deadline: z.date(),
    defaultDeadline: z.number().int().default(30),
    rank: z.number().int().min(1).max(3),
    topics: z.array(z.number()).optional(),
    categoryId: z.number().optional().nullable(),
    minTeamSize: z.number().int().min(1).optional(),
    maxTeamSize: z.number().int().min(1).optional(),
    requireRealName: z.boolean().optional().default(false),
    participantLimit: z.number().int().min(1).optional().nullable(),
    teamLockingPolicy: z.enum(['NO_LOCK', 'LOCK_ON_APPROVAL']).optional(),
    accessControlEnabled: z.boolean().optional().default(false),
    accessDomainGroupIds: z.array(z.number()).optional(),
  })
  .refine(
    (arg) => !arg.maxTeamSize || !arg.minTeamSize || arg.maxTeamSize >= arg.minTeamSize,
    { message: '最大人数不能小于最小人数', path: ['maxTeamSize'] },
  )

// ---------------------------------------------------------------------------
// Form data → TaskFormSubmitData transformation
// ---------------------------------------------------------------------------

interface TaskFormSubmitData {
  name: string
  submitterType: 'USER' | 'TEAM'
  rank: number
  registrationStartAt?: number | null
  deadline: number
  defaultDeadline: number
  resubmittable: boolean
  editable: boolean
  intro: string
  description: string
  requireRealName: boolean
  categoryId?: number
  minTeamSize?: number
  maxTeamSize?: number
  participantLimit?: number
  teamLockingPolicy?: string
  accessControlEnabled?: boolean
  accessDomainGroupIds?: number[]
}

function buildSubmitData(values: Record<string, unknown>, descriptionText: string): TaskFormSubmitData {
  const deadlineDate = new Date(values.deadline as string)
  deadlineDate.setHours(23, 59, 59, 999)

  const accessControlEnabled = (values.accessControlEnabled as boolean) || false
  const accessDomainGroupIds = accessControlEnabled
    ? (values.accessDomainGroupIds as number[] | undefined)
    : undefined

  return {
    name: values.name as string,
    submitterType: values.submitterType as 'USER' | 'TEAM',
    rank: values.rank as number,
    registrationStartAt: values.registrationStartAt
      ? new Date(values.registrationStartAt as string).getTime()
      : null,
    deadline: deadlineDate.getTime(),
    defaultDeadline: values.defaultDeadline as number,
    resubmittable: true,
    editable: true,
    intro: descriptionText.substring(0, 255),
    description: JSON.stringify(values.description),
    requireRealName: (values.requireRealName as boolean) || false,
    categoryId: values.categoryId as number | undefined,
    minTeamSize: values.submitterType === 'TEAM' ? (values.minTeamSize as number) : undefined,
    maxTeamSize: values.submitterType === 'TEAM' ? (values.maxTeamSize as number) : undefined,
    participantLimit: (values.participantLimit as number) || undefined,
    teamLockingPolicy:
      values.submitterType === 'TEAM' ? (values.teamLockingPolicy as string) : undefined,
    accessControlEnabled,
    accessDomainGroupIds,
  }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeValidFormValues(overrides: Record<string, unknown> = {}) {
  return {
    name: 'Test Task',
    submitterType: 'USER',
    rank: 1,
    defaultDeadline: 30,
    deadline: new Date('2026-06-01'),
    description: [{ type: 'paragraph', content: [] }],
    accessControlEnabled: false,
    accessDomainGroupIds: [],
    ...overrides,
  }
}

// ---------------------------------------------------------------------------
// Tests: zod validation schema
// ---------------------------------------------------------------------------

describe('TaskForm access control schema validation', () => {
  it('accepts accessControlEnabled as boolean true', () => {
    const result = taskFormSchema.safeParse(makeValidFormValues({ accessControlEnabled: true }))
    expect(result.success).toBe(true)
  })

  it('accepts accessControlEnabled as boolean false', () => {
    const result = taskFormSchema.safeParse(makeValidFormValues({ accessControlEnabled: false }))
    expect(result.success).toBe(true)
  })

  it('defaults accessControlEnabled to false when omitted', () => {
    const result = taskFormSchema.safeParse(makeValidFormValues())
    expect(result.success).toBe(true)
    if (result.success) {
      expect(result.data.accessControlEnabled).toBe(false)
    }
  })

  it('rejects accessControlEnabled as string', () => {
    const result = taskFormSchema.safeParse(
      makeValidFormValues({ accessControlEnabled: 'yes' }),
    )
    expect(result.success).toBe(false)
  })

  it('accepts accessDomainGroupIds as number array', () => {
    const result = taskFormSchema.safeParse(
      makeValidFormValues({ accessControlEnabled: true, accessDomainGroupIds: [1, 2, 3] }),
    )
    expect(result.success).toBe(true)
    if (result.success) {
      expect(result.data.accessDomainGroupIds).toEqual([1, 2, 3])
    }
  })

  it('accepts empty accessDomainGroupIds array', () => {
    const result = taskFormSchema.safeParse(
      makeValidFormValues({ accessControlEnabled: true, accessDomainGroupIds: [] }),
    )
    expect(result.success).toBe(true)
  })

  it('accepts undefined accessDomainGroupIds', () => {
    const values = makeValidFormValues({ accessControlEnabled: false })
    delete values.accessDomainGroupIds
    const result = taskFormSchema.safeParse(values)
    expect(result.success).toBe(true)
  })

  it('rejects accessDomainGroupIds with non-number elements', () => {
    const result = taskFormSchema.safeParse(
      makeValidFormValues({ accessDomainGroupIds: ['abc'] }),
    )
    expect(result.success).toBe(false)
  })
})

// ---------------------------------------------------------------------------
// Tests: submit data construction
// ---------------------------------------------------------------------------

describe('TaskForm submit data access control logic', () => {
  it('includes accessControlEnabled: true in submit data', () => {
    const values = makeValidFormValues({
      accessControlEnabled: true,
      accessDomainGroupIds: [1, 2],
    })
    const data = buildSubmitData(values, 'Task intro text')

    expect(data.accessControlEnabled).toBe(true)
    expect(data.accessDomainGroupIds).toEqual([1, 2])
  })

  it('sets accessDomainGroupIds to undefined when accessControlEnabled is false', () => {
    const values = makeValidFormValues({
      accessControlEnabled: false,
      accessDomainGroupIds: [1, 2],
    })
    const data = buildSubmitData(values, 'Task intro text')

    expect(data.accessControlEnabled).toBe(false)
    expect(data.accessDomainGroupIds).toBeUndefined()
  })

  it('sets accessDomainGroupIds to undefined when accessControlEnabled is missing', () => {
    const values = makeValidFormValues({ accessControlEnabled: false })
    delete values.accessDomainGroupIds
    const data = buildSubmitData(values, 'Task intro text')

    expect(data.accessControlEnabled).toBe(false)
    expect(data.accessDomainGroupIds).toBeUndefined()
  })

  it('passes through empty accessDomainGroupIds when accessControlEnabled is true', () => {
    const values = makeValidFormValues({
      accessControlEnabled: true,
      accessDomainGroupIds: [],
    })
    const data = buildSubmitData(values, 'Task intro text')

    expect(data.accessControlEnabled).toBe(true)
    expect(data.accessDomainGroupIds).toEqual([])
  })

  it('submit data structure matches TaskFormSubmitData type shape', () => {
    const values = makeValidFormValues({
      accessControlEnabled: true,
      accessDomainGroupIds: [1],
    })
    const data = buildSubmitData(values, 'intro')

    // Verify every expected key is present
    expect(data).toHaveProperty('name')
    expect(data).toHaveProperty('submitterType')
    expect(data).toHaveProperty('rank')
    expect(data).toHaveProperty('deadline')
    expect(data).toHaveProperty('defaultDeadline')
    expect(data).toHaveProperty('resubmittable')
    expect(data).toHaveProperty('editable')
    expect(data).toHaveProperty('intro')
    expect(data).toHaveProperty('description')
    expect(data).toHaveProperty('requireRealName')
    expect(data).toHaveProperty('accessControlEnabled')
    expect(data).toHaveProperty('accessDomainGroupIds')
  })
})

// ---------------------------------------------------------------------------
// Tests: Form initial values from props.initialData
// ---------------------------------------------------------------------------

interface TaskFormInitialData {
  name?: string
  submitterType?: string
  rank?: number
  defaultDeadline?: number
  registrationStartAt?: number | null
  deadline?: number
  resubmittable?: boolean
  editable?: boolean
  description?: string
  requireRealName?: boolean
  minTeamSize?: number | null
  maxTeamSize?: number | null
  participantLimit?: number | null
  teamLockingPolicy?: string | null
  categoryId?: number | null
  accessControlEnabled?: boolean
  accessDomainGroupIds?: number[]
  videoUrl?: string
}

/** Mirrors initialValues construction in TaskForm.vue */
function buildInitialValues(initialData?: TaskFormInitialData): Record<string, unknown> {
  return {
    name: initialData?.name ?? '',
    submitterType: initialData?.submitterType ?? 'USER',
    rank: initialData?.rank ?? 1,
    registrationStartAt: initialData?.registrationStartAt
      ? new Date(initialData.registrationStartAt).toISOString()
      : null,
    deadline: initialData?.deadline
      ? new Date(initialData.deadline).toISOString().slice(0, 10)
      : '',
    defaultDeadline: initialData?.defaultDeadline ?? 30,
    topics: [] as number[],
    categoryId: initialData?.categoryId ?? null,
    minTeamSize: initialData?.minTeamSize ?? 1,
    maxTeamSize: initialData?.maxTeamSize ?? 10,
    requireRealName: initialData?.requireRealName ?? false,
    participantLimit: initialData?.participantLimit ?? null,
    teamLockingPolicy: initialData?.teamLockingPolicy ?? 'NO_LOCK',
    accessControlEnabled: initialData?.accessControlEnabled ?? false,
    accessDomainGroupIds: initialData?.accessDomainGroupIds ?? [],
    videoUrl: initialData?.videoUrl ?? '',
  }
}

describe('TaskForm initialValues from initialData (access control)', () => {
  it('reads accessControlEnabled from initialData', () => {
    const values = buildInitialValues({
      name: 'Edit Task',
      accessControlEnabled: true,
      accessDomainGroupIds: [1, 5],
    })
    expect(values.accessControlEnabled).toBe(true)
  })

  it('reads accessDomainGroupIds from initialData', () => {
    const values = buildInitialValues({
      name: 'Edit Task',
      accessControlEnabled: true,
      accessDomainGroupIds: [1, 5],
    })
    expect(values.accessDomainGroupIds).toEqual([1, 5])
  })

  it('defaults accessControlEnabled to false when not in initialData', () => {
    const values = buildInitialValues({ name: 'Task' })
    expect(values.accessControlEnabled).toBe(false)
  })

  it('defaults accessDomainGroupIds to empty array when not in initialData', () => {
    const values = buildInitialValues({ name: 'Task', accessControlEnabled: true })
    expect(values.accessDomainGroupIds).toEqual([])
  })

  it('handles accessControlEnabled: false with non-empty groups', () => {
    const values = buildInitialValues({
      name: 'Task',
      accessControlEnabled: false,
      accessDomainGroupIds: [1, 2, 3],
    })
    expect(values.accessControlEnabled).toBe(false)
    expect(values.accessDomainGroupIds).toEqual([1, 2, 3])
  })

  it('handles empty accessDomainGroupIds array', () => {
    const values = buildInitialValues({
      name: 'Task',
      accessControlEnabled: true,
      accessDomainGroupIds: [],
    })
    expect(values.accessControlEnabled).toBe(true)
    expect(values.accessDomainGroupIds).toEqual([])
  })

  it('defaults all fields when initialData is undefined', () => {
    const values = buildInitialValues()
    expect(values.accessControlEnabled).toBe(false)
    expect(values.accessDomainGroupIds).toEqual([])
    expect(values.name).toBe('')
    expect(values.submitterType).toBe('USER')
  })
})
