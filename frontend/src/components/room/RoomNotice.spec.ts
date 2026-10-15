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
  it('每个文件带着自己的增删', () => {
    const { container } = mount(changes(['a.ts', 'b.ts']))
    const text = container.textContent ?? ''
    expect(text).toContain('a.ts')
    expect(text).toContain('+10')
    expect(text).toContain('b.ts')
    expect(text).toContain('+11')
    expect(text).toContain('−1')
  })

  it('文件多的时候先列前几个，其余点一下就展开', async () => {
    const paths = ['a.ts', 'b.ts', 'c.ts', 'd.ts', 'e.ts']
    const { container, getByRole } = mount(changes(paths))
    expect(container.textContent).not.toContain('e.ts')
    await fireEvent.click(getByRole('button', { name: /另 \d+ 个文件/ }))
    for (const p of paths) expect(container.textContent).toContain(p)
  })

  it('后端没发过来的那几个也算进「另 N 个」，不假装已经列全', async () => {
    const { container, getByRole } = mount(changes(['a.ts', 'b.ts', 'c.ts', 'd.ts'], 8))
    expect(getByRole('button', { name: /另 9 个文件/ })).toBeTruthy()
    await fireEvent.click(getByRole('button', { name: /另 9 个文件/ }))
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
        { name: 'project-routines', path: '/projects/:projectId/routines', component: stub },
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

  it('提到一条规则：一颗「打开这条」，带的是这条的编号，不是一个去处', async () => {
    const { getByTestId, emitted } = await mountWithRouter(
      proposed({ event_type: 'routine_proposed', routine_id: 'r-9' })
    )
    const btn = getByTestId('notice-routine')
    expect(btn.textContent?.trim()).toBe('打开这条')
    // 它不换页：规则长在这个房间里，人也就在这个房间里。所以没有 href，只有一次上报。
    expect(btn.getAttribute('href')).toBeNull()
    await fireEvent.click(btn)
    expect(emitted()['open-routine']).toEqual([['r-9']])
  })

  it('紧挨着还有第二颗：直接看这一条的执行记录，一样是这个房间里的动作', async () => {
    const { getByTestId, emitted } = await mountWithRouter(
      proposed({ event_type: 'routine_proposed', routine_id: 'r-9' })
    )
    const btn = getByTestId('notice-routine-records')
    expect(btn.textContent?.trim()).toBe('看执行记录')
    expect(btn.getAttribute('href')).toBeNull()
    await fireEvent.click(btn)
    expect(emitted()['open-routine-records']).toEqual([['r-9']])
  })

  it('开始执行的那一句也带「打开这条」', async () => {
    const { getByTestId } = await mountWithRouter(proposed({ event_type: 'routine_run', routine_id: 'r-9' }))
    expect(getByTestId('notice-routine').textContent?.trim()).toBe('打开这条')
  })

  it('这两类都不再是「等人确认」的样子：没有「去确认」', async () => {
    for (const kind of ['routine_proposed', 'routine_run']) {
      const { queryByTestId } = await mountWithRouter(proposed({ event_type: kind, routine_id: 'r-9' }))
      expect(queryByTestId('notice-confirm')).toBeNull()
    }
  })

  it('规则删了以后：那一行只说结果，不再指过去', async () => {
    const block = proposed({ event_type: 'routine_proposed', routine_id: 'r-9', who: '', state: 'over' })
    block.content = '芝士删掉了例行事务「每周进展汇总」'
    const { queryByTestId, container } = await mountWithRouter(block)
    expect(container.textContent).toContain('删掉了例行事务')
    expect(queryByTestId('notice-routine')).toBeNull()
    // 两颗都收掉：那条规则已经不在了，看它的记录也看不到东西。
    expect(queryByTestId('notice-routine-records')).toBeNull()
  })

  it('整理的技能：通到技能页的那一条', async () => {
    const { getByTestId } = await mountWithRouter(proposed({ event_type: 'skill_proposed', skill_id: 's-3' }))
    expect(getByTestId('notice-confirm').getAttribute('href')).toBe('/projects/proj-1/skills?skill=s-3')
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
    // 一次执行的结果本身不带编号（编号在它上面那一行「开始执行」上），所以也不指。
    expect(queryByTestId('notice-routine')).toBeNull()
    expect(queryByTestId('notice-routine-records')).toBeNull()
  })
})
