import type { AssistantEdit } from '@/stores/agentThread'

import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import {
  checklistOf,
  foldedLine,
  interleave,
  type PanelStep,
  readsLine,
  stepError,
  stepRows,
  stepView,
  withSubagents,
} from './assistantView'
import { writesView } from './draftChangeView'

import { setLocale, t } from '@/i18n'

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

describe('一轮做过的几步怎么读', () => {
  beforeEach(() => setLocale('zh-CN'))
  afterEach(() => setLocale('en'))

  type Done = { name: string; args?: object; effect?: 'reads' | 'changes' | 'reaches others'; shown?: string }
  const known = (d: Done) => ({ ...(d.effect ? { effect: d.effect } : {}), ...(d.shown ? { shown: d.shown } : {}) })
  const steps = (...done: (Done | 'run' | ['think', number] | ['failed', Done, string])[]): PanelStep[] =>
    done.map((d, i) => {
      if (d === 'run')
        return stepView({
          call: `c${i}`,
          name: 'cheese_read',
          args: '{}',
          state: 'run',
          effect: 'reads',
          shown: '空间「数据结构」',
        })
      if (Array.isArray(d) && d[0] === 'think')
        return stepView({ call: `c${i}`, name: 'thinking', args: '', state: 'done', ms: d[1] * 1000 })
      if (Array.isArray(d)) {
        const [, failed, error] = d
        return stepView({
          call: `c${i}`,
          name: failed.name,
          args: JSON.stringify(failed.args ?? {}),
          state: 'failed',
          error,
          ...known(failed),
        })
      }
      return stepView({ call: `c${i}`, name: d.name, args: JSON.stringify(d.args ?? {}), state: 'done', ...known(d) })
    })
  const read = (shown: string): Done => ({ name: 'cheese_read', args: { ref: 'x' }, effect: 'reads', shown })

  it('只查阅时，收起的一句写查阅了几项，展开后并成一组，每项写读了什么', () => {
    const turn = steps(
      ['think', 1],
      read('你加入的空间'),
      read('空间「操作系统」'),
      { name: 'cheese_find', args: { where: 'spaces/3' }, effect: 'reads', shown: '空间「操作系统」的题目' },
      { name: 'cheese_help', args: { query: '删除题目' }, effect: 'reads', shown: '帮助文档：删除题目' }
    )
    expect(foldedLine(turn)).toBe('查阅 4 项资料')
    const [group, ...rest] = stepRows(turn)
    expect(rest).toEqual([])
    expect(group.kind === 'reads' && group.steps.map((s) => `${s.verb} ${s.arg}`)).toEqual([
      '读取 你加入的空间',
      '读取 空间「操作系统」',
      '查找 空间「操作系统」的题目',
      '搜索 帮助文档：删除题目',
    ])
    expect(group.kind === 'reads' && readsLine(group.steps)).toBe('查阅 4 项资料')
  })

  it('做了事时先写做了什么，再写查阅了几项；做的事单列一行', () => {
    const turn = steps(read('项目「机器学习」'), {
      name: 'chat_send',
      args: { where: 'ml/channels/2', content: '改到三点' },
      effect: 'reaches others',
      shown: '频道「实验」',
    })
    expect(foldedLine(turn)).toBe('发送 1 条消息，查阅 1 项资料')
    const rows = stepRows(turn)
    expect(rows.map((r) => r.kind)).toEqual(['reads', 'step'])
    const sent = rows[1].kind === 'step' ? rows[1].step : null
    expect(sent && `${sent.verb} ${sent.arg}`).toBe('发送消息 频道「实验」')
  })

  it('还在读时，那一组写正在读的那一项', () => {
    const [group] = stepRows(steps(read('你加入的空间'), 'run'))
    expect(group.kind === 'reads' && readsLine(group.steps)).toBe('正在读取空间「数据结构」…')
  })

  it('没读成的一项单列，写出原因', () => {
    const rows = stepRows(steps(read('你加入的空间'), ['failed', read('题目「TEST」'), 'Platform HTTP 403: {}']))
    const failed = rows[1].kind === 'step' ? rows[1].step : null
    expect(failed?.error).toBe('没有权限')
  })

  it('想得短的一段不列，想了十秒以上的列出来', () => {
    expect(stepRows(steps(['think', 3], read('你的待办'))).map((r) => r.kind)).toEqual(['reads'])
    expect(stepRows(steps(['think', 12], read('你的待办'))).map((r) => r.kind)).toEqual(['step', 'reads'])
  })

  it('从前的回答没记每步做的是哪类事：收起时只说完成了几步', () => {
    expect(
      foldedLine(steps({ name: 'cheese_read', args: { ref: 'x' } }, { name: 'cheese_find', args: { query: 'a' } }))
    ).toBe('完成 2 步')
  })
})

describe('stepView', () => {
  it('在电脑上做的一步，写出跑的命令或读写的文件', () => {
    const step = (name: string, args: object) =>
      stepView({ call: name, name, args: JSON.stringify(args), state: 'done' })
    expect(step('bash', { command: 'python3 stats.py\n  --all' })).toMatchObject({
      verb: t('toolLabels.piBash'),
      arg: 'python3 stats.py --all',
    })
    expect(step('write', { path: 'report.md', content: '……' }).arg).toBe('report.md')
  })

  it('指代东西的编号不写出来，资料库里的文件写文件名', () => {
    const step = (name: string, args: object) =>
      stepView({ call: name, name, args: JSON.stringify(args), state: 'done' })
    expect(step('cheese_read', { ref: 'ml-course/channels/3' }).arg).toBe('')
    expect(step('cheese_inbox_respond', { ref: 'alerts/9029', choice: '采纳' }).arg).toBe('')
    expect(step('chat_send', { where: 'ml-course/tasks/12', content: '好' }).arg).toBe('')
    expect(step('cheese_read', { ref: 'library/报告/实验 3.pdf' }).arg).toBe('实验 3.pdf')
    expect(step('cheese_file_to_computer', { ref: 'library/squares.csv' }).arg).toBe('squares.csv')
    expect(step('cheese_find', { query: '缓存实验' }).arg).toBe('缓存实验')
  })

  it('读回来的东西有名字时写名字，分身读的也一样', () => {
    const read = { call: 'c1', name: 'cheese_read', args: '{"ref": "ml/tasks/12"}', state: 'done' as const }
    expect(stepView({ ...read, shown: '任务「报名说明」' }).arg).toBe('任务「报名说明」')
    const [sent] = withSubagents(
      [stepView({ call: 's1', name: 'cheese_subagent_start', args: '{}', state: 'done' })],
      [
        {
          id: 'branch-1',
          call: 's1',
          description: '读频道',
          status: 'done',
          steps: [{ name: 'cheese_read', args: '{"ref": "ml/channels/3"}', shown: '频道「综合」' }],
        },
      ]
    )
    expect(sent.subagent?.steps.map((s) => s.arg)).toEqual(['频道「综合」'])
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
      kind: 'draft',
      kept: [],
      notes: [],
      stageEffect: [],
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

  it.each([
    ['DIRECT', '开始做这道题后锁定', '截止日期'],
    ['APPLY', '申请通过后锁定', '截止日期'],
  ] as const)('%s 的题：卡上的队伍锁定按开始方式说，不说报名', (startMode, locked, deadline) => {
    setLocale('zh-CN')
    const view = writesView(
      [edit(1, 7, { teamLockingPolicy: 'LOCK_ON_APPROVAL', deadline: 0 }, { startMode })],
      terms,
      7
    )

    const rows = Object.fromEntries(view.changes[0].rows.map((r) => [r.key, r]))
    expect(rows.team_locking.value).toBe(locked)
    expect(rows.deadline.label).toBe(deadline)
    expect(JSON.stringify(view.changes[0].rows)).not.toContain('报名')
  })

  it('直接开始的题：开始日期那一格叫开放日期', () => {
    setLocale('zh-CN')
    const view = writesView([edit(1, 7, { registrationStartAt: 0 }, { startMode: 'DIRECT' })], terms, 7)

    expect(view.changes[0].rows[0].label).toBe('开放日期')
  })

  it('存下阶段的影响只看草稿现在的样子：后一次写入说没人受影响，前一次的提醒就不在了', () => {
    setLocale('zh-CN')
    const failing = { key: 'stageEffectFailing', params: { n: 1 } }
    const moved = edit(1, 7, { stages: [] }, { stageEffect: [failing] })
    const restored = edit(2, 7, { stages: [] }, { stageEffect: [] })

    expect(writesView([moved], terms, 7).changes[0].notes.map((n) => n.text)).toEqual([
      '1 位参与者有已过截止、还没提交的阶段，保存后将判为未通过',
    ])
    expect(writesView([moved, restored], terms, 7).changes[0].notes).toEqual([])
  })

  it('存下阶段的影响按读的人的语言写', () => {
    setLocale('en')
    try {
      const said = edit(1, 7, { stages: [] }, { stageEffect: [{ key: 'stageEffectFailing', params: { n: 1 } }] })
      expect(writesView([said], terms, 7).changes[0].notes.map((n) => n.text)).toEqual([
        'Participants with a stage past its due date and nothing handed in: 1. Saving marks them as failed',
      ])
    } finally {
      setLocale('zh-CN')
    }
  })

  it('一份文件拆成几道题：开着的那份展开，别的各收成一行，写明是第几道', () => {
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

    expect(view.changes.map((c) => [c.draft, c.ordinal, !!c.collapsed])).toEqual([
      [7, 1, false],
      [8, 2, true],
    ])
    expect(view.changes[0].notes.map((n) => n.key)).toEqual(['deadline'])
    expect(view.changes[1].notes).toEqual([])
    expect(view.changes[1].canUndo).toBe(false)
  })

  it('改详情里的一段：卡上写改了的那一行，不写开头', () => {
    const before = '## 背景\n\n实验室的服务跑在 K8s 上。\n\n## 要求\n\n交源码。'
    const after = '## 背景\n\n实验室的服务跑在 K8s 上。\n\n## 要求\n\n交源码和 4–6 页报告。'
    const one = {
      ...edit(1, 7, {}),
      changed: { descriptionMarkdown: { before, after }, description: { before, after } },
    } as AssistantEdit

    expect(writesView([one], terms, 7).changes[0].rows[0].value).toBe('交源码和 4–6 页报告。')
  })

  it('期限带单位，导师写名字，访问范围写域名组', () => {
    const named = {
      categories: [],
      topics: [],
      mentors: [{ handle: 'wangli', name: '王立' }],
      domainGroups: [{ id: 3, name: '本校邮箱' }],
    }
    const view = writesView(
      [
        edit(1, 7, {
          defaultDeadline: 30,
          mentors: ['wangli'],
          accessControlEnabled: true,
          accessDomainGroupIds: [3],
        }),
      ],
      named,
      7
    )

    expect(view.changes[0].rows.map((r) => [r.key, r.value])).toEqual([
      ['days_to_submit', t('tasks.assistant.changes.days', { n: 30 })],
      ['mentors', '王立'],
      ['access', '本校邮箱'],
    ])
  })

  it('没有表单的页面：每份草稿各一张卡', () => {
    const view = writesView([edit(1, 7, { name: 'A' }), edit(2, 8, { name: 'B' })], terms, null)

    expect(view.changes.map((c) => c.draft)).toEqual([7, 8])
  })

  it('改的是自己的通知设置：一张卡，卡头写改了什么，每一项能撤销，不受页面上有没有表单影响', () => {
    const setting = {
      ...edit(9, 0, {}),
      kind: 'settings',
      title: '通知设置',
      undoable: true,
      changed: { push: { label: '推送通知', before: 'on', after: 'off' } },
    } as unknown as AssistantEdit
    const view = writesView([edit(1, 7, { name: 'A' }), setting], terms, 7, () => false)

    expect(view.changes).toHaveLength(2)
    const card = view.changes[1]
    expect(card.title).toBe('通知设置')
    expect(card.rows.map((r) => [r.label, r.value])).toEqual([['推送通知', t('tasks.assistant.changes.setting.off')]])
    expect(card.canUndo).toBe(true)
    expect(card.collapsed).toBeFalsy()
  })

  it('记下、改写、忘掉一条记忆：各一张卡，写做了哪一种和它的描述，能撤销', () => {
    const memory = (id: number, before: string | null, after: string | null) =>
      ({
        ...edit(id, 0, {}),
        kind: 'memory',
        title: '记忆',
        undoable: true,
        changed: { memory: { label: '给老师写东西用书面语', before, after } },
      }) as unknown as AssistantEdit
    const view = writesView([memory(1, null, 'a'), memory(2, 'a', 'b'), memory(3, 'b', null)], terms, null)

    expect(view.changes.map((c) => [c.title, c.rows[0].value, c.rows[0].label, c.canUndo])).toEqual([
      [
        t('tasks.assistant.changes.memory.title'),
        '给老师写东西用书面语',
        t('tasks.assistant.changes.memory.saved'),
        true,
      ],
      [
        t('tasks.assistant.changes.memory.title'),
        '给老师写东西用书面语',
        t('tasks.assistant.changes.memory.changed'),
        true,
      ],
      [
        t('tasks.assistant.changes.memory.title'),
        '给老师写东西用书面语',
        t('tasks.assistant.changes.memory.forgotten'),
        true,
      ],
    ])
  })
})

describe('interleave', () => {
  const step = (key: string, at?: number): PanelStep => ({ key, verb: '读取', arg: '', state: 'done', at })
  const shape = (text: string, steps: PanelStep[]) =>
    interleave(text, steps).map((p) => (p.kind === 'text' ? p.text : p.steps.map((s) => s.key).join('+')))

  it('places each step after the words written before it, and steps in a row together', () => {
    const text = '我先查一下。\n\n有两件事。\n\n都处理好了。'
    expect(shape(text, [step('a', 6), step('b', 6), step('c', 13)])).toEqual([
      '我先查一下。',
      'a+b',
      '有两件事。',
      'c',
      '都处理好了。',
    ])
  })

  it('puts steps that kept no place before the words, as answers did before', () => {
    expect(shape('好了。', [step('a'), step('b')])).toEqual(['a+b', '好了。'])
  })

  it('shows steps alone when nothing was written', () => {
    expect(shape('', [step('a', 0)])).toEqual(['a'])
  })
})

describe('进度清单', () => {
  const todo = (call: string, todos: unknown, state: 'run' | 'done' = 'done') =>
    stepView({ call, name: 'cheese_todo', args: JSON.stringify({ todos }), state, at: 0 })

  it('is drawn from the last list written, and writing it is not a step of its own', () => {
    const first = todo('t1', [{ content: '读评分标准', status: 'in_progress' }])
    const read = stepView({ call: 'r1', name: 'cheese_read', args: '{"ref":"library/a.pdf"}', state: 'done', at: 0 })
    const second = todo('t2', [
      { content: '读评分标准', status: 'completed' },
      { content: '打分', status: 'in_progress' },
    ])

    expect(checklistOf([first, read, second])).toEqual([
      { content: '读评分标准', status: 'completed' },
      { content: '打分', status: 'in_progress' },
    ])
    expect(interleave('', [first, read, second]).flatMap((p) => (p.kind === 'steps' ? p.steps : []))).toEqual([read])
  })

  it('keeps the last whole list while the next one is still being written', () => {
    const whole = todo('t1', [{ content: '读评分标准', status: 'in_progress' }])
    const half = stepView({ call: 't2', name: 'cheese_todo', args: '{"todos":[{"content":"读', state: 'run' })

    expect(checklistOf([whole, half])).toEqual([{ content: '读评分标准', status: 'in_progress' }])
  })
})

describe('分身', () => {
  it('turns with its subagent: spinning while it works, done when it reports back', () => {
    const start = stepView({ call: 'c1', name: 'cheese_subagent_start', args: '{"description":"打分"}', state: 'done' })
    const sub = (status: 'running' | 'done') => ({
      id: 'branch-1',
      call: 'c1',
      description: '打分',
      status,
      steps: [{ name: 'cheese_read', args: '{"ref":"library/a.pdf"}' }],
    })

    const [working] = withSubagents([start], [sub('running')])
    const [back] = withSubagents([start], [sub('done')])

    expect(working).toMatchObject({ arg: '打分', state: 'run', subagent: { status: 'running' } })
    expect(back.state).toBe('done')
    expect(back.subagent?.steps.map((s) => s.arg)).toEqual(['a.pdf'])
  })
})
