/**
 * 「我的团队」那一屏拆出来的画法（`MineView.vue`）：props 进、事件出。
 *
 * 四种状态一眼分开，是这一屏的三件事：读回来之前不说「还没有加入团队」；读失败说的是
 * 「加载失败」加一个重试，而且那个重试是**交给页面**去重取，不是这一半自己发请求；
 * 有团队时每一行都链到那个团队。
 */
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import MineView from './MineView.vue'

import i18n, { setLocale } from '@/i18n'
import { MY_TEAMS } from '@/views/demo/catalogScenesMiscFixtures'

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
  return render(MineView, {
    props,
    global: { plugins: [createVuetify({ components, directives }), i18n, router()] },
  })
}

beforeEach(() => setLocale('zh-CN'))
afterEach(cleanup)

describe('我的团队', () => {
  it('还在读的时候画转圈，不说「还没有加入团队」', () => {
    const view = mount({ isLoadingMyTeams: true, loadMyTeamsError: false, myTeams: [] })
    expect(view.container.querySelector('.v-progress-circular')).not.toBeNull()
    expect(view.queryByText('还没有加入团队')).toBeNull()
  })

  it('读失败画「加载失败」，点重试就是请页面重取一次', async () => {
    const view = mount({ isLoadingMyTeams: false, loadMyTeamsError: true, myTeams: [] })
    expect(view.queryByText('加载失败')).not.toBeNull()
    expect(view.queryByText('还没有加入团队')).toBeNull()

    await fireEvent.click(view.getByRole('button', { name: /重新加载/ }))
    expect(view.emitted()['fetch-my-teams']).toHaveLength(1)
  })

  it('读回来了、一个团队都没有，才说「还没有加入团队」', () => {
    const view = mount({ isLoadingMyTeams: false, loadMyTeamsError: false, myTeams: [] })
    expect(view.queryByText('还没有加入团队')).not.toBeNull()
    expect(view.queryByText('加载失败')).toBeNull()
  })

  it('有团队就一列画出来，每一行都链到那个团队', () => {
    const view = mount({ isLoadingMyTeams: false, loadMyTeamsError: false, myTeams: MY_TEAMS })
    expect(view.queryByText('城市数据实验室')).not.toBeNull()
    expect(view.queryByText('数学建模社')).not.toBeNull()
    expect(view.queryByText('还没有加入团队')).toBeNull()

    const hrefs = [...view.container.querySelectorAll('a[href]')].map((a) => a.getAttribute('href') ?? '')
    expect(hrefs.some((href) => href.includes('city-lab'))).toBe(true)
    expect(hrefs.some((href) => href.includes('math-club'))).toBe(true)
  })
})
