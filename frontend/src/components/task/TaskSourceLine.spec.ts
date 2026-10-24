/**
 * 任务页顶上说这个任务从哪句话来：引那句原话（@ 过谁就写谁的名字，不露出 token），点它
 * 就去那段对话；不是从哪句话转来的任务照实说是谁建的，不拿别的话顶替。
 */
import type { RoomTask } from '@/cx_types'
import type { TaskSource } from '@/types/taskOrigin'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import { describe, expect, it } from 'vitest'

import TaskSourceLine from './TaskSourceLine.vue'

import { setLocale, t } from '@/i18n'

setLocale('zh-CN')
const vuetify = createVuetify({ components, directives })

const task = { id: 't1', created_by: 'andy', created_at: '2026-10-11T21:59:04Z' } as RoomTask

function said(content: string, where: TaskSource['where'] = 'thread'): TaskSource {
  return {
    block_id: 'b1',
    author: 'andy',
    content,
    created_at: '2026-10-11T21:54:17Z',
    conversation_id: 'th1',
    room_id: 'r1',
    where,
    title: 'bug-fixing',
  }
}

function show(source: TaskSource | null, memberNames: Record<string, string> = {}, of: RoomTask = task) {
  return render(TaskSourceLine, {
    props: { source, task: of, memberNames },
    global: { plugins: [vuetify] },
  })
}

describe('TaskSourceLine', () => {
  it('quotes the message the task came from and the channel it was said in', () => {
    const view = show(said('经常在任务里停在中间'), { andy: 'Andy' })

    const line = view.getByTestId('task-source').textContent ?? ''
    expect(line).toContain('Andy：经常在任务里停在中间')
    expect(line).toContain('#bug-fixing')
  })

  it('reads a mention in the message as the name it mentions', () => {
    const view = show(said('<@cheese-d22f1886fd86> 修一下拖拽排序'), { andy: 'Andy', 'cheese-d22f1886fd86': 'Nova' })

    const line = view.getByTestId('task-source').textContent ?? ''
    expect(line).toContain('Andy：@Nova 修一下拖拽排序')
    expect(line).not.toContain('<@')
  })

  it('asks to open the message where it was said when clicked', async () => {
    const source = said('回复变长时不贴底')
    const view = show(source)

    await fireEvent.click(view.getByTestId('task-source'))

    expect(view.emitted('open')).toEqual([[source]])
  })

  it('says who created a task that came from no message, and quotes nothing', () => {
    const view = show(null, { andy: 'Andy' })

    const line = view.getByTestId('task-source').textContent ?? ''
    expect(line).toContain('Andy 新建的任务')
    expect(line).not.toContain('：')
    expect(view.queryByRole('button')).toBeNull()
  })

  it('says his own 芝士 created it for him when it did', () => {
    const view = show(null, { andy: 'Andy' }, { ...task, created_by_assistant: true })

    expect(view.getByTestId('task-source').textContent).toContain(t('work.task.source.byAssistant', { name: 'Andy' }))
  })

  it('says a teammate created it when the platform did not record what asked for it', () => {
    const view = show(null, { 'cheese-c82aeb40555a': '芝士' }, { ...task, created_by: 'cheese-c82aeb40555a' })

    expect(view.getByTestId('task-source').textContent).toContain('芝士 新建的任务，没有记下是从哪句话来的')
  })
})
