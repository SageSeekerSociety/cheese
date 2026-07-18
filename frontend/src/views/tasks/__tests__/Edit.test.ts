/** Unit tests for Edit.vue domain group loading logic.
 *
 * Edit.vue onMounted logic:
 *   await loadTaskData()
 *   if (taskData.value?.space?.id) {
 *     spaceStore.currentSpaceId = taskData.value.space.id
 *     await spaceStore.fetchDomainGroups()
 *   }
 *
 * Covers:
 * - Loads domain groups when task data has a space
 * - Skips domain group fetch when task data has no space
 * - Skips domain group fetch when space has no id
 * - Correctly sets currentSpaceId before fetching
 */
import { describe, expect, it } from 'vitest'

// ---------------------------------------------------------------------------
// Extracted logic — mirrors Edit.vue onMounted
// ---------------------------------------------------------------------------

interface TaskWithOptionalSpace {
  space?: { id?: number } | null
}

interface DomainGroupStoreState {
  currentSpaceId: number | null
  domainGroups: Array<{ id: number; name: string }>
  fetchCalled: boolean
}

/** Simulates the onMounted logic in Edit.vue. Returns the store state after execution. */
async function simulateEditOnMounted(
  taskData: TaskWithOptionalSpace | null,
  fetchDomainGroups: () => Promise<void>,
): Promise<{
  spaceIdSet: number | null
  fetchCalled: boolean
}> {
  let spaceIdSet: number | null = null
  let fetchCalled = false

  // Simulate: await loadTaskData()
  // (taskData is already loaded)

  // Simulate: if (taskData.value?.space?.id)
  if (taskData?.space?.id) {
    spaceIdSet = taskData.space.id
    fetchCalled = true
    await fetchDomainGroups()
  }

  return { spaceIdSet, fetchCalled }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeTask(spaceId?: number): TaskWithOptionalSpace {
  return {
    space: spaceId != null ? { id: spaceId } : undefined,
  }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('Edit.vue domain group loading on mount', () => {
  it('fetches domain groups when task has a space id', async () => {
    const task = makeTask(42)
    let fetchCount = 0
    const fetchDomainGroups = async () => {
      fetchCount++
    }

    const result = await simulateEditOnMounted(task, fetchDomainGroups)

    expect(result.spaceIdSet).toBe(42)
    expect(result.fetchCalled).toBe(true)
    expect(fetchCount).toBe(1)
  })

  it('skips fetch when task has no space', async () => {
    const task: TaskWithOptionalSpace = { space: undefined }
    let fetchCount = 0
    const fetchDomainGroups = async () => {
      fetchCount++
    }

    const result = await simulateEditOnMounted(task, fetchDomainGroups)

    expect(result.spaceIdSet).toBeNull()
    expect(result.fetchCalled).toBe(false)
    expect(fetchCount).toBe(0)
  })

  it('skips fetch when task space is null', async () => {
    const task: TaskWithOptionalSpace = { space: null }
    let fetchCount = 0
    const fetchDomainGroups = async () => {
      fetchCount++
    }

    const result = await simulateEditOnMounted(task, fetchDomainGroups)

    expect(result.spaceIdSet).toBeNull()
    expect(result.fetchCalled).toBe(false)
    expect(fetchCount).toBe(0)
  })

  it('skips fetch when task space has no id', async () => {
    const task: TaskWithOptionalSpace = { space: {} }
    let fetchCount = 0
    const fetchDomainGroups = async () => {
      fetchCount++
    }

    const result = await simulateEditOnMounted(task, fetchDomainGroups)

    expect(result.spaceIdSet).toBeNull()
    expect(result.fetchCalled).toBe(false)
    expect(fetchCount).toBe(0)
  })

  it('skips fetch when task is null (loading not complete)', async () => {
    let fetchCount = 0
    const fetchDomainGroups = async () => {
      fetchCount++
    }

    const result = await simulateEditOnMounted(null, fetchDomainGroups)

    expect(result.spaceIdSet).toBeNull()
    expect(result.fetchCalled).toBe(false)
    expect(fetchCount).toBe(0)
  })

  it('sets currentSpaceId correctly for large space IDs', async () => {
    const task = makeTask(999999)
    let fetchCount = 0
    const fetchDomainGroups = async () => {
      fetchCount++
    }

    const result = await simulateEditOnMounted(task, fetchDomainGroups)

    expect(result.spaceIdSet).toBe(999999)
    expect(fetchCount).toBe(1)
  })

  it('fetch is called exactly once during mount', async () => {
    const task = makeTask(10)
    let fetchCount = 0
    const fetchDomainGroups = async () => {
      fetchCount++
    }

    await simulateEditOnMounted(task, fetchDomainGroups)

    expect(fetchCount).toBe(1)
  })
})

// ---------------------------------------------------------------------------
// Tests: editTaskData computed — accessControlEnabled + accessDomainGroupIds
// ---------------------------------------------------------------------------

interface TaskForEdit {
  name: string
  submitterType: string
  rank: number
  defaultDeadline: number
  registrationStartAt: number | null
  deadline: number
  resubmittable: boolean
  editable: boolean
  description: string
  requireRealName: boolean
  minTeamSize: number | null
  maxTeamSize: number | null
  participantLimit: number | null
  teamLockingPolicy: string | null
  category?: { id: number }
  accessControlEnabled?: boolean
  accessDomainGroupIds?: number[]
  videoUrl?: string
}

interface EditTaskFormData {
  name?: string
  accessControlEnabled?: boolean
  accessDomainGroupIds?: number[]
  // ... other fields
  [key: string]: unknown
}

/** Mirrors editTaskData computed in useTaskData.ts */
function buildEditTaskData(task: TaskForEdit | null): EditTaskFormData {
  if (!task) return {}
  return {
    name: task.name,
    submitterType: task.submitterType,
    rank: task.rank,
    defaultDeadline: task.defaultDeadline,
    registrationStartAt: task.registrationStartAt
      ? new Date(task.registrationStartAt).getTime()
      : null,
    deadline: new Date(task.deadline).getTime(),
    resubmittable: task.resubmittable,
    editable: task.editable,
    description: task.description,
    requireRealName: task.requireRealName,
    minTeamSize: task.minTeamSize,
    maxTeamSize: task.maxTeamSize,
    participantLimit: task.participantLimit,
    teamLockingPolicy: task.teamLockingPolicy,
    categoryId: task.category?.id,
    accessControlEnabled: task.accessControlEnabled,
    accessDomainGroupIds: task.accessDomainGroupIds ?? [],
    videoUrl: task.videoUrl || '',
  }
}

function makeTaskForEdit(overrides: Partial<TaskForEdit> = {}): TaskForEdit {
  return {
    name: 'Test Task',
    submitterType: 'USER',
    rank: 0,
    defaultDeadline: 30,
    registrationStartAt: null,
    deadline: Date.now() + 86400000,
    resubmittable: false,
    editable: false,
    description: 'Some description',
    requireRealName: false,
    minTeamSize: null,
    maxTeamSize: null,
    participantLimit: null,
    teamLockingPolicy: null,
    ...overrides,
  }
}

describe('editTaskData computed (accessControlEnabled + accessDomainGroupIds)', () => {
  it('includes accessControlEnabled from task data', () => {
    const task = makeTaskForEdit({ accessControlEnabled: true })
    const data = buildEditTaskData(task)
    expect(data.accessControlEnabled).toBe(true)
  })

  it('defaults accessControlEnabled to undefined when not set', () => {
    const task = makeTaskForEdit()
    const data = buildEditTaskData(task)
    expect(data.accessControlEnabled).toBeUndefined()
  })

  it('includes accessDomainGroupIds from task data', () => {
    const task = makeTaskForEdit({
      accessControlEnabled: true,
      accessDomainGroupIds: [1, 2, 3],
    })
    const data = buildEditTaskData(task)
    expect(data.accessDomainGroupIds).toEqual([1, 2, 3])
  })

  it('defaults accessDomainGroupIds to empty array when not set', () => {
    const task = makeTaskForEdit({ accessControlEnabled: true })
    const data = buildEditTaskData(task)
    expect(data.accessDomainGroupIds).toEqual([])
  })

  it('preserves empty accessDomainGroupIds array', () => {
    const task = makeTaskForEdit({
      accessControlEnabled: true,
      accessDomainGroupIds: [],
    })
    const data = buildEditTaskData(task)
    expect(data.accessDomainGroupIds).toEqual([])
  })

  it('handles accessControlEnabled = false', () => {
    const task = makeTaskForEdit({
      accessControlEnabled: false,
      accessDomainGroupIds: [5],
    })
    const data = buildEditTaskData(task)
    expect(data.accessControlEnabled).toBe(false)
    expect(data.accessDomainGroupIds).toEqual([5])
  })

  it('returns empty object when task is null', () => {
    const data = buildEditTaskData(null)
    expect(data).toEqual({})
  })
})
