/** 总览的其余两块（#1889 ②③）：正文下面那一栏。
 *
 * 它在这之前只进 AI 队友的提示词，人翻开总览文档只看得到正文那一块。这一份钉的是
 * 界面这一头：两块都画出来、每块标着「平台自动生成」，而且每一条都点得动——去哪
 * 看是它自己的那一头，不是一段死文字。
 */
import type { Component } from 'vue'
import type { OverviewAutoBlock, Topic } from '../../../cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, waitFor } from '@testing-library/vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const getOverviewAuto = vi.fn()

vi.mock('../../../api', async () => {
  const actual = await vi.importActual<typeof import('../../../api')>('../../../api')
  return { ...actual, getOverviewAuto: (...a: unknown[]) => getOverviewAuto(...a) }
})

import OverviewAuto from './OverviewAuto.vue'

import { setLocale } from '@/i18n'

const Auto = OverviewAuto as unknown as Component

const ROOT = {
  id: 'root-1',
  project_id: 'p1',
  parent_id: null,
  title: '项目总览',
  kind: 'root',
  status: 'active',
} as Topic

const BLOCKS: OverviewAutoBlock[] = [
  {
    key: 'active_topics',
    title: '现在在做什么',
    items: [
      { kind: 'topic', topic_id: 't-1', title: '分页接口', owner: '@张衡', status: '在做', conclusion: '用 cursor。' },
    ],
  },
  {
    key: 'closed_topics',
    title: '已结束的话题',
    items: [{ kind: 'topic', topic_id: 't-2', title: '选型', owner: null, status: null, conclusion: '用 Postgres。' }],
  },
]

function mount() {
  return render(Auto, {
    props: { topic: ROOT, activityTick: 0 },
    global: { plugins: [createVuetify({ components, directives })] },
  })
}

beforeEach(() => {
  setLocale('zh-CN')
  getOverviewAuto.mockReset()
  getOverviewAuto.mockResolvedValue({ root_topic_id: 'root-1', blocks: BLOCKS })
})

describe('总览的自动区', () => {
  it('两块都画出来，每块都标着它不由人维护', async () => {
    const { container, findAllByText } = mount()

    await findAllByText('现在在做什么')
    for (const title of ['现在在做什么', '已结束的话题']) {
      expect(container.textContent).toContain(title)
    }
    // 每块一份：读的人在一屏里连着看到两块，说明只写一次就够不着第二块。
    expect(container.querySelectorAll('.auto-block__badge')).toHaveLength(BLOCKS.length)
    expect(container.querySelectorAll('.auto-block__badge')[0].textContent).toContain('平台自动生成，不可编辑')
  })

  it('话题那一行画的是它现在的状态和结论，去处是那个房间', async () => {
    const { container, findAllByText } = mount()
    await findAllByText('分页接口')

    expect(container.textContent).toContain('在做')
    expect(container.textContent).toContain('@张衡')
    expect(container.textContent).toContain('用 cursor。')
    expect(container.textContent).toContain('用 Postgres。')
  })

  it('点一条话题进那个房间', async () => {
    const { findAllByText, emitted } = mount()
    const title = (await findAllByText('分页接口'))[0]

    await fireEvent.click(title.closest('button') as HTMLElement)

    expect(emitted()['open-topic']).toEqual([['t-1']])
  })

  it('两块都没有就整段不画 —— 不补一排「暂无」', async () => {
    getOverviewAuto.mockResolvedValue({ root_topic_id: 'root-1', blocks: [] })
    const { container } = mount()

    await waitFor(() => expect(getOverviewAuto).toHaveBeenCalled())
    expect(container.querySelector('.overview-auto')).toBeNull()
  })

  it('拿不到就照实说，重试能把它再要一遍', async () => {
    getOverviewAuto.mockRejectedValueOnce(new Error('boom'))
    const { container, findByText } = mount()

    await findByText('无法加载这部分内容')
    getOverviewAuto.mockResolvedValue({ root_topic_id: 'root-1', blocks: BLOCKS })
    await fireEvent.click(container.querySelector('.overview-auto__retry') as HTMLElement)

    await findByText('现在在做什么')
  })
})
