import type { AssistantEdit } from '@/composables/useAssistant'

import { describe, expect, it } from 'vitest'

import { stepError, writesView } from './assistantView'

import { t } from '@/i18n'

describe('stepError', () => {
  it('平台原样的回应只说是哪一类', () => {
    const body = '{"code":404,"message":"Resource space not found"}'
    expect(stepError(`Platform HTTP 404: ${body}`)).toBe(t('tasks.assistant.steps.notFound'))
    expect(stepError('Platform HTTP 403: {}')).toBe(t('tasks.assistant.steps.denied'))
    expect(stepError('Platform HTTP 500: oops')).toBe(t('tasks.assistant.steps.failed'))
    expect(stepError('Traceback (most recent call last)')).toBe(t('tasks.assistant.steps.failed'))
  })

  it('平台拒绝时说的那句原样留着', () => {
    const said = '这份草稿已经发布，不能再改'
    expect(stepError(said)).toBe(said)
  })
})

describe('writesView', () => {
  const terms = { categories: [], topics: [] }
  const edit = (id: number, draft: number, changed: Record<string, unknown>, more: Partial<AssistantEdit> = {}) =>
    ({
      id,
      draft,
      taskId: null,
      created: false,
      version: id,
      name: `草稿${draft}`,
      changed: Object.fromEntries(Object.entries(changed).map(([k, v]) => [k, { before: null, after: v }])),
      kept: [],
      notes: [],
      undone: [],
      ...more,
    }) as AssistantEdit

  it('一份草稿写了几次，并成一张卡，同一格只留最后一次', () => {
    const view = writesView([edit(1, 7, { name: 'A', rank: 1 }), edit(2, 7, { name: 'B' })], terms, 7, () => true)

    expect(view.changes).toHaveLength(1)
    expect(view.changes[0].rows.map((r) => [r.key, r.value, r.change])).toEqual([
      ['difficulty', t('tasks.form.beginner'), 1],
      ['name', 'B', 2],
    ])
  })

  it('一份文件拆成几道题：卡只画开着的那份，写明是第几道', () => {
    const view = writesView(
      [
        edit(1, 7, { name: 'A' }, { notes: [{ field: 'deadline', text: '按第 9 周换算' }] }),
        edit(2, 8, { name: 'B' }, { created: true, name: 'B', notes: [{ field: 'name', text: '编号' }] }),
      ],
      terms,
      7,
      (e) => e.draft === 7,
      (id) => (id === 7 ? 1 : 2)
    )

    expect(view.changes.map((c) => [c.draft, c.ordinal])).toEqual([[7, 1]])
    expect(view.changes[0].notes.map((n) => n.key)).toEqual(['deadline'])
  })

  it('没有表单的页面：每份草稿各一张卡', () => {
    const view = writesView([edit(1, 7, { name: 'A' }), edit(2, 8, { name: 'B' })], terms, null)

    expect(view.changes.map((c) => c.draft)).toEqual([7, 8])
  })
})
