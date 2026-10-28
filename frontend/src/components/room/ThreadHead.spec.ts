// 支线页头的状态那一格只写状态本身（「在做」），不把做这件任务的队友拼进标签：
// 拼进去时写的是默认的「芝士」，项目改过名的读者会以为换人没生效。
import type { ProjectMemberRow, RoomTask } from '@/cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render } from '@testing-library/vue'
import { beforeEach, describe, expect, it } from 'vitest'

import ThreadHead from './ThreadHead.vue'

import i18n, { setLocale } from '@/i18n'

const vuetify = createVuetify({ components, directives })

const MEMBERS: ProjectMemberRow[] = [
  { user_handle: 'ana', name: 'Ana' },
  { user_handle: 'cheese-1', name: '小 C', name_source: 'human', agent: true, project_default: true },
  { user_handle: 'cheese-2', name: '阿森', name_source: 'human', agent: true, project_default: false },
]

function running(agentHandle: string | null): RoomTask {
  return {
    id: 't1',
    title: '整理周报',
    status: 'open',
    owner_handle: 'ana',
    waiting_on: null,
    agent_handle: agentHandle,
    presentation: { column: 'building', phrase: 'running' },
  } as unknown as RoomTask
}

function headText(task: RoomTask): string {
  const { getByTestId } = render(ThreadHead, {
    props: {
      heading: '整理周报',
      task,
      viewer: 'ana',
      members: MEMBERS,
      memberNames: { ana: 'Ana', 'cheese-1': '小 C', 'cheese-2': '阿森' },
      canToTask: false,
    },
    global: { plugins: [vuetify, i18n] },
  })
  return getByTestId('thread-task-phrase').textContent ?? ''
}

describe('ThreadHead 的状态那一格', () => {
  beforeEach(() => setLocale('zh-CN'))

  // 状态只写状态本身：在做就是「在做」，不再把哪位队友拼进去（改过名的项目里拼出来的
  // 一直是默认的「芝士」）。
  it('写的是状态本身，不带默认的队友名字', () => {
    const text = headText(running(null))
    expect(text.trim()).toBe('在做')
  })
})
