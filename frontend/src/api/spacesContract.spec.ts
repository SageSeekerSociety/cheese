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
  getJoinLink: { method: 'GET', path: '/spaces/7/join-link', args: [7] },
  updateJoinLink: {
    method: 'PATCH',
    path: '/spaces/7/join-link',
    args: [7, { approval: true }],
    body: { approval: true },
  },
  resetJoinLink: { method: 'POST', path: '/spaces/7/join-link/reset', args: [7] },
  listJoinRequests: { method: 'GET', path: '/spaces/7/join-requests', args: [7] },
  approveJoinRequest: { method: 'POST', path: '/spaces/7/join-requests/9/approve', args: [7, 9] },
  rejectJoinRequest: { method: 'POST', path: '/spaces/7/join-requests/9/reject', args: [7, 9] },
  // 令牌来自地址栏：编码后才进路径，一个斜杠改不了请求的是哪条路由。
  detailByJoinLink: { method: 'GET', path: '/space-invites/a%2Fb', args: ['a/b'] },
  joinByJoinLink: { method: 'POST', path: '/space-invites/a%2Fb/join', args: ['a/b'] },
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
    args: [7, { knowledgePoint: 3 }],
    query: [['knowledgePoint', '3']],
  },
  getLearningQueues: {
    method: 'GET',
    path: '/spaces/7/analytics/learning/queues',
    args: [7, { from: 1, to: 2 }],
    query: [
      ['from', '1'],
      ['to', '2'],
    ],
  },
  getLearningClassOverview: { method: 'GET', path: '/spaces/7/learning-analysis/class/overview', args: [7] },
  getLearningClassPoints: {
    method: 'GET',
    path: '/spaces/7/learning-analysis/class/points',
    args: [7, { askedRatio: 0.5 }],
    query: [['askedRatio', '0.5']],
  },
  getLearningClassPoint: { method: 'GET', path: '/spaces/7/learning-analysis/class/points/3', args: [7, 3] },
  pushLearningPractice: { method: 'POST', path: '/spaces/7/learning-analysis/class/points/3/push', args: [7, 3] },
  readMyLearningPractice: { method: 'GET', path: '/spaces/7/learning-analysis/me/practice', args: [7] },
  startLearningPractice: {
    method: 'POST',
    path: '/spaces/7/learning-analysis/me/practice',
    args: [7, { knowledgePointId: 3, fresh: true }],
    body: { knowledgePointId: 3, fresh: true },
  },
  readLearningPractice: { method: 'GET', path: '/spaces/7/learning-analysis/me/practice/5', args: [7, 5] },
  answerLearningPractice: {
    method: 'POST',
    path: '/spaces/7/learning-analysis/me/practice/5/answers',
    args: [7, 5, { question: 1, choice: 2 }],
    body: { question: 1, choice: 2 },
  },
  completeLearningPractice: {
    method: 'POST',
    path: '/spaces/7/learning-analysis/me/practice/5/complete',
    args: [7, 5],
  },
  explainLearningPoint: {
    method: 'POST',
    path: '/spaces/7/learning-analysis/me/explanation',
    args: [7, { knowledgePointId: 3 }],
    body: { knowledgePointId: 3 },
  },
  getLearningClassQuestions: { method: 'GET', path: '/spaces/7/learning-analysis/class/questions', args: [7] },
  getLearningClassExcerpts: {
    method: 'GET',
    path: '/spaces/7/learning-analysis/class/excerpts',
    args: [7, { cluster: 4 }],
    query: [['cluster', '4']],
  },
  getLearningClassWords: { method: 'GET', path: '/spaces/7/learning-analysis/class/words', args: [7] },
  getLearningClassTasks: { method: 'GET', path: '/spaces/7/learning-analysis/class/tasks', args: [7] },
  getLearningClassBehavior: { method: 'GET', path: '/spaces/7/learning-analysis/class/behavior', args: [7] },
  getLearningClassTrend: { method: 'GET', path: '/spaces/7/learning-analysis/class/trend', args: [7] },
  exportLearningClassReport: {
    method: 'POST',
    path: '/spaces/7/learning-analysis/class/export',
    args: [7, { format: 'xlsx', sections: ['points'] }],
    body: { format: 'xlsx', sections: ['points'] },
    blob: true,
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
