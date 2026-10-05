// 探索小队这份名录只有两根线：搜出来一堆、和搜了但没搜到。第二种曾经把第一种的
// 失败也一起吞了：一次没读到的搜索留下的也是空数组，「没有找到团队」就跟着出来了
// —— 那是在替服务端说「确实一个都没有」，而它其实什么都没答上来。
//
// 这一份钉三件事：搜到了就列出来；搜到了但为空才说「没有找到团队」；没读到就说没
// 读到，并且留在原地给一条重试的路。
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor, within } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const search = vi.hoisted(() => vi.fn())

vi.mock('@/network/api/teams', () => ({ TeamsApi: { search } }))

import Explore from './Explore.vue'

import { setLocale } from '@/i18n'

const NO_RESULTS = '没有找到团队'
const SEARCH_FAILED = '搜索失败，请稍后重试'

async function mount() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'root', component: { template: '<div />' } },
      { path: '/teams/:handle', name: 'TeamsDetailDefault', component: { template: '<div />' } },
    ],
  })
  await router.push({ name: 'root' })
  await router.isReady()
  return render(Explore, { global: { plugins: [createVuetify({ components, directives }), router] } })
}

function team(id: number, name: string) {
  return { id, name, intro: '', handle: `t${id}`, avatarId: null }
}

async function searchFor(view: Awaited<ReturnType<typeof mount>>, query: string) {
  const input = view.container.querySelector('input') as HTMLInputElement
  await fireEvent.update(input, query)
  await fireEvent.keyUp(input, { key: 'Enter' })
}

beforeAll(() => {
  if (!globalThis.matchMedia) {
    globalThis.matchMedia = (() => ({
      matches: true,
      addEventListener() {},
      removeEventListener() {},
      addListener() {},
      removeListener() {},
      dispatchEvent: () => false,
    })) as unknown as typeof globalThis.matchMedia
  }
})

beforeEach(() => {
  setLocale('zh-CN')
  search.mockReset()
})

afterEach(cleanup)

describe('搜索小队', () => {
  it('搜到了就列出来，不说「没有找到团队」', async () => {
    search.mockResolvedValue({ data: { teams: [team(1, '芝士核心组')] } })
    const view = await mount()
    await searchFor(view, '芝士')

    await waitFor(() => expect(view.getByText('芝士核心组')).toBeTruthy())
    expect(view.queryByText(NO_RESULTS)).toBeNull()
  })

  it('搜到了但一个都没有，才说「没有找到团队」', async () => {
    search.mockResolvedValue({ data: { teams: [] } })
    const view = await mount()
    await searchFor(view, '不存在的组')

    await waitFor(() => expect(view.getByText(NO_RESULTS)).toBeTruthy())
    expect(view.container.querySelector('.base-load-error')).toBeNull()
  })

  it('没读到就说没读到，不说「没有找到团队」，重试真的再问一次', async () => {
    search.mockRejectedValue(new Error('boom'))
    const view = await mount()
    await searchFor(view, '芝士')

    await waitFor(() => expect(view.container.querySelector('.base-load-error')).toBeTruthy())
    const block = view.container.querySelector('.base-load-error') as HTMLElement
    expect(view.queryByText(NO_RESULTS)).toBeNull()
    expect(block.textContent).toContain(SEARCH_FAILED)

    search.mockResolvedValue({ data: { teams: [team(1, '芝士核心组')] } })
    await fireEvent.click(within(block).getByRole('button'))
    await waitFor(() => expect(search).toHaveBeenCalledTimes(2))
    await waitFor(() => expect(view.getByText('芝士核心组')).toBeTruthy())
    expect(view.container.querySelector('.base-load-error')).toBeNull()
  })

  it('403 说的是「没权限」，并且不给一颗按不动的重试', async () => {
    search.mockRejectedValue(Object.assign(new Error('nope'), { status: 403 }))
    const view = await mount()
    await searchFor(view, '芝士')

    await waitFor(() => expect(view.container.querySelector('.base-load-error')).toBeTruthy())
    const block = view.container.querySelector('.base-load-error') as HTMLElement
    expect(block.textContent).toContain('你没有权限查看')
    expect(within(block).queryByRole('button')).toBeNull()
  })
})
