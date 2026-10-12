/** 第四批独立场景：申请与邀请、学习看板。所有取数与写操作留在路由容器。 */
import type { CatalogEntry, CatalogNeed } from './catalog'

import {
  EMPTY_LEARNING_QUEUES,
  LEARNING_FILTERS,
  LEARNING_OUTLINE,
  LEARNING_QUESTIONS,
  LEARNING_QUEUES,
  PENDING_INVITATIONS,
  PENDING_REQUESTS,
  PROJECT_INVITATIONS,
} from './catalogScenesMisc3Fixtures'

import { t } from '@/i18n'
import LearningView from '@/views/spaces/detail/analytics/LearningView.vue'
import PendingView from '@/views/teams/PendingView.vue'

const UI_T_R: CatalogNeed[] = ['vuetify', 'i18n', 'router']

export const SCENES_MISC3_ENTRIES: CatalogEntry[] = [
  {
    id: 'pending-view',
    title: 'PendingView',
    get about() { return t('catalog.pendingView.about') },
    file: 'src/views/teams/PendingView.vue',
    component: PendingView,
    needs: UI_T_R,
    args: { myRequests: PENDING_REQUESTS, myInvitations: PENDING_INVITATIONS, projectInvitations: PROJECT_INVITATIONS,
      loadingMyRequests: false, loadingMyInvitations: false, loadingProjectInvitations: false,
      failedMyRequests: false, failedMyInvitations: false, failedProjectInvitations: false,
      myRequestsError: null, myInvitationsError: null, projectInvitationsError: null, answering: null, },
    states: [
      {
        get name() { return t('catalog.pendingView.s1.name') },
        get note() { return t('catalog.pendingView.s1.note') },
        props: {},
        expect: '光学实验',
      },
      {
        get name() { return t('catalog.pendingView.s2.name') },
        get note() { return t('catalog.pendingView.s2.note') },
        props: { myRequests: [], myInvitations: [], projectInvitations: [], loadingMyRequests: true },
        expectSelector: '.v-progress-circular',
      },
      {
        get name() { return t('catalog.pendingView.s3.name') },
        get note() { return t('catalog.pendingView.s3.note') },
        props: { myInvitations: [], projectInvitations: [], loadingMyInvitations: true },
        expectSelector: '.v-progress-circular',
      },
      {
        get name() { return t('catalog.pendingView.s4.name') },
        get note() { return t('catalog.pendingView.s4.note') },
        props: { projectInvitations: [], loadingProjectInvitations: true },
        expectSelector: '.v-progress-circular',
      },
      {
        get name() { return t('catalog.pendingView.s5.name') },
        get note() { return t('catalog.pendingView.s5.note') },
        props: { myRequests: [], myInvitations: [], projectInvitations: [] },
        expect: '暂无项目邀请',
      },
      {
        get name() { return t('catalog.pendingView.s6.name') },
        get note() { return t('catalog.pendingView.s6.note') },
        props: { myRequests: [], failedMyRequests: true, myRequestsError: '申请接口超时' },
        expect: '申请接口超时',
      },
      {
        get name() { return t('catalog.pendingView.s7.name') },
        get note() { return t('catalog.pendingView.s7.note') },
        props: { myInvitations: [], failedMyInvitations: true, myInvitationsError: '团队邀请接口超时' },
        expect: '团队邀请接口超时',
      },
      {
        get name() { return t('catalog.pendingView.s8.name') },
        get note() { return t('catalog.pendingView.s8.note') },
        props: { projectInvitations: [], failedProjectInvitations: true, projectInvitationsError: '项目邀请接口超时' },
        expect: '项目邀请接口超时',
      },
      {
        get name() { return t('catalog.pendingView.s9.name') },
        get note() { return t('catalog.pendingView.s9.note') },
        props: { answering: PROJECT_INVITATIONS[0].id },
        expectSelector: 'button:disabled',
      },
      {
        get name() { return t('catalog.pendingView.s10.name') },
        get note() { return t('catalog.pendingView.s10.note') },
        props: { loadingMyRequests: true, loadingMyInvitations: true, loadingProjectInvitations: true },
        expect: '光学实验', expectSelector: '.v-progress-circular',
      },
    ],
  },
  {
    id: 'learning-view',
    title: 'LearningView',
    get about() { return t('catalog.learningView.about') },
    file: 'src/views/spaces/detail/analytics/LearningView.vue',
    component: LearningView,
    needs: UI_T_R,
    args: { loading: false, outlineLoading: false, learningFilters: LEARNING_FILTERS, queues: LEARNING_QUEUES,
      questions: LEARNING_QUESTIONS, outline: null, failed: false, errorDetail: null,
      selected: [], student: null, knowledgePoint: null, },
    states: [
      {
        get name() { return t('catalog.learningView.s1.name') },
        get note() { return t('catalog.learningView.s1.note') },
        props: {},
        expect: '尚未记录需要老师复核的信号',
      },
      {
        get name() { return t('catalog.learningView.s2.name') },
        get note() { return t('catalog.learningView.s2.note') },
        props: { loading: true, learningFilters: null, queues: null, questions: [] },
        expectSelector: '.v-progress-linear',
      },
      {
        get name() { return t('catalog.learningView.s3.name') },
        get note() { return t('catalog.learningView.s3.note') },
        props: { failed: true, errorDetail: '学习队列接口超时' },
        expect: '学习队列接口超时',
      },
      {
        get name() { return t('catalog.learningView.s4.name') },
        get note() { return t('catalog.learningView.s4.note') },
        props: { queues: EMPTY_LEARNING_QUEUES, questions: [] },
        expect: '暂无成员发言',
      },
      {
        get name() { return t('catalog.learningView.s5.name') },
        get note() { return t('catalog.learningView.s5.note') },
        props: { learningFilters: { ...LEARNING_FILTERS, projectCount: 0 }, failed: true, errorDetail: '学习队列接口超时' },
        expect: '暂无可查看的成员项目',
      },
      {
        get name() { return t('catalog.learningView.s6.name') },
        get note() { return t('catalog.learningView.s6.note') },
        props: { student: 'alice', knowledgePoint: 7, selected: ['b-1'] },
        expectSelector: 'input:checked',
      },
      {
        get name() { return t('catalog.learningView.s7.name') },
        get note() { return t('catalog.learningView.s7.note') },
        props: { selected: ['b-1', 'b-2'], outline: LEARNING_OUTLINE },
        expect: '光学讲解提纲',
      },
      {
        get name() { return t('catalog.learningView.s8.name') },
        get note() { return t('catalog.learningView.s8.note') },
        props: { loading: true, outlineLoading: true, selected: ['b-1'], outline: LEARNING_OUTLINE },
        expect: '光学讲解提纲', expectSelector: '.v-btn--loading',
      },
      {
        get name() { return t('catalog.learningView.s9.name') },
        get note() { return t('catalog.learningView.s9.note') },
        props: { queues: { ...LEARNING_QUEUES, reviewFlag: { available: true, reason: '', items: LEARNING_QUESTIONS } } },
        expect: '因为瑞利散射，短波长的光被散射得更多。',
      },
    ],
  },
]
