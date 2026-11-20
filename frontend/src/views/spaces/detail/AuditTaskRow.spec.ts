/** 待审核列表里的一行：截止那一格说清是哪一个截止。申请制的题，题目的截止是申请截止。 */
import type { Task } from '@/types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, render } from '@testing-library/vue'
import dayjs from 'dayjs'
import { afterEach, beforeAll, describe, it } from 'vitest'

import AuditTaskRow from './AuditTaskRow.vue'

import i18n, { setLocale } from '@/i18n'

const t = i18n.global.t
const AT = Date.UTC(2026, 10, 2, 4)

function row(startMode: 'DIRECT' | 'APPLY') {
  const task = {
    id: 5,
    name: '小组项目：简易数据库索引',
    intro: '',
    topics: [],
    description: '',
    deadline: AT,
    submitterType: 'USER',
    startMode,
    creator: { id: 1, username: 'alice', nickname: 'Alice' },
    createdAt: AT,
  } as unknown as Task
  return render(AuditTaskRow, {
    props: { task, open: false },
    global: { plugins: [createVuetify({ components, directives }), i18n] },
  })
}

describe('待审核的一行', () => {
  beforeAll(() => setLocale('zh-CN'))
  afterEach(cleanup)

  it.each([
    ['APPLY', 'spaces.detail.auditTasks.applyDeadline'],
    ['DIRECT', 'spaces.detail.auditTasks.deadline'],
  ] as const)('%s 的题：截止那一格说清是哪一个截止', (startMode, key) => {
    const view = row(startMode)

    view.getByText(t(key, { date: dayjs(AT).format('MM-DD HH:mm') }))
  })
})
