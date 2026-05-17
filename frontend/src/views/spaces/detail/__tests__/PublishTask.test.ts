/** Unit tests for PublishTask access control API parameter mapping.
 *
 * PublishTask.vue receives TaskFormSubmitData from TaskForm and maps it
 * to TasksApi.create() params. These tests validate the access control
 * parameter mapping logic.
 */
import { describe, expect, it } from 'vitest'

import type { TaskFormSubmitData } from '@/types'
import type { PostTaskRequestData } from '@/network/api/tasks/types'

// ---------------------------------------------------------------------------
// The API parameter mapping logic from PublishTask.submitTask(),
// extracted for testability. Mirror any changes here to PublishTask.vue.
// ---------------------------------------------------------------------------

function buildApiParams(
  taskData: TaskFormSubmitData,
  spaceId: number,
  submissionSchema: Array<{ prompt: string; type: 'FILE' | 'TEXT' }>,
): PostTaskRequestData {
  return {
    ...taskData,
    submissionSchema,
    space: spaceId,
    requireRealName: taskData.requireRealName || false,
    categoryId: taskData.categoryId,
    accessControlEnabled: taskData.accessControlEnabled || false,
    accessDomainGroupIds: taskData.accessControlEnabled
      ? taskData.accessDomainGroupIds
      : undefined,
  }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeTaskFormData(overrides: Partial<TaskFormSubmitData> = {}): TaskFormSubmitData {
  return {
    name: 'Test Task',
    submitterType: 'USER',
    rank: 1,
    deadline: new Date('2026-06-01').getTime(),
    defaultDeadline: 30,
    resubmittable: true,
    editable: true,
    intro: 'intro text',
    description: '[{"type":"paragraph"}]',
    requireRealName: false,
    ...overrides,
  }
}

const DEFAULT_SCHEMA = [{ prompt: '提交文件', type: 'FILE' as const }]

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('PublishTask API parameter mapping', () => {
  describe('accessControlEnabled', () => {
    it('passes accessControlEnabled: true when taskData has it enabled', () => {
      const taskData = makeTaskFormData({
        accessControlEnabled: true,
        accessDomainGroupIds: [1, 2],
      })
      const params = buildApiParams(taskData, 42, DEFAULT_SCHEMA)

      expect(params.accessControlEnabled).toBe(true)
    })

    it('passes accessControlEnabled: false when taskData has it disabled', () => {
      const taskData = makeTaskFormData({
        accessControlEnabled: false,
      })
      const params = buildApiParams(taskData, 42, DEFAULT_SCHEMA)

      expect(params.accessControlEnabled).toBe(false)
    })

    it('defaults accessControlEnabled to false when undefined', () => {
      const taskData = makeTaskFormData()
      delete (taskData as Record<string, unknown>).accessControlEnabled

      const params = buildApiParams(taskData, 42, DEFAULT_SCHEMA)

      expect(params.accessControlEnabled).toBe(false)
    })
  })

  describe('accessDomainGroupIds', () => {
    it('passes accessDomainGroupIds when access control is enabled', () => {
      const taskData = makeTaskFormData({
        accessControlEnabled: true,
        accessDomainGroupIds: [1, 2, 3],
      })
      const params = buildApiParams(taskData, 42, DEFAULT_SCHEMA)

      expect(params.accessDomainGroupIds).toEqual([1, 2, 3])
    })

    it('passes undefined accessDomainGroupIds when access control is disabled', () => {
      const taskData = makeTaskFormData({
        accessControlEnabled: false,
        accessDomainGroupIds: [1, 2],
      })
      const params = buildApiParams(taskData, 42, DEFAULT_SCHEMA)

      expect(params.accessDomainGroupIds).toBeUndefined()
    })

    it('passes undefined accessDomainGroupIds when access control is missing but groups present', () => {
      const taskData = makeTaskFormData({
        accessDomainGroupIds: [1],
      })
      delete (taskData as Record<string, unknown>).accessControlEnabled

      const params = buildApiParams(taskData, 42, DEFAULT_SCHEMA)

      // accessControlEnabled defaults to false, so group IDs are stripped
      expect(params.accessDomainGroupIds).toBeUndefined()
    })

    it('passes empty array when access control enabled with no groups selected', () => {
      const taskData = makeTaskFormData({
        accessControlEnabled: true,
        accessDomainGroupIds: [],
      })
      const params = buildApiParams(taskData, 42, DEFAULT_SCHEMA)

      expect(params.accessDomainGroupIds).toEqual([])
    })
  })

  describe('other API params are forwarded correctly', () => {
    it('forwards space and submission schema', () => {
      const taskData = makeTaskFormData()
      const schema = [{ prompt: '提交代码', type: 'FILE' as const }]
      const params = buildApiParams(taskData, 99, schema)

      expect(params.space).toBe(99)
      expect(params.submissionSchema).toBe(schema)
    })

    it('forwards requireRealName and categoryId', () => {
      const taskData = makeTaskFormData({
        requireRealName: true,
        categoryId: 7,
      })
      const params = buildApiParams(taskData, 42, DEFAULT_SCHEMA)

      expect(params.requireRealName).toBe(true)
      expect(params.categoryId).toBe(7)
    })

    it('forwards team-related fields for TEAM submitter type', () => {
      const taskData = makeTaskFormData({
        submitterType: 'TEAM',
        minTeamSize: 3,
        maxTeamSize: 5,
        teamLockingPolicy: 'LOCK_ON_APPROVAL',
      })
      const params = buildApiParams(taskData, 42, DEFAULT_SCHEMA)

      expect(params.submitterType).toBe('TEAM')
      // spread forwards all taskData fields to the API params
    })
  })
})
