// 一张任务卡的负责人要画成一张脸 + 他的名字（`owner`），不是一行字符串；队友画超椭圆、
// 人画圆形头像或彩色首字母；没有负责人时那一行不画，也不凭空画一张脸。
import type { TaskLine } from '../../lib/channelTasks'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render } from '@testing-library/vue'
import { beforeAll, describe, expect, it } from 'vitest'

import TaskCard from './TaskCard.vue'

import i18n, { setLocale } from '@/i18n'

beforeAll(() => {
  setLocale('zh-CN')
})

const task: TaskLine = {
  id: 'task-1',
  title: '整理第一周的课件',
  owner: 'cheese',
  creator: 'bob',
  status: '进行中',
  tone: 'running',
  accepted: null,
  at: new Date(Date.now() - 5 * 60_000).toISOString(),
}

const plugins = [createVuetify({ components, directives }), i18n]

describe('TaskCard · 谁负责', () => {
  it('owner 给了就画共用身份：脸 + 名，名字落在「负责」那句话里', () => {
    const { container } = render(TaskCard, {
      props: { task, owner: { handle: 'cheese', name: '芝士', agent: true } },
      global: { plugins },
    })

    const own = container.querySelector('.task-card__owner') as HTMLElement
    expect(own.querySelector('.identity-view'), '负责人那一行没画身份').not.toBeNull()
    expect(own.querySelector('.identity-view__name')?.textContent).toContain('芝士')
    expect(own.textContent).toContain('负责')
    // 队友画超椭圆，不画真人的圆头像。
    expect(own.querySelector('.cheese-avatar')).not.toBeNull()
  })

  it('人挑过头像就画那张脸', () => {
    const { container } = render(TaskCard, {
      props: { task, owner: { handle: 'wang', name: '王长鑫', avatar: '/avatars/5', agent: false } },
      global: { plugins },
    })

    const own = container.querySelector('.task-card__owner') as HTMLElement
    expect(own.querySelector('.identity-view__name')?.textContent).toContain('王长鑫')
    expect(own.querySelector('img')?.getAttribute('src')).toContain('/avatars/5')
  })

  it('没有负责人时不画那一行，也不凭空画一张脸', () => {
    const { container } = render(TaskCard, {
      props: { task: { ...task, owner: null }, owner: null },
      global: { plugins },
    })

    expect(container.querySelector('.task-card__owner')).toBeNull()
    expect(container.querySelector('.identity-view')).toBeNull()
  })
})
