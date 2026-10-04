/** 悬停条上那颗「复制链接」。
 *
 * 一条消息要能在站内被指出来（贴给别人、自己留个书签），地址沿用搜索那一页的
 * 形状：`?block=<id>`，活卡里的对话再带上 `tab`/`card`。地址由 `useMessageLink`
 * 拼（组件不能 import vue-router），所以这里装真的路由来喂它。
 *
 * 还有一条同样重要：宿主**没有**路由时（演示页、一整类单测），不给链接就不画这
 * 一颗——画一颗点了没反应的按钮，比少画一颗更糟。
 */
import type { Block } from '../../cx_types'

import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import RoomHoverBar from './RoomHoverBar.vue'

import { setLocale } from '@/i18n'

const writeText = vi.fn().mockResolvedValue(undefined)

function block(id: string, taskId: string | null = null): Block {
  return {
    id,
    topic_id: 'tp1',
    kind: 'message',
    author_type: 'participant',
    author: '张衡',
    content: '这一条',
    created_at: '2026-09-20T00:00:00Z',
    task_id: taskId,
  }
}

async function routerStoppedOnTopic() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ name: 'workspace-topic', path: '/p/:projectId/t/:topicId', component: { template: '<div />' } }],
  })
  await router.push('/p/pr1/t/tp1')
  await router.isReady()
  return router
}

function renderBar(b: Block, router?: ReturnType<typeof createRouter>) {
  const vuetify = createVuetify({ components, directives })
  const plugins = router ? [vuetify, router] : [vuetify]
  return render(RoomHoverBar, {
    props: {
      block: b,
      shown: true,
      top: 0,
      jump: false,
      isAgent: false,
      pickerOpen: false,
      editable: false,
    },
    global: { plugins },
  })
}

function linkButton(container: Element): HTMLButtonElement | null {
  return container.querySelector<HTMLButtonElement>('button[title="复制链接"], button[title="已复制链接"]')
}

async function flush() {
  for (let i = 0; i < 4; i += 1) await new Promise((r) => setTimeout(r, 0))
}

beforeEach(() => {
  // These assertions read the Chinese copy.
  setLocale('zh-CN')
  writeText.mockClear()
  Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
})
afterEach(() => vi.restoreAllMocks())

describe('消息的悬停条：复制链接', () => {
  it('有路由时给得出这一条的链接，点了写进剪贴板', async () => {
    const router = await routerStoppedOnTopic()
    const { container } = renderBar(block('b1'), router)

    const button = linkButton(container)
    expect(button, '有路由，却画不出复制链接那一颗').toBeTruthy()

    await fireEvent.click(button!)
    await flush()

    expect(writeText).toHaveBeenCalledTimes(1)
    const url = new URL(writeText.mock.calls[0][0] as string)
    expect(url.origin).toBe(window.location.origin)
    expect(url.pathname).toBe('/p/pr1/t/tp1')
    expect(url.searchParams.get('block')).toBe('b1')
  })

  it('活卡里的对话多带 tab 和 card', async () => {
    const router = await routerStoppedOnTopic()
    const { container } = renderBar(block('b2', 'task9'), router)

    await fireEvent.click(linkButton(container)!)
    await flush()

    const url = new URL(writeText.mock.calls[0][0] as string)
    expect(url.searchParams.get('block')).toBe('b2')
    expect(url.searchParams.get('tab')).toBe('overview')
    expect(url.searchParams.get('card')).toBe('task9')
  })

  it('复制完原地说一声「已复制链接」', async () => {
    const router = await routerStoppedOnTopic()
    const { container } = renderBar(block('b1'), router)

    await fireEvent.click(linkButton(container)!)
    await flush()

    expect(container.querySelector('button[title="已复制链接"]')).toBeTruthy()
  })

  it('宿主没有路由就不画这一颗，而不是画一颗点了没反应的', async () => {
    const { container } = renderBar(block('b1'))
    expect(linkButton(container)).toBeNull()
    // 其余的按钮照旧在（复制的正文那颗还在）。
    expect(container.querySelector('button[title="复制"]')).toBeTruthy()
  })
})
