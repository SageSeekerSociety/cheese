// 支线页头的状态那一句说到做这件任务的队友时，写项目给它起的名字：项目可以给队友改名，
// 改了之后页头还写「芝士在做」，读的人会以为换人没生效。
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

describe('ThreadHead 状态那一句里的队友', () => {
  beforeEach(() => setLocale('zh-CN'))

  it('任务没单独指定队友：写项目默认那位现在的名字', () => {
    const text = headText(running(null))
    expect(text).toContain('小 C在做')
    expect(text).not.toContain('芝士')
  })

  it('任务交给了另一位队友：写那一位的名字', () => {
    expect(headText(running('cheese-2'))).toContain('阿森在做')
  })
})
