import type { RouteRecordRaw } from 'vue-router'

import RouterPassThrough from '@/layouts/RouterPassThrough.vue'

export default {
  path: '/questions',
  name: 'Question',
  component: RouterPassThrough,
  meta: {
    titleKey: 'navigation.pages.questions',
    disabled: true,
  },
  children: [
    {
      path: 'ask',
      name: 'QuestionAsk',
      component: () => import('@/views/question/Ask.vue'),
      meta: {
        titleKey: 'navigation.pages.ask',
        disabled: true,
      },
    },
    {
      path: ':questionId(\\d+)',
      name: 'QuestionDetail',
      component: () => import('@/views/question/Detail.vue'),
      meta: {
        titleKey: 'navigation.pages.questionDetail',
        disabled: true,
      },
      children: [
        {
          path: '',
          name: 'QuestionAnswerList',
          component: () => import('@/views/question/DetailAnswerList.vue'),
        },
        {
          path: 'answers/:answerId(\\d+)',
          name: 'QuestionAnswer',
          component: () => import('@/views/question/DetailAnswer.vue'),
        },
      ],
    },
  ],
} as RouteRecordRaw
