/** 第四批独立场景：申请与邀请、学习看板。所有取数与写操作留在路由容器。 */
import type { CatalogEntry, CatalogNeed } from './catalog'

import {
  LEARNING_CLASS_BEHAVIOR,
  LEARNING_CLASS_OFF,
  LEARNING_CLASS_OVERVIEW,
  LEARNING_CLASS_POINT,
  LEARNING_CLASS_POINTS,
  LEARNING_CLASS_QUESTIONS,
  LEARNING_CLASS_TASKS,
  LEARNING_CLASS_TREND,
  LEARNING_CLASS_WORDS,
} from './catalogLearningClassFixtures'
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
import LearningAnalysisView from '@/views/spaces/detail/analytics/LearningAnalysisView.vue'
import LearningView from '@/views/spaces/detail/analytics/LearningView.vue'
import PendingView from '@/views/teams/PendingView.vue'

const UI_T_R: CatalogNeed[] = ['vuetify', 'i18n', 'router']

export const SCENES_MISC3_ENTRIES: CatalogEntry[] = [
  {
    id: 'pending-view',
    title: 'PendingView',
    get about() {
      return t('catalog.pendingView.about')
    },
    file: 'src/views/teams/PendingView.vue',
    component: PendingView,
    needs: UI_T_R,
    args: {
      myRequests: PENDING_REQUESTS,
      myInvitations: PENDING_INVITATIONS,
      projectInvitations: PROJECT_INVITATIONS,
      loadingMyRequests: false,
      loadingMyInvitations: false,
      loadingProjectInvitations: false,
      failedMyRequests: false,
      failedMyInvitations: false,
      failedProjectInvitations: false,
      myRequestsError: null,
      myInvitationsError: null,
      projectInvitationsError: null,
      answering: null,
    },
    states: [
      {
        get name() {
          return t('catalog.pendingView.s1.name')
        },
        get note() {
          return t('catalog.pendingView.s1.note')
        },
        props: {},
        expect: '光学实验',
      },
      {
        get name() {
          return t('catalog.pendingView.s2.name')
        },
        get note() {
          return t('catalog.pendingView.s2.note')
        },
        props: { myRequests: [], myInvitations: [], projectInvitations: [], loadingMyRequests: true },
        expectSelector: '.v-progress-circular',
      },
      {
        get name() {
          return t('catalog.pendingView.s3.name')
        },
        get note() {
          return t('catalog.pendingView.s3.note')
        },
        props: { myInvitations: [], projectInvitations: [], loadingMyInvitations: true },
        expectSelector: '.v-progress-circular',
      },
      {
        get name() {
          return t('catalog.pendingView.s4.name')
        },
        get note() {
          return t('catalog.pendingView.s4.note')
        },
        props: { projectInvitations: [], loadingProjectInvitations: true },
        expectSelector: '.v-progress-circular',
      },
      {
        get name() {
          return t('catalog.pendingView.s5.name')
        },
        get note() {
          return t('catalog.pendingView.s5.note')
        },
        props: { myRequests: [], myInvitations: [], projectInvitations: [] },
        expect: '暂无项目邀请',
      },
      {
        get name() {
          return t('catalog.pendingView.s6.name')
        },
        get note() {
          return t('catalog.pendingView.s6.note')
        },
        props: { myRequests: [], failedMyRequests: true, myRequestsError: '申请接口超时' },
        expect: '申请接口超时',
      },
      {
        get name() {
          return t('catalog.pendingView.s7.name')
        },
        get note() {
          return t('catalog.pendingView.s7.note')
        },
        props: { myInvitations: [], failedMyInvitations: true, myInvitationsError: '团队邀请接口超时' },
        expect: '团队邀请接口超时',
      },
      {
        get name() {
          return t('catalog.pendingView.s8.name')
        },
        get note() {
          return t('catalog.pendingView.s8.note')
        },
        props: { projectInvitations: [], failedProjectInvitations: true, projectInvitationsError: '项目邀请接口超时' },
        expect: '项目邀请接口超时',
      },
      {
        get name() {
          return t('catalog.pendingView.s9.name')
        },
        get note() {
          return t('catalog.pendingView.s9.note')
        },
        props: { answering: PROJECT_INVITATIONS[0].id },
        expectSelector: '.v-btn--disabled',
      },
      {
        get name() {
          return t('catalog.pendingView.s10.name')
        },
        get note() {
          return t('catalog.pendingView.s10.note')
        },
        props: { loadingMyRequests: true, loadingMyInvitations: true, loadingProjectInvitations: true },
        expect: '光学实验',
        expectSelector: '.v-progress-circular',
      },
    ],
  },
  {
    id: 'learning-class-view',
    title: 'LearningAnalysisView',
    get about() {
      return t('catalog.learningClassView.about')
    },
    file: 'src/views/spaces/detail/analytics/LearningAnalysisView.vue',
    component: LearningAnalysisView,
    needs: UI_T_R,
    args: { sub: 'overview', enabled: true, overview: LEARNING_CLASS_OVERVIEW },
    states: [
      {
        get name() {
          return t('catalog.learningClassView.s1.name')
        },
        get note() {
          return t('catalog.learningClassView.s1.note')
        },
        props: {},
        expect: '下节课值得讲的',
      },
      {
        get name() {
          return t('catalog.learningClassView.s2.name')
        },
        get note() {
          return t('catalog.learningClassView.s2.note')
        },
        props: { sub: 'points', points: LEARNING_CLASS_POINTS },
        expect: '不足 3 人，不分析',
      },
      {
        get name() {
          return t('catalog.learningClassView.s3.name')
        },
        get note() {
          return t('catalog.learningClassView.s3.note')
        },
        props: { pointId: 2, pointDetail: LEARNING_CLASS_POINT },
        expect: '内层不知道怎样引用外层当前行',
      },
      {
        get name() {
          return t('catalog.learningClassView.s4.name')
        },
        get note() {
          return t('catalog.learningClassView.s4.note')
        },
        props: { sub: 'questions', questions: LEARNING_CLASS_QUESTIONS },
        expect: '成员 A7F3',
      },
      {
        get name() {
          return t('catalog.learningClassView.s5.name')
        },
        get note() {
          return t('catalog.learningClassView.s5.note')
        },
        props: { sub: 'words', words: LEARNING_CLASS_WORDS },
        expect: 'ERROR 1111',
      },
      {
        get name() {
          return t('catalog.learningClassView.s6.name')
        },
        get note() {
          return t('catalog.learningClassView.s6.note')
        },
        props: { sub: 'tasks', tasks: LEARNING_CLASS_TASKS },
        expect: 'T3 子查询与聚合',
      },
      {
        get name() {
          return t('catalog.learningClassView.s7.name')
        },
        get note() {
          return t('catalog.learningClassView.s7.note')
        },
        props: { sub: 'behavior', behavior: LEARNING_CLASS_BEHAVIOR },
        expect: '直接要答案',
      },
      {
        get name() {
          return t('catalog.learningClassView.s8.name')
        },
        get note() {
          return t('catalog.learningClassView.s8.note')
        },
        props: { sub: 'trend', trend: LEARNING_CLASS_TREND },
        expectSelector: '.lc-trend',
      },
      {
        get name() {
          return t('catalog.learningClassView.s9.name')
        },
        get note() {
          return t('catalog.learningClassView.s9.note')
        },
        props: { enabled: false, overview: LEARNING_CLASS_OFF },
        expect: '这个空间没有开启学情分析',
      },
      {
        get name() {
          return t('catalog.learningClassView.s10.name')
        },
        get note() {
          return t('catalog.learningClassView.s10.note')
        },
        props: { failed: true, errorDetail: '学情分析接口超时' },
        expect: '学情分析接口超时',
      },
    ],
  },
  {
    id: 'learning-view',
    title: 'LearningView',
    get about() {
      return t('catalog.learningView.about')
    },
    file: 'src/views/spaces/detail/analytics/LearningView.vue',
    component: LearningView,
    needs: UI_T_R,
    args: {
      loading: false,
      outlineLoading: false,
      learningFilters: LEARNING_FILTERS,
      queues: LEARNING_QUEUES,
      questions: LEARNING_QUESTIONS,
      outline: null,
      failed: false,
      errorDetail: null,
      selected: [],
      knowledgePoint: null,
    },
    states: [
      {
        get name() {
          return t('catalog.learningView.s1.name')
        },
        get note() {
          return t('catalog.learningView.s1.note')
        },
        props: {},
        expect: '尚未记录需要老师复核的信号',
      },
      {
        get name() {
          return t('catalog.learningView.s2.name')
        },
        get note() {
          return t('catalog.learningView.s2.note')
        },
        props: { loading: true, learningFilters: null, queues: null, questions: [] },
        expectSelector: '.v-progress-linear',
      },
      {
        get name() {
          return t('catalog.learningView.s3.name')
        },
        get note() {
          return t('catalog.learningView.s3.note')
        },
        props: { failed: true, errorDetail: '学习队列接口超时' },
        expect: '学习队列接口超时',
      },
      {
        get name() {
          return t('catalog.learningView.s4.name')
        },
        get note() {
          return t('catalog.learningView.s4.note')
        },
        props: { queues: EMPTY_LEARNING_QUEUES, questions: [] },
        expect: '暂无成员发言',
      },
      {
        get name() {
          return t('catalog.learningView.s5.name')
        },
        get note() {
          return t('catalog.learningView.s5.note')
        },
        props: {
          learningFilters: { ...LEARNING_FILTERS, projectCount: 0 },
          failed: true,
          errorDetail: '学习队列接口超时',
        },
        expect: '暂无可查看的成员项目',
      },
      {
        get name() {
          return t('catalog.learningView.s6.name')
        },
        get note() {
          return t('catalog.learningView.s6.note')
        },
        props: { knowledgePoint: 7, selected: ['b-1'] },
        expectSelector: 'input:checked',
      },
      {
        get name() {
          return t('catalog.learningView.s7.name')
        },
        get note() {
          return t('catalog.learningView.s7.note')
        },
        props: { selected: ['b-1', 'b-2'], outline: LEARNING_OUTLINE },
        expect: '光学讲解提纲',
      },
      {
        get name() {
          return t('catalog.learningView.s8.name')
        },
        get note() {
          return t('catalog.learningView.s8.note')
        },
        props: { loading: true, outlineLoading: true, selected: ['b-1'], outline: LEARNING_OUTLINE },
        expect: '光学讲解提纲',
        expectSelector: '.v-btn--loading',
      },
      {
        get name() {
          return t('catalog.learningView.s9.name')
        },
        get note() {
          return t('catalog.learningView.s9.note')
        },
        props: {
          queues: { ...LEARNING_QUEUES, reviewFlag: { available: true, reason: '', items: LEARNING_QUESTIONS } },
        },
        expect: '因为瑞利散射，短波长的光被散射得更多。',
      },
    ],
  },
]
