// 这一份是「不再被路由拖下水」的机械验收：从前 115 个组件掉到 D 级只是因为它们
// 自己 `useRouter().push()` 或 `useRoute().params`，其中这一批连 store 和接口都不碰
// ——它们本来该是「给 props 就能单独渲染」的那一档。
//
// 现在它们读路由只走 `composables/useNavigation`（组件链接走
// `components/common/NavLink.vue`），所以**不装 vue-router 也能挂起来**。这里逐个
// 挂一遍：给的是产品里的真 props，装的是产品自己的两样（Vuetify + i18n），
// **不给路由、不给 pinia** —— 挂不上、或者画不出该有的东西，就是这条接缝又漏了。
//
// 为什么还是要 Vuetify：`<v-skeleton-loader>`、`<v-list-item>` 这些是产品的画法，
// 不是路由那一层的东西；Vuetify 少一个 provide 就直接抛（display / layout /
// defaults），和「有没有路由」无关。这条线画在「路由和 pinia」上，也正是这批组件
// 从前缺的那两样：从前它们各要一个 `createRouter()` + `createPinia()` 才肯渲染。
//
// 名单里的 `components/**` 七个是全仓 `components/` 下仅有的这一类；另外四个是同一
// 形状的视图级组件（LearningQuoteItem / AnalyticsNavigationTabs / 404 /
// DetailAnswerList），路由同样是它们唯一的全局依赖。
import type { Component } from 'vue'
import type { RouteLocationRaw } from 'vue-router'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render } from '@testing-library/vue'
import { beforeAll, describe, expect, it, vi } from 'vitest'

import AdminActionList from '@/components/admin/AdminActionList.vue'
import AdminBarChart from '@/components/admin/AdminBarChart.vue'
import AdminKpiCard from '@/components/admin/AdminKpiCard.vue'
import AdminNumberList from '@/components/admin/AdminNumberList.vue'
import AppPage from '@/components/common/AppPage.vue'
import MobileActionSheet from '@/components/common/MobileActionSheet.vue'
import BottomAppBar from '@/components/common/Navigation/BottomAppBar.vue'
import i18n, { setLocale } from '@/i18n'
import NotFound from '@/views/404.vue'
import AnalyticsNavigationTabs from '@/views/spaces/detail/analytics/components/AnalyticsNavigationTabs.vue'
import LearningQuoteItem from '@/views/spaces/detail/analytics/components/LearningQuoteItem.vue'

// 装了产品自己的主题和语言，**没有 vue-router、没有 pinia**：这份清单要证的就是
// 「这两样不在场也能渲染」。
const plugins = [createVuetify({ components, directives }), i18n]

/** 「这一颗挂出来了」的判据：给一段它自己画的文字。 */
interface Case {
  name: string
  component: Component
  props: Record<string, unknown>
  /** 挂出来之后页面上该有的文字。 */
  text: string
  /** 有这一条时：这片区域里那个「去处」画成了没有 href 的 `<a>`（不是 router-link）。 */
  inertLink?: string
  /** 需要坐在某个父组件里的（Vuetify 的布局 / 抽屉本来就要求如此），包一层。 */
  host?: 'layout'
  stubs?: Record<string, Component>
  /** 这一颗画出来的东西不在 container 里（浮层传送到 body）。 */
  where?: 'body'
}

const TO = (path: string): RouteLocationRaw => ({ path })

/** 挂在 Vuetify 布局里的一层壳：底部动作面板、底栏本来就长在 layout 里面。
 *  这一层是「产品里的位置」，不是路由那一层的东西。 */
const IN_LAYOUT = {
  template: '<v-layout><Case v-bind="$attrs" /><slot /></v-layout>',
}

const adminQueue: RouteLocationRaw = TO('/admin/feedback')

const CASES: Case[] = [
  {
    name: 'AdminKpiCard',
    component: AdminKpiCard as unknown as Component,
    props: {
      label: '待分诊',
      value: '12,048',
      unit: '条',
      to: adminQueue,
      delta: '+12.3%',
      deltaTitle: '上一周期（再前 7 天）：10,727',
      spark: [3, 5, 4, 8, 6, null, 9],
    },
    text: '12,048',
    inertLink: '.akpi__link',
  },
  {
    name: 'AdminBarChart',
    component: AdminBarChart as unknown as Component,
    props: {
      title: '反馈状态分布',
      rows: [
        { id: 'received', label: '待处理', value: 42, to: adminQueue },
        { id: 'in_progress', label: '处理中', value: 17, to: adminQueue },
        { id: 'resolved', label: '已解决', value: 118 },
      ],
    },
    text: '已解决',
    inertLink: '.abr__row--link',
  },
  {
    name: 'AdminNumberList',
    component: AdminNumberList as unknown as Component,
    props: {
      title: '需处理 · 1',
      rows: [
        {
          id: 'f-1',
          no: 1024,
          title: '导出一个月的数据要等四十秒',
          status: 'received',
          updatedAt: '2026-09-28T09:12:00Z',
        },
      ],
      moreTo: adminQueue,
    },
    text: '导出一个月的数据要等四十秒',
    inertLink: '.anl__row',
  },
  {
    name: 'AdminActionList',
    component: AdminActionList as unknown as Component,
    props: {
      title: '等你处理',
      empty: '今天没有卡住的事',
      rows: [
        {
          id: 'task:2100',
          title: '合并 #2100（前端边界闸）',
          subtitle: '任务',
          tone: 'warn',
          statusLabel: '待评审',
          age: '3 小时前',
          to: TO('/topics/t-1'),
        },
      ],
      moreTo: adminQueue,
    },
    text: '合并 #2100（前端边界闸）',
    inertLink: '.aal__link',
  },
  {
    name: 'AppPage',
    component: AppPage as unknown as Component,
    props: { title: '项目设置', parent: { label: '成员', to: TO('/projects/p1/members') } },
    text: '项目设置',
    inertLink: '.app-page__parent',
  },
  {
    name: 'MobileActionSheet',
    component: MobileActionSheet as unknown as Component,
    props: {
      modelValue: true,
      title: '话题',
      actions: [
        { key: 'rename', label: '重命名', icon: 'mdi-pencil', to: TO('/topics/t-1/rename') },
        { key: 'delete', label: '删除话题', icon: 'mdi-trash-can-outline', danger: true },
      ],
    },
    text: '重命名',
    host: 'layout',
    where: 'body',
  },
  {
    name: 'BottomAppBar',
    component: BottomAppBar as unknown as Component,
    props: {
      items: [
        { key: 'spaces', type: 'item', title: '空间', icon: 'mdi-view-grid-outline', to: '/spaces' },
        { key: 'inbox', type: 'item', title: '待办', icon: 'mdi-inbox-outline', to: '/inbox', badge: 3 },
      ],
    },
    text: '待办',
    // v-bottom-navigation 要坐在一个 layout 里，和它在 App.vue 里的位置一样。
    host: 'layout',
  },
  {
    name: 'AnalyticsNavigationTabs',
    component: AnalyticsNavigationTabs as unknown as Component,
    props: {},
    text: '总览',
  },
  {
    name: 'LearningQuoteItem',
    component: LearningQuoteItem as unknown as Component,
    props: {
      excerpt: {
        blockId: 'b-1',
        topicId: 't-1',
        projectId: 'p1',
        student: 'alice',
        studentName: '爱丽丝',
        topicTitle: '第 3 题：为什么天空是蓝的',
        createdAt: Date.parse('2026-09-28T09:12:00Z'),
        quote: '因为瑞利散射，短波长的光被散射得更多。',
        knowledgePoint: '瑞利散射',
      },
    },
    text: '因为瑞利散射，短波长的光被散射得更多。',
  },
  {
    name: '404',
    component: NotFound as unknown as Component,
    props: {},
    text: '页面不存在',
  },
]

beforeAll(() => {
  setLocale('zh-CN')
  // 抽屉和弹窗是 VOverlay，少了这两个全局对象在 happy-dom 里根本不渲染。
  vi.stubGlobal('visualViewport', {
    width: 1024,
    height: 768,
    offsetLeft: 0,
    offsetTop: 0,
    scale: 1,
    addEventListener() {},
    removeEventListener() {},
  })
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  )
})

describe('这些组件不再需要路由', () => {
  for (const c of CASES) {
    it(`${c.name}：不给路由、不给 pinia 也画得出来`, () => {
      const host = c.host ? { ...IN_LAYOUT, components: { Case: c.component } } : c.component
      const { container } = render(host as unknown as Component, {
        props: c.props,
        global: { plugins, stubs: c.stubs },
      })
      const where = c.where === 'body' ? document.body : container
      expect(where.textContent).toContain(c.text)
      if (c.inertLink) {
        const link = container.querySelector(c.inertLink)
        // 去处照旧画在这一行的位置（数据一列不少），只是没有路由时它不是一条链接：
        // 没有 href，也就点不动、进不了 Tab 顺序。
        expect(link).toBeTruthy()
        expect(link?.hasAttribute('href')).toBe(false)
      }
    })
  }
})
