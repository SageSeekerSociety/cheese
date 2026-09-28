/** ②~⑤ 只长在总览房间的文档下面。
 *
 * 五块总览是项目级的：只有根话题的文档是「整个项目」那一份，别的房间的文档写的
 * 是它自己。在那些房间里再挂一份项目全局，读的人会以为这四块说的是这个房间。
 */
import type { Component } from 'vue'
import type { Block, Topic } from '../../cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render, waitFor } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const getDoc = vi.fn()
const getComments = vi.fn()
const getDocNodes = vi.fn()
const getOverviewAuto = vi.fn()

vi.mock('../../api', async () => {
  const actual = await vi.importActual<typeof import('../../api')>('../../api')
  return {
    ...actual,
    getDoc: (...a: unknown[]) => getDoc(...a),
    getComments: (...a: unknown[]) => getComments(...a),
    getDocNodes: (...a: unknown[]) => getDocNodes(...a),
    getOverviewAuto: (...a: unknown[]) => getOverviewAuto(...a),
  }
})

import PanelDoc from './PanelDoc.vue'

import { setLocale } from '@/i18n'

const Doc = PanelDoc as unknown as Component

const ROOT = {
  id: 'root-1',
  project_id: 'p1',
  parent_id: null,
  title: '项目总览',
  kind: 'root',
  status: 'active',
  created_by: 'u',
  created_at: '2026-08-01T00:00:00Z',
  updated_at: '2026-08-01T00:00:00Z',
} as Topic

const ROOM = { ...ROOT, id: 't1', title: '干活的房间', kind: 'topic' } as Topic

const BLOCKS = [
  {
    key: 'active_topics',
    title: '现在在做什么',
    items: [{ kind: 'topic', topic_id: 't2', title: '分页接口', owner: null, status: '还没开活', conclusion: null }],
  },
]

function doc(content: string): Block {
  return { id: 'd1', kind: 'doc', content, doc_version: 3 } as unknown as Block
}

let vuetify: ReturnType<typeof createVuetify>

beforeAll(() => {
  vuetify = createVuetify({ components, directives })
})

beforeEach(() => {
  setLocale('zh-CN')
  getDoc.mockReset()
  getOverviewAuto.mockReset()
  getDoc.mockResolvedValue(doc('## 项目是什么\n\n给高中生做算法课。'))
  getComments.mockResolvedValue({ data: [], total: 0 })
  getDocNodes.mockResolvedValue({ data: [], total: 0 })
  getOverviewAuto.mockResolvedValue({ root_topic_id: 'root-1', blocks: BLOCKS })
})

function open(topic: Topic) {
  return render(Doc, {
    props: { topic, activityTick: 0, topicList: [] },
    global: { plugins: [vuetify] },
  })
}

describe('总览房间的文档下面', () => {
  it('根话题把正文之外的四块摆在正文下面', async () => {
    const { container, findByText } = open(ROOT)

    await findByText('现在在做什么')
    expect(container.textContent).toContain('给高中生做算法课。')
    expect(container.textContent).toContain('平台自动生成，不可编辑')
    // 在正文下面、评论区上面：读文档的人顺着读下去，不必先翻过一屏评论。
    const page = container.querySelector('.doc-page') as HTMLElement
    const order = ['.doc-editor-wrap', '.overview-auto', '.doc-comments']
    const positions = order.map((sel) => page.querySelector(sel))
    expect(
      positions.every((el) => el !== null),
      `文档页里少了：${order}`
    ).toBe(true)
  })

  it('别的房间不挂这一栏 —— 它们的文档说的是房间自己', async () => {
    const { container, findByText } = open(ROOM)

    await findByText('给高中生做算法课。')
    expect(container.querySelector('.overview-auto')).toBeNull()
    await waitFor(() => expect(getOverviewAuto).not.toHaveBeenCalled())
  })
})
