// 侧栏一行右边那几个小头像：同一位队友只画一次。
//
// 一位队友在记录里有两个 handle——名册上的座位（`cheese-<hex>`）和它自己的（`cheese`，
// 会话、轮次帧、消息的收件人用它）。「在干活」和「房间在等它」两处各写一个，是同一个
// 人的两条记录，不是两个人：按 handle 比字符串去重，它就会被画成两个头像，一绿一红。
import type { ProjectMemberRow, Topic } from '@/cx_types'

import { defineComponent, h } from 'vue'
import { render } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { beforeEach, describe, expect, it } from 'vitest'

import { useTopicRail } from './useTopicRail'

import { setLocale } from '@/i18n'
import { useWorkspaceStore } from '@/stores/workspace'

const CHEESE_SEAT = 'cheese-0000000000a1'
const KIMI_SEAT = 'cheese-0000000000b2'

// 两位有名字的队友：默认那位在记录里用 `cheese`（它自己的 handle），另一位用
// `cheese-kimi`。名字都改过，所以屏幕上的名字就是名册上的名字，不是「芝士」兜底。
const TEAM: ProjectMemberRow[] = [
  {
    user_handle: CHEESE_SEAT,
    instance_handle: 'cheese',
    name: '小知',
    name_source: 'human',
    agent: true,
    project_default: true,
    source: 'agent',
  },
  {
    user_handle: KIMI_SEAT,
    instance_handle: 'cheese-kimi',
    name: '芝士K',
    name_source: 'human',
    agent: true,
    source: 'agent',
  },
]

function topic(id: string, extra: Partial<Topic>): Topic {
  return {
    id,
    project_id: 'p1',
    parent_id: null,
    title: id,
    kind: 'topic',
    status: 'active',
    created_at: '2026-10-01T00:00:00Z',
    ...extra,
  } as Topic
}

/** 挂一条侧栏，把每一行画出来的头像（状态 + 名字）摆进 DOM。 */
function mount(topics: Topic[]) {
  const Host = defineComponent({
    setup() {
      useWorkspaceStore().members = TEAM
      const rail = useTopicRail({ topics, selectedProjectId: 'p1', selectedTopicId: null })
      return () =>
        h(
          'div',
          topics.flatMap((room) =>
            rail
              .memberMarks(room)
              .map((mark) => h('span', { 'data-room': room.id, 'data-state': mark.state, 'data-name': mark.name }))
          )
        )
    },
  })
  return render(Host, { global: { plugins: [createPinia()] } })
}

const marksOf = (container: HTMLElement, room: string) =>
  [...container.querySelectorAll(`[data-room="${room}"]`)].map((el) => ({
    state: el.getAttribute('data-state'),
    name: el.getAttribute('data-name'),
  }))

describe('侧栏一行上的成员头像', () => {
  beforeEach(() => setLocale('zh-CN'))

  it('同一位在干活、又被等着，只画一个——而且是红色那个', () => {
    const { container } = mount([
      topic('t-1', {
        activity: [{ member: 'cheese', kind: 'working', since: 1 }],
        waits: [{ member: CHEESE_SEAT, reason: 'failed', since: '2026-10-01T00:00:00Z' }],
      }),
    ])

    expect(marksOf(container, 't-1')).toEqual([{ state: 'stalled', name: '小知' }])
  })

  it('两位队友各画一个，在等的那位在前', () => {
    const { container } = mount([
      topic('t-1', {
        activity: [{ member: 'cheese-kimi', kind: 'working', since: 1 }],
        waits: [{ member: CHEESE_SEAT, reason: 'failed', since: '2026-10-01T00:00:00Z' }],
      }),
    ])

    expect(marksOf(container, 't-1')).toEqual([
      { state: 'stalled', name: '小知' },
      { state: 'working', name: '芝士K' },
    ])
  })
})
