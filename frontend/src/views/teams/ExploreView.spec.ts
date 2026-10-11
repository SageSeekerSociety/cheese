/**
 * 「发现小队」那一屏拆出来的画法（`ExploreView.vue`）：props 进、事件出。
 *
 * 四件会静默坏掉的事，各钉一条：
 * 1. **搜失败不是「没有找到团队」**：`searchFailed` 时说的是服务端原话加一个重试，
 *    底下那句「没有找到团队」不能再画出来 —— 两个答案撞在一起会让人以为搜的是空结果。
 * 2. **清除键**把搜索框、结果、`hasSearched`、错误四样一起清掉（只清框不清结果，
 *    下一次进来还能看见上次的字）。
 * 3. **提交**（回车 / 表单提交）报的是当下的词，重试报的也是同一个词。
 * 4. 「没有找到团队」只在**搜过了**（`hasSearched`）且结果为空时出现 —— 头一次进来
 *    看到的是招募广场，不是一句「没有找到」。
 */
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import ExploreView from './ExploreView.vue'

import i18n, { setLocale } from '@/i18n'
import { SEARCH_TEAMS } from '@/views/demo/catalogScenesMiscFixtures'

const Blank = { template: '<div />' }
const router = () =>
  createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/t/:handle', name: 'TeamsDetailDefault', component: Blank },
      { path: '/:pathMatch(.*)*', component: Blank },
    ],
  })

function mount(props: Record<string, unknown>) {
  return render(ExploreView, {
    props,
    global: { plugins: [createVuetify({ components, directives }), i18n, router()] },
  })
}

const base = { mdAndUp: false, searchFailed: false, reason: null, forbidden: false }

beforeEach(() => setLocale('zh-CN'))
afterEach(cleanup)

describe('发现小队', () => {
  it('头一次进来画的是招募广场，不是「没有找到团队」', () => {
    const view = mount({ ...base, searchQuery: '', searchTeamsData: [], hasSearched: false, searchError: null })
    expect(view.queryByText('招募广场')).not.toBeNull()
    expect(view.queryByText('没有找到团队')).toBeNull()
  })

  it('搜过了、有结果，就画那一列', () => {
    const view = mount({
      ...base,
      searchQuery: '开源',
      searchTeamsData: SEARCH_TEAMS,
      hasSearched: true,
      searchError: null,
    })
    expect(view.queryByText('开源小伙伴')).not.toBeNull()
    expect(view.queryByText('找到 1 个团队')).not.toBeNull()
    expect(view.queryByText('没有找到团队')).toBeNull()
  })

  it('搜过了、没结果，说「没有找到团队」', () => {
    const view = mount({
      ...base,
      searchQuery: 'zzz',
      searchTeamsData: [],
      hasSearched: true,
      searchError: null,
    })
    expect(view.queryByText('没有找到团队')).not.toBeNull()
  })

  it('搜失败说的是服务端原话，而不是「没有找到团队」', () => {
    const view = mount({
      ...base,
      searchFailed: true,
      reason: 'connect ECONNREFUSED 127.0.0.1:5432',
      searchQuery: '开源',
      searchTeamsData: [],
      hasSearched: true,
      searchError: new Error('connect ECONNREFUSED 127.0.0.1:5432'),
    })
    expect(view.queryByText('connect ECONNREFUSED 127.0.0.1:5432')).not.toBeNull()
    expect(view.queryByText('没有找到团队')).toBeNull()
  })

  it('没有权限时说的是「你没有权限」，不是原话', () => {
    const view = mount({
      ...base,
      searchFailed: true,
      forbidden: true,
      searchQuery: '',
      searchTeamsData: [],
      hasSearched: false,
      searchError: null,
    })
    expect(view.queryByText('你没有权限查看')).not.toBeNull()
  })

  it('清除键把框、结果、搜过没、错误四样一起清掉', async () => {
    const view = mount({
      ...base,
      searchQuery: '开源',
      searchTeamsData: SEARCH_TEAMS,
      hasSearched: true,
      searchError: new Error('boom'),
    })
    await fireEvent.click(view.getByRole('button', { name: '清除' }))

    const emitted = view.emitted()
    expect((emitted['update:searchQuery'] as unknown[][])[0][0]).toBe('')
    expect((emitted['update:searchTeamsData'] as unknown[][])[0][0]).toEqual([])
    expect((emitted['update:hasSearched'] as unknown[][])[0][0]).toBe(false)
    expect((emitted['update:searchError'] as unknown[][])[0][0]).toBeNull()
  })

  it('提交搜索时把当下的词报给页面', async () => {
    const view = mount({
      ...base,
      searchQuery: '开源',
      searchTeamsData: [],
      hasSearched: false,
      searchError: null,
    })
    await fireEvent.keyUp(view.getByRole('textbox'), { key: 'Enter' })
    expect((view.emitted()['fetch-search-results'] as unknown[][])[0][0]).toBe('开源')
  })
})
