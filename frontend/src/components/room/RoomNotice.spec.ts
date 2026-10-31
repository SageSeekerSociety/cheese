/** 本轮改动那一行：改了哪些文件、各改了多少，先看得到几个，其余一点就有。 */
import type { Component } from 'vue'
import type { Block } from '@/cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import { beforeEach, describe, expect, it } from 'vitest'

import { setLocale } from '../../i18n'
import { collapseNotices } from '../../lib/platformNotice'

import RoomNotice from './RoomNotice.vue'

const vuetify = createVuetify({ components, directives })

function changes(paths: string[], omitted = 0): Block {
  return {
    id: 'c1',
    conversation_id: 't',
    kind: 'event',
    author_type: 'platform',
    author: 'system',
    content: '改动',
    turn_id: 'turn-1',
    created_at: '2026-09-25T08:00:00Z',
    meta: {
      changeset: {
        files_total: paths.length + omitted,
        added: 0,
        removed: 0,
        files: paths.map((path, i) => ({ path, added: 10 + i, removed: i })),
        files_omitted: omitted,
      },
    },
  } as unknown as Block
}

function mount(block: Block) {
  const [row] = collapseNotices([
    block,
    { ...block, id: 'd1', content: '更新了文档', meta: { action: 'doc' } } as Block,
  ])
  return render(RoomNotice as Component, {
    props: {
      block: row.block,
      notice: row.notice!,
      run: row.run,
      agent: { name: '芝士', handle: 'cheese-a1' },
      time: '16:05',
      agentName: '芝士',
      refs: { mentionNames: {}, topicTitles: {} },
    },
    global: { plugins: [vuetify] },
  })
}

beforeEach(() => setLocale('zh-CN'))

describe('本轮改动的文件', () => {
  it('收成一行，点开才列出每个文件和它自己的增删', async () => {
    const { container, getByRole } = mount(changes(['a.ts', 'b.ts']))
    expect(container.textContent).toContain('2')
    expect(container.textContent).not.toContain('a.ts')
    await fireEvent.click(getByRole('button', { name: '改动的文件' }))
    const text = container.textContent ?? ''
    expect(text).toContain('a.ts')
    expect(text).toContain('+10')
    expect(text).toContain('b.ts')
    expect(text).toContain('+11')
    expect(text).toContain('−1')
  })

  it('后端没发过来的那几个，点开后说「另 N 个」，不假装已经列全', async () => {
    const { container, getByRole } = mount(changes(['a.ts', 'b.ts', 'c.ts', 'd.ts'], 8))
    await fireEvent.click(getByRole('button', { name: '改动的文件' }))
    expect(container.textContent).toContain('d.ts')
    expect(container.textContent).toContain('另 8 个文件')
  })
})

describe('平台自动改了标题', () => {
  const renamed = {
    id: 'rename-1',
    conversation_id: 't',
    kind: 'event',
    author_type: 'platform',
    author: 'system',
    content: '标题自动更新为「Valkey 连接池耗尽」（原为「dev 外网访问慢」）',
    turn_id: null,
    created_at: '2026-09-26T08:00:00Z',
    meta: { action: 'title', who: 'platform', from: 'dev 外网访问慢', to: 'Valkey 连接池耗尽' },
  } as unknown as Block

  it('旧的改名记录照常显示，不再带撤销', () => {
    const [row] = collapseNotices([renamed])
    const view = render(RoomNotice as Component, {
      props: {
        block: row.block,
        notice: row.notice!,
        run: row.run,
        agent: { name: '芝士', handle: 'cheese-a1' },
        time: '16:05',
        agentName: '芝士',
        refs: { mentionNames: {}, topicTitles: {} },
      },
      global: { plugins: [vuetify] },
    })
    expect(view.container.textContent).toContain('标题自动更新为「Valkey 连接池耗尽」')
    expect(view.queryByRole('button', { name: '撤销' })).toBeNull()
  })
})

describe('芝士起草、等人确认的那一行', () => {
  function proposed(meta: Record<string, unknown>): Block {
    return {
      id: 'p1',
      conversation_id: 't',
      kind: 'event',
      author_type: 'platform',
      author: 'system',
      content: '芝士起草了「每周进展」，要你确认后才会执行',
      created_at: '2026-09-27T08:00:00Z',
      meta: { severity: 'info', who: 'human', detail: '名称：每周进展', detail_label: '待确认的配置', ...meta },
    } as unknown as Block
  }

  async function mountWithRouter(block: Block) {
    const { createMemoryHistory, createRouter } = await import('vue-router')
    const stub = { template: '<div />' }
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/', component: stub },
        { name: 'project-skills', path: '/projects/:projectId/skills', component: stub },
        { name: 'project-settings', path: '/projects/:projectId/settings/:section?', component: stub },
      ],
    })
    const [row] = collapseNotices([block])
    return render(RoomNotice as Component, {
      props: {
        block: row.block,
        notice: row.notice!,
        run: row.run,
        agent: { name: '芝士', handle: 'cheese-a1' },
        time: '16:05',
        agentName: '芝士',
        refs: { mentionNames: {}, topicTitles: {} },
        projectId: 'proj-1',
      },
      global: { plugins: [vuetify, router] },
    })
  }

  it('提议的例行事务是一张卡片：标「待确认」，点它在右侧打开', async () => {
    const view = await mountWithRouter(proposed({ event_type: 'routine_proposed', routine_id: 'r-9' }))
    expect(view.getByTestId('work-card').textContent).toContain('待确认')
    await fireEvent.click(view.getByRole('button', { name: /每周进展/ }))
    expect(view.emitted('open-proposal')).toEqual([['routine', 'r-9']])
  })

  it('规则那两类都不是「等人确认」的样子：没有「去确认」', async () => {
    for (const kind of ['routine_proposed', 'routine_run']) {
      const { queryByTestId } = await mountWithRouter(proposed({ event_type: kind, routine_id: 'r-9' }))
      expect(queryByTestId('notice-confirm')).toBeNull()
    }
  })

  it('规则删了以后：那一行只说结果', async () => {
    const block = proposed({ event_type: 'routine_proposed', routine_id: 'r-9', who: '', state: 'over' })
    block.content = '芝士删掉了例行事务「每周进展汇总」'
    const { container } = await mountWithRouter(block)
    expect(container.textContent).toContain('删掉了例行事务')
    expect(container.querySelector('.sys-btn')).toBeNull()
  })

  it('提议的技能不在对话里直接保存：点开在右侧读完再决定', async () => {
    const view = await mountWithRouter(proposed({ event_type: 'skill_proposed', skill_id: 's-3' }))
    expect(view.queryByRole('button', { name: '保存' })).toBeNull()
    await fireEvent.click(view.getByRole('button', { name: /每周进展/ }))
    expect(view.emitted('open-proposal')).toEqual([['skill', 's-3']])
  })

  it('人定下来以后，卡片写的是结果', async () => {
    const block = proposed({
      event_type: 'skill_proposed',
      skill_id: 's-3',
      state: 'over',
      i18n: { content: { key: 'skillProposalSavedBy', params: { who: 'lin', title: '组会纪要' } } },
    })
    const view = await mountWithRouter(block)
    const card = view.getByTestId('work-card')
    expect(card.textContent).toContain('组会纪要')
    expect(card.textContent).toContain('已保存')
    expect(card.textContent).not.toContain('待确认')
  })

  it('环境准备失败：一颗「去环境设置」直接打开那个频道的环境', async () => {
    const { getByTestId } = await mountWithRouter(
      proposed({ event_type: 'environment_failed', room_id: 'room-7', who: 'human' })
    )
    const link = getByTestId('notice-environment')
    expect(link.getAttribute('href')).toBe('/projects/proj-1/settings/environment?room=room-7')
  })

  it('人定下来以后：这一行只说结果，不再请人去确认，也不说谁在处理', async () => {
    const block = proposed({ event_type: 'skill_proposed', skill_id: 's-3', who: '', state: 'over' })
    block.content = '芝士提议的技能「组会纪要」已保存'
    const { queryByTestId, container } = await mountWithRouter(block)
    expect(container.textContent).toContain('已保存')
    expect(queryByTestId('notice-confirm')).toBeNull()
    expect(container.textContent).not.toContain('正在处理')
    expect(container.textContent).not.toContain('等人处理')
  })

  it('别的平台提示不带这颗按钮', async () => {
    const { queryByTestId } = await mountWithRouter(proposed({ event_type: 'routine_result', routine_id: 'r-9' }))
    expect(queryByTestId('notice-confirm')).toBeNull()
  })
})

describe('平台替 AI 队友说的话', () => {
  function shown(block: Block, opts: { canRetry?: boolean } = {}) {
    const [row] = collapseNotices([block])
    return render(RoomNotice as Component, {
      props: {
        block: row.block,
        notice: row.notice!,
        run: row.run,
        agent: null,
        time: '16:05',
        agentName: '芝士',
        refs: { mentionNames: {}, topicTitles: {} },
        canRetry: opts.canRetry,
      },
      global: { plugins: [vuetify] },
    })
  }
  const failed = {
    id: 'f1',
    conversation_id: 't',
    kind: 'event',
    author_type: 'platform',
    author: 'system',
    content: '这次没做完：模型服务繁忙',
    turn_id: 'turn-1',
    created_at: '2026-10-31T08:00:00Z',
    shown_as: 'for_agent',
    meta: {
      event_type: 'turn_failed',
      who: 'human',
      retryable: true,
      detail: '这是服务端的问题，通常很快恢复。',
      detail_label: '说明',
    },
  } as unknown as Block

  it('一句话说出了什么事，带上能做的那一步，原因收在「详情」里', async () => {
    const view = shown(failed, { canRetry: true })
    expect(view.container.textContent).toContain('这次没做完：模型服务繁忙')
    expect(view.getByRole('button', { name: '重试' })).toBeTruthy()
    expect(view.container.textContent).not.toContain('通常很快恢复')
    await fireEvent.click(view.getByRole('button', { name: /详情/ }))
    expect(view.container.textContent).toContain('通常很快恢复')
  })

  it('不说谁在处理，也不铺底色', () => {
    const view = shown(failed, { canRetry: true })
    expect(view.container.textContent).not.toContain('等人处理')
    expect(view.container.querySelector('.sys-row--warn, .sys-row--danger')).toBeNull()
  })

  it('这一行已经不能重试了（后面的事盖过了它）：不画一颗点不动的按钮', () => {
    const view = shown(failed, { canRetry: false })
    expect(view.queryByRole('button', { name: '重试' })).toBeNull()
  })
})

describe('退回时写下的理由', () => {
  it('画成审阅那个人的一条消息：他的名字，在做什么，理由原文', () => {
    const rejected = {
      id: 'r1',
      conversation_id: 't',
      kind: 'event',
      author_type: 'platform',
      author: 'system',
      content: '<@lin> 退回了改动，正在修改',
      turn_id: null,
      created_at: '2026-10-31T08:00:00Z',
      shown_as: 'person',
      meta: { event_type: 'card_rejected', by: 'lin', detail: '登录失败的提示也要改' },
    } as unknown as Block
    const [row] = collapseNotices([rejected])
    const view = render(RoomNotice as Component, {
      props: {
        block: row.block,
        notice: row.notice!,
        run: row.run,
        agent: null,
        time: '16:05',
        agentName: '芝士',
        refs: { mentionNames: { lin: '林晓' }, topicTitles: {} },
      },
      global: { plugins: [vuetify] },
    })
    const said = view.getByTestId('person-said')
    expect(said.textContent).toContain('林晓')
    expect(said.textContent).toContain('退回了改动')
    expect(said.textContent).toContain('登录失败的提示也要改')
  })
})
