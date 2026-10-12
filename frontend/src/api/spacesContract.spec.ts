// @vitest-environment jsdom
// 空间一域：逐个方法钉住它发出去的浏览器请求（方法、地址与查询、鉴权头、JSON/多分体正文），
// 并核对载荷契约 —— JSON 调用直接给 payload，无载荷命令给显式成功值。空间端点在**新 origin**
// （`newer`），多分体上传（`uploadMaterial`）的文件在前、可见性字段在后。钉的是浏览器可见的
// 请求，不是 axios 配置或包装源码。
import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'

import {
  type ContractCase,
  contractEnvironment,
  contractRequest,
  envelope,
  newer,
  server,
  uploadFile,
} from './requestContract.spec.helper'

contractEnvironment()
const cases: Record<string, ContractCase> = {
  applications: {
    method: 'GET',
    path: '/space-applications',
    args: [2],
    query: [
      ['offset', '2'],
      ['limit', '50'],
    ],
  },
  resubmit: {
    method: 'POST',
    path: '/space-applications/7/resubmit',
    args: [7, { name: '空间' }],
    body: { name: '空间' },
  },
  reviews: {
    method: 'GET',
    path: '/admin/spaces',
    args: ['PENDING', 2, 51],
    query: [
      ['status', 'PENDING'],
      ['offset', '2'],
      ['limit', '51'],
    ],
  },
  review: {
    method: 'POST',
    path: '/admin/spaces/7/review',
    args: [7, false, '理由'],
    body: { approved: false, reason: '理由' },
  },
  create: {
    method: 'POST',
    path: '/spaces',
    args: [{ name: '空间', visibleTaskLimit: null }],
    body: { name: '空间', visibleTaskLimit: null },
  },
  join: { method: 'POST', path: '/spaces/join', args: [{ code: 'A&B' }], body: { code: 'A&B' } },
  listMembers: { method: 'GET', path: '/spaces/7/members', args: [7] },
  addMember: { method: 'POST', path: '/spaces/7/members', args: [7, { userId: 9 }], body: { userId: 9 } },
  removeMember: { method: 'DELETE', path: '/spaces/7/members/9', args: [7, 9] },
  leave: { method: 'POST', path: '/spaces/7/leave', args: [7] },
  listAnnouncements: { method: 'GET', path: '/spaces/7/announcements', args: [7] },
  publishAnnouncement: {
    method: 'POST',
    path: '/spaces/7/announcements',
    args: [7, { title: '消息', content: '内容', pinned: false, expiresAt: null }],
    body: { title: '消息', content: '内容', pinned: false, expiresAt: null },
  },
  updateAnnouncement: {
    method: 'PATCH',
    path: '/spaces/7/announcements/9',
    args: [7, 9, { expiresAt: null }],
    body: { expiresAt: null },
  },
  deleteAnnouncement: { method: 'DELETE', path: '/spaces/7/announcements/9', args: [7, 9] },
  listInviteCodes: { method: 'GET', path: '/spaces/7/invite-codes', args: [7] },
  createInviteCode: { method: 'POST', path: '/spaces/7/invite-codes', args: [7], body: {} },
  updateInviteCode: {
    method: 'PATCH',
    path: '/spaces/7/invite-codes/9',
    args: [7, 9, { note: null, expiresAt: null }],
    body: { note: null, expiresAt: null },
  },
  revokeInviteCode: { method: 'DELETE', path: '/spaces/7/invite-codes/9', args: [7, 9] },
  listMaterials: { method: 'GET', path: '/spaces/7/materials', args: [7] },
  uploadMaterial: { method: 'POST', path: '/spaces/7/materials', args: [], fileFields: { visibility: 'members' } },
  updateMaterialVisibility: {
    method: 'PATCH',
    path: '/spaces/7/materials/9',
    args: [7, 9, 'members'],
    body: { visibility: 'members' },
  },
  deleteMaterial: { method: 'DELETE', path: '/spaces/7/materials/9', args: [7, 9] },
  downloadMaterial: { method: 'GET', path: '/spaces/7/materials/9/download', args: [7, 9], blob: true },
  update: {
    method: 'PATCH',
    path: '/spaces/7',
    args: [7, { visibleTaskLimit: null, classificationTopics: [1, 2] }],
    body: { visibleTaskLimit: null, classificationTopics: [1, 2] },
  },
  del: { method: 'DELETE', path: '/spaces/7', args: [7] },
  detail: {
    method: 'GET',
    path: '/spaces/7',
    args: [7, { queryClassificationTopics: true, queryMyRank: false }],
    query: [
      ['queryClassificationTopics', 'true'],
      ['queryMyRank', 'false'],
    ],
  },
  list: {
    method: 'GET',
    path: '/spaces',
    args: [{ pageSize: 20, pageStart: 2, sort_by: 'name', sort_order: 'asc' }],
    query: [
      ['pageSize', '20'],
      ['pageStart', '2'],
      ['sort_by', 'name'],
      ['sort_order', 'asc'],
    ],
  },
  getAnalyticsOverview: {
    method: 'GET',
    path: '/spaces/7/analytics/overview',
    args: [7, { from: 1, taskApproved: 'APPROVED', groupBy: 'week' }],
    query: [
      ['from', '1'],
      ['taskApproved', 'APPROVED'],
      ['groupBy', 'week'],
    ],
  },
  getAnalyticsAlerts: { method: 'GET', path: '/spaces/7/analytics/alerts', args: [7] },
  getAnalyticsPublishers: {
    method: 'GET',
    path: '/spaces/7/analytics/publishers',
    args: [7, { sortBy: 'taskCount', sortOrder: 'desc' }],
    query: [
      ['sortBy', 'taskCount'],
      ['sortOrder', 'desc'],
    ],
  },
  getAnalyticsTasks: {
    method: 'GET',
    path: '/spaces/7/analytics/tasks',
    args: [7, { hasPendingReview: false }],
    query: [['hasPendingReview', 'false']],
  },
  getAnalyticsPeople: { method: 'GET', path: '/spaces/7/analytics/people', args: [7] },
  getAnalyticsParticipants: {
    method: 'GET',
    path: '/spaces/7/analytics/participants',
    args: [7, { realName: 'without', completionStatus: 'SUCCESS' }],
    query: [
      ['realName', 'without'],
      ['completionStatus', 'SUCCESS'],
    ],
  },
  getLearningFilters: { method: 'GET', path: '/spaces/7/analytics/learning/filters', args: [7] },
  getLearningQuestions: {
    method: 'GET',
    path: '/spaces/7/analytics/learning/questions',
    args: [7, { student: '名字 & +' }],
    query: [['student', '名字 & +']],
  },
  getLearningQueues: {
    method: 'GET',
    path: '/spaces/7/analytics/learning/queues',
    args: [7, { student: '名字 & +' }],
    query: [['student', '名字 & +']],
  },
  buildLearningOutline: {
    method: 'POST',
    path: '/spaces/7/analytics/learning/outline',
    args: [7, { blockIds: ['a', 'b'] }],
    body: { blockIds: ['a', 'b'] },
  },
  getMyPublishedTasks: {
    method: 'GET',
    path: '/spaces/7/me/publishing/tasks',
    args: [7, { hasPendingReview: false }],
    query: [['hasPendingReview', 'false']],
  },
  addAdmin: {
    method: 'POST',
    path: '/spaces/7/managers',
    args: [7, { userId: 9, role: 'ADMIN' }],
    body: { userId: 9, role: 'ADMIN' },
  },
  updateAdmin: {
    method: 'PATCH',
    path: '/spaces/7/managers/9',
    args: [7, 9, { role: 'OWNER' }],
    body: { role: 'OWNER' },
  },
  removeAdmin: { method: 'DELETE', path: '/spaces/7/managers/9', args: [7, 9] },
  listCategories: {
    method: 'GET',
    path: '/spaces/7/categories',
    args: [7, { includeArchived: false }],
    query: [['includeArchived', 'false']],
  },
  createCategory: {
    method: 'POST',
    path: '/spaces/7/categories',
    args: [7, { name: '分类', description: null }],
    body: { name: '分类', description: null },
  },
  getCategory: { method: 'GET', path: '/spaces/7/categories/9', args: [7, 9] },
  updateCategory: {
    method: 'PATCH',
    path: '/spaces/7/categories/9',
    args: [7, 9, { description: null }],
    body: { description: null },
  },
  deleteCategory: { method: 'DELETE', path: '/spaces/7/categories/9', args: [7, 9] },
  archiveCategory: { method: 'POST', path: '/spaces/7/categories/9/archive', args: [7, 9] },
  unarchiveCategory: { method: 'DELETE', path: '/spaces/7/categories/9/archive', args: [7, 9] },
  getSpaceTopics: {
    method: 'GET',
    path: '/spaces/7/topics',
    args: [7, 8, 'name', '名字 & +'],
    query: [
      ['limit', '8'],
      ['sort', 'name'],
      ['keyword', '名字 & +'],
    ],
  },
  listDomainGroups: { method: 'GET', path: '/spaces/7/domain-groups', args: [7] },
  createDomainGroup: {
    method: 'POST',
    path: '/spaces/7/domain-groups',
    args: [7, { name: '域', domains: ['a.test', 'b.test'] }],
    body: { name: '域', domains: ['a.test', 'b.test'] },
  },
  updateDomainGroup: {
    method: 'PATCH',
    path: '/spaces/7/domain-groups/9',
    args: [7, 9, { description: null }],
    body: { description: null },
  },
  deleteDomainGroup: { method: 'DELETE', path: '/spaces/7/domain-groups/9', args: [7, 9] },
  getSubmissionQueue: {
    method: 'GET',
    path: '/spaces/7/submissions',
    args: [7, { reviewed: false, pageStart: 2, pageSize: 20 }],
    query: [
      ['reviewed', 'false'],
      ['pageStart', '2'],
      ['pageSize', '20'],
    ],
  },
}

describe('spaces browser contract', () => {
  it.each(['null', 'omitted', '204'])('keeps %s command success distinct from a caught failure', async (kind) => {
    server.use(
      http.delete(`${newer}/spaces/7/members/9`, () =>
        kind === '204'
          ? new HttpResponse(null, { status: 204 })
          : HttpResponse.json(kind === 'null' ? envelope(null) : { code: 200, message: 'ok' })
      )
    )
    const { SpacesApi: next } = await import('./spaces')
    await expect(next.removeMember(7, 9)).resolves.toBe(true)
  })
  it.each(Object.entries(cases))('%s preserves the request and returns the payload', async (name, test) => {
    const { SpacesApi: next } = await import('./spaces')
    const args = name === 'uploadMaterial' ? [7, uploadFile(), 'members'] : test.args
    await contractRequest(test, () =>
      (next[name as keyof typeof next] as (...args: unknown[]) => Promise<unknown>)(...args)
    )
  })
})
