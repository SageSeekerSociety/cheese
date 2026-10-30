/** 个人芝士的面板：头上写着是谁在回答、属于谁；说出去的话交给外面去问；芝士还在答的
 * 时候不能再问，也不能开新话题，但能停下；停下的回答留着写出的部分；话题空着时，起步问题
 * 点一下就是一个问题；之前的话题摆在上面，用「新话题」隔开，「更早的话题」再往上读。 */
import type { AssistantEdit, AssistantMessage } from '@/stores/agentThread'
import type { PanelMessage } from './AssistantPanel.vue'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { beforeEach, describe, expect, it } from 'vitest'

import AssistantPanel from './AssistantPanel.vue'

import i18n, { setLocale } from '@/i18n'
import { panelMessages } from '@/lib/draftChangeView'
import { composerText, pressInComposer, typeInComposer } from '@/test/composerInput'

const SAID: PanelMessage[] = [
  { role: 'user', text: '学习率一般设多少？', at: '2026-10-01T08:00:00Z' },
  { role: 'assistant', text: '常见起点是 0.1', at: '2026-10-01T08:00:05Z' },
]

function mount(props: Partial<InstanceType<typeof AssistantPanel>['$props']> = {}) {
  return render(AssistantPanel, {
    props: {
      messages: SAID,
      streaming: null,
      tool: null,
      notice: null,
      busy: false,
      ...props,
    },
    global: { plugins: [createVuetify({ components, directives }), i18n] },
  })
}

beforeEach(() => setLocale('zh-CN'))

describe('AssistantPanel', () => {
  it('hands what was typed to the page and clears the box', async () => {
    const view = mount()
    await typeInComposer(view.container, '  验证集怎么划？ ')
    await fireEvent.click(view.getByRole('button', { name: '发送' }))

    expect(view.emitted('send')).toEqual([['验证集怎么划？']])
    await waitFor(() => expect(composerText(view.container)).toBe(''))
  })

  it('sends files handed over alone, with nothing typed', async () => {
    const view = mount({ files: [{ name: '课题.pdf', uploading: false }], canAttach: true })
    await fireEvent.click(view.getByRole('button', { name: '发送' }))

    expect(view.emitted('send')).toEqual([['']])
  })

  it('says who is answering: there is one 芝士, so nothing says whose', () => {
    const view = mount()
    expect(view.getByText('芝士')).toBeTruthy()
    expect(view.queryByText('个人')).toBeNull()
  })

  it('takes what is typed while 芝士 is answering into that answer, but no new topic', async () => {
    const view = mount({ busy: true, streaming: '常见起点是' })
    await typeInComposer(view.container, '只看第 3 份')
    await fireEvent.keyDown(view.getByRole('textbox'), { key: 'Enter' })
    await fireEvent.click(view.getByRole('button', { name: '新话题' }))

    expect(view.emitted('send')).toEqual([['只看第 3 份']])
    expect(view.emitted('new')).toBeUndefined()
  })

  it('takes files while 芝士 is answering, and sends them into that answer', async () => {
    const view = mount({
      busy: true,
      streaming: '常见起点是',
      canAttach: true,
      files: [{ name: '第3份.pdf', mime: 'application/pdf', uploading: false }],
    })
    expect(view.getByRole('button', { name: '更多操作' })).toBeTruthy()
    await fireEvent.click(view.getByRole('button', { name: '发送' }))

    expect(view.emitted('send')).toEqual([['']])
    expect(view.emitted('stop')).toBeUndefined()
  })

  it('stops the answer being written, and asks nothing new', async () => {
    const view = mount({ busy: true, streaming: '常见起点是' })
    await fireEvent.click(view.getByRole('button', { name: '停止' }))

    expect(view.emitted('stop')).toHaveLength(1)
    expect(view.emitted('send')).toBeUndefined()
  })

  it('keeps a stopped answer and says it was stopped', () => {
    const view = mount({
      messages: [
        { role: 'user', text: '学习率设多少？', at: '2026-10-04T08:00:00Z' },
        { role: 'assistant', text: '常见起点是 0.1', stopped: true, at: '2026-10-04T08:00:05Z' },
      ],
    })

    expect(view.getByText('常见起点是 0.1')).toBeTruthy()
    expect(view.getByText('已停止')).toBeTruthy()
  })

  it('a file 芝士 handed over is a card under its answer, and its download goes to the page', async () => {
    const file = { team: 9, path: '数据结构/成绩分布.png', bytes: 84 * 1024 }
    const view = mount({
      messages: [
        { role: 'user', text: '画一张分布图', at: '2026-10-04T08:00:00Z' },
        { role: 'assistant', text: '画好了。', at: '2026-10-04T08:00:05Z', handed: [file] },
      ],
    })

    expect(view.getByText('成绩分布.png')).toBeTruthy()
    await fireEvent.click(view.getByRole('button', { name: '下载' }))
    expect(view.emitted('downloadFile')).toEqual([[file]])
  })

  it('opens a link to a page of the site in place, and leaves links elsewhere to the browser', async () => {
    const view = mount({
      messages: [
        { role: 'user', text: '进展如何', at: '2026-10-04T08:00:00Z' },
        {
          role: 'assistant',
          text: '见 [部署值守](/projects/cheese-dev/channels/15) 和 [文档](https://docs.okcheese.com/apps)',
          at: '2026-10-04T08:00:05Z',
        },
      ],
    })

    await fireEvent.click(view.getByRole('link', { name: '部署值守' }))
    await fireEvent.click(view.getByRole('link', { name: '文档' }))
    expect(view.emitted('openLink')).toEqual([['/projects/cheese-dev/channels/15']])
  })

  it('shows what 芝士 said and the calls it made in the order they happened', () => {
    const view = mount({
      messages: [
        { role: 'user', text: '有什么要我处理的？', at: '2026-10-04T08:00:00Z' },
        {
          role: 'assistant',
          text: '我先查一下待办。\n\n有一件：周明的邀请。',
          at: '2026-10-04T08:00:05Z',
          steps: [{ key: 'c1', verb: '查看待办', arg: '', state: 'done', at: 8 }],
        },
      ],
    })

    const said = view.getByText('我先查一下待办。')
    const call = view.getByText('查看待办')
    const after = view.getByText('有一件：周明的邀请。')
    expect(said.compareDocumentPosition(call) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(call.compareDocumentPosition(after) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('while 芝士 goes on after its last call came back, a line says it is thinking', () => {
    const view = mount({
      busy: true,
      streaming: '',
      steps: [{ key: 'c1', verb: '查看待办', arg: '', state: 'done', at: 0 }],
    })

    expect(view.getByText('思考中')).toBeTruthy()
  })

  it('says 芝士 gave no answer when it did something and wrote nothing', () => {
    const view = mount({
      messages: [
        { role: 'user', text: '有什么待办？', at: '2026-10-04T08:00:00Z' },
        {
          role: 'assistant',
          text: '',
          silent: true,
          at: '2026-10-04T08:00:05Z',
          steps: [{ key: 'c1', verb: '查看待办', arg: '', state: 'done', at: 0 }],
        },
      ],
    })

    expect(view.getByText('芝士没有给出回答')).toBeTruthy()
  })

  it('reads the earlier messages of a long topic on asking', async () => {
    const view = mount({ currentEarlier: true })

    await fireEvent.click(view.getByRole('button', { name: '更早的消息' }))

    expect(view.emitted('olderMessages')).toEqual([[null]])
  })

  it('shows the checklist above an answer under way, and folds it to a line once all is done', () => {
    const list = (status: 'in_progress' | 'completed') =>
      JSON.stringify({
        todos: [
          { content: '读评分标准', status: 'completed' },
          { content: '给 40 份报告打分', status },
        ],
      })
    const step = (status: 'in_progress' | 'completed') => ({
      key: 't1',
      verb: '更新进度清单',
      arg: '',
      state: 'done' as const,
      hidden: true,
      checklist: JSON.parse(list(status)).todos,
    })

    const going = mount({ busy: true, streaming: '', steps: [step('in_progress')] })
    expect(going.getByText('给 40 份报告打分')).toBeTruthy()
    expect(going.getByRole('img', { name: '进行中' })).toBeTruthy()
    expect(going.queryByText('更新进度清单')).toBeNull()
    going.unmount()

    const done = mount({
      messages: [
        { role: 'user', text: '打分', at: '2026-10-04T08:00:00Z' },
        { role: 'assistant', text: '打完了。', at: '2026-10-04T08:00:05Z', steps: [step('completed')] },
      ],
    })
    expect(done.getByRole('button', { name: /2\/2 项已完成/ })).toBeTruthy()
    expect(done.queryByText('给 40 份报告打分')).toBeNull()
  })

  it('shows a subagent on the step that started it, and what it did on opening it', async () => {
    const view = mount({
      busy: true,
      streaming: '',
      steps: [
        {
          key: 'c1',
          verb: '派出分身',
          arg: '给第 1–10 份打分',
          state: 'run',
          at: 0,
          subagent: { status: 'running', steps: [{ key: 's1', verb: '读取', arg: '报告 1.pdf', state: 'done' }] },
        },
      ],
    })

    expect(view.getByText('进行中')).toBeTruthy()
    expect(view.queryByText('报告 1.pdf')).toBeNull()
    await fireEvent.click(view.getByRole('button', { name: /给第 1–10 份打分/ }))
    expect(view.getByText('报告 1.pdf')).toBeTruthy()
  })

  it('asks a starter question in one click when the conversation is empty', async () => {
    const view = mount({ messages: [] })
    await fireEvent.click(view.getByRole('button', { name: '做这道题要先会什么？' }))

    expect(view.emitted('send')).toEqual([['做这道题要先会什么？']])
  })

  it('shows earlier topics above, set apart by 新话题, and reads further back on asking', async () => {
    const view = mount({
      messages: [],
      earlier: [{ id: 'a', title: '学习率一般设多少？', at: '2026-10-01T08:00:00Z', messages: SAID }],
      hasOlder: true,
    })

    expect(view.getByText('常见起点是 0.1')).toBeTruthy()
    expect(view.getAllByText('新话题').length).toBeGreaterThan(0)
    await fireEvent.click(view.getByRole('button', { name: '更早的话题' }))
    expect(view.emitted('older')).toHaveLength(1)
  })

  it('pins itself to the right, or floats, when the page can dock it', async () => {
    const view = mount({ dockable: true, docked: false })
    await fireEvent.click(view.getByRole('button', { name: '固定到右侧' }))
    expect(view.emitted('dock')).toEqual([[true]])
  })

  it('a run the platform started reads as what started it, not as what the platform told 芝士', () => {
    const view = mount({
      messages: [
        {
          role: 'platform',
          text: '<平台>\n例行事务「每日待办」现在运行',
          at: '2026-10-01T00:00:00Z',
          shown: { routine: '每日待办' },
        },
        { role: 'assistant', text: '今天没有待办。', at: '2026-10-01T00:00:05Z' },
      ],
    })
    expect(view.getByText('自动化「每日待办」开始运行')).toBeTruthy()
    expect(view.queryByText(/<平台>/)).toBeNull()
  })
})

describe('AssistantPanel: what a question carries', () => {
  const CITABLE = [
    { id: 'a1', kind: 'task' as const, title: '接入搜索', where: '知是', place: '后端' },
    { id: 'd1', kind: 'doc' as const, title: '搜索接入方案', where: '知是' },
  ]

  it('a question that cited things shows them by name, where it was asked, and that it carried a selection', () => {
    const view = mount({
      messages: [
        {
          role: 'user',
          text: '比较 <#d1> 和 <#file-f1>',
          at: '2026-10-24T08:00:00Z',
          askedOn: '频道「后端」',
          selection: '超时设成 600 秒',
          cited: [
            { id: 'd1', kind: 'doc', title: '搜索接入方案' },
            { id: 'file-f1', kind: 'file', title: '数据集说明.pdf' },
          ],
        },
      ],
    })

    expect(view.getByText('搜索接入方案')).toBeTruthy()
    expect(view.getByText('数据集说明.pdf')).toBeTruthy()
    expect(view.queryByText(/<#/)).toBeNull()
    expect(view.getByText('频道「后端」')).toBeTruthy()
    expect(view.getByText('超时设成 600 秒')).toBeTruthy()
  })

  it('the page can be left out and put back, and a selection taken off, in the box beside the files', async () => {
    const view = mount({ pageLabel: '频道「后端」', selection: '超时设成 600 秒' })

    await fireEvent.click(view.getByRole('button', { name: '不带这一页' }))
    await fireEvent.click(view.getByRole('button', { name: '去掉选中的内容' }))
    await view.rerender({ pageOff: true })
    await fireEvent.click(view.getByRole('button', { name: '带上这一页' }))

    expect(view.emitted('togglePage')).toHaveLength(2)
    expect(view.emitted('clearSelection')).toHaveLength(1)
  })

  it('@ opens the menu, letters narrow it, and Enter writes the reference where the @ was', async () => {
    const view = mount({ citable: CITABLE, project: '知是' })

    await typeInComposer(view.container, '比较 @jrss')
    await waitFor(() => expect(view.getByRole('listbox')).toBeTruthy())
    expect(view.emitted('citeOpen')).toBeTruthy()
    pressInComposer(view.container, 'Enter')

    await waitFor(() => expect(composerText(view.container)).toBe('比较 <#a1> '))
    expect(view.emitted('cite')).toEqual([[CITABLE[0]]])
    expect(view.emitted('send')).toBeUndefined()
  })
})

describe('AssistantPanel: what saving the stages of a draft would do', () => {
  const WARNING = '1 位参与者有已过截止、还没提交的阶段，保存后将判为未通过'
  const write = (
    id: number,
    stageEffect: AssistantEdit['stageEffect'],
    changed: AssistantEdit['changed'] = { stages: { before: [], after: [] } }
  ): AssistantEdit => ({
    id,
    kind: 'draft',
    title: '',
    undoable: false,
    draft: 41,
    taskId: 9,
    created: false,
    version: id,
    name: '实验一',
    changed,
    kept: [],
    notes: [],
    stageEffect,
    undone: [],
  })
  const answer = (at: string, edits: AssistantEdit[], asked = '改一下阶段截止'): AssistantMessage[] => [
    { role: 'user', text: asked, at },
    { role: 'assistant', text: '改好了', at, edits },
  ]
  const terms = { categories: [], topics: [] }

  it('a warning a later answer took back is no longer waiting on the person, in any answer', () => {
    const moved = answer('2026-10-01T08:00:00Z', [write(1, [{ key: 'stageEffectFailing', params: { n: 1 } }])])
    expect(mount({ messages: panelMessages(moved, terms) }).container.textContent).toContain(WARNING)
    cleanup()

    const restored = answer('2026-10-01T08:05:00Z', [write(2, [])])
    const view = mount({ messages: panelMessages([...moved, ...restored], terms) })

    expect(view.container.textContent).not.toContain(WARNING)
  })

  it('a warning that still holds goes with the latest answer that touched the draft, whatever it changed', () => {
    const failing: AssistantEdit['stageEffect'] = [{ key: 'stageEffectFailing', params: { n: 1 } }]
    const moved = answer('2026-10-01T08:00:00Z', [write(1, failing)])
    const renamed = answer(
      '2026-10-01T08:05:00Z',
      [write(2, failing, { name: { before: '实验一', after: '实验一（修订）' } })],
      '把题目改个名'
    )
    const text = mount({ messages: panelMessages([...moved, ...renamed], terms) }).container.textContent ?? ''

    expect(text.split(WARNING).length - 1).toBe(1)
    expect(text.indexOf(WARNING)).toBeGreaterThan(text.indexOf('把题目改个名'))
  })
})
