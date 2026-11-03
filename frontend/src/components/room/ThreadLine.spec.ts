// 主线上那一行：支线里的一轮没回答就出错了，主线上说的就是支线里落下的那一句——
// 同一个说法、同一个原因，按读者的语言说。
import type { ThreadSummary } from '@/types/threads'

import { createVuetify } from 'vuetify'
import { fireEvent, render } from '@testing-library/vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const getThread = vi.fn()
vi.mock('@/api/threads', () => ({ getThread: (id: string) => getThread(id), listThreads: vi.fn() }))
const listBlocks = vi.fn()
vi.mock('@/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api')>()),
  listBlocks: (id: string, opts: unknown) => listBlocks(id, opts),
}))

import ThreadLine from './ThreadLine.vue'

import { setLocale } from '@/i18n'

function summary(extra: Partial<ThreadSummary>): ThreadSummary {
  return {
    id: 't1',
    room_id: 'room1',
    root_block_id: 'root',
    reply_count: 0,
    last_reply_at: null,
    last_reply: null,
    participants: ['lin'],
    tasks: [],
    ...extra,
  }
}

function show(s: ThreadSummary) {
  return render(ThreadLine, {
    props: { summary: s, replying: null, refs: {} as never, nameOf: (h: string) => h, time: null },
    global: { plugins: [createVuetify()] },
  })
}

beforeEach(() => setLocale('zh-CN'))

describe('支线出错时主线上那一行', () => {
  it('说的是支线里那句失败提示，不另起一句', () => {
    const view = show(
      summary({
        failed: true,
        failed_notice: { content: '这次没做完：调用的模型出错了', i18n: { content: { key: 'turnFailedModelError' } } },
      })
    )
    expect(view.getByText('这次没做完：调用的模型出错了')).toBeTruthy()
  })

  it('额度用完拦下的一轮，说的也是支线里那句', () => {
    const view = show(
      summary({
        failed: true,
        failed_notice: { content: '额度已用完，这次没有运行', i18n: { content: { key: 'creditsExhausted' } } },
      })
    )
    expect(view.getByText('额度已用完，这次没有运行')).toBeTruthy()
  })

  it('英文界面说那句提示的英文', () => {
    setLocale('en')
    const view = show(
      summary({
        failed: true,
        failed_notice: { content: '这次没做完：调用的模型出错了', i18n: { content: { key: 'turnFailedModelError' } } },
      })
    )
    expect(view.getByText('Stopped before finishing: the call to the model failed')).toBeTruthy()
  })

  it('没有句子键的旧提示照存下的原句说', () => {
    const view = show(summary({ failed: true, failed_notice: { content: '本轮未完成：这条会话的机器尚未连接' } }))
    expect(view.getByText('本轮未完成：这条会话的机器尚未连接')).toBeTruthy()
  })
})

describe('主线上那一行的头像', () => {
  const names: Record<string, string> = { lin: '林', 'cheese-s': '芝士 S', bob: '鲍勃', amy: '艾米', joe: '乔' }
  function line(s: ThreadSummary, replyingSeats: string[]) {
    return render(ThreadLine, {
      props: {
        summary: s,
        replying: replyingSeats.length ? names[replyingSeats[0]] : null,
        replyingSeats,
        refs: {} as never,
        nameOf: (h: string) => names[h] ?? h,
        time: null,
      },
      global: { plugins: [createVuetify()] },
    })
  }

  it('队友正在回复、支线里还没有回复：头像里有它自己，不只是发起的人', () => {
    const view = line(summary({ participants: ['lin'] }), ['cheese-s'])
    expect(view.getByText('芝士 S 正在回复')).toBeTruthy()
    expect(view.getAllByLabelText('芝士 S')).toHaveLength(1)
  })

  it('说过话的人多到排不下时，正在回复的队友也还在', () => {
    const view = line(summary({ reply_count: 4, participants: ['lin', 'bob', 'amy', 'joe'] }), ['cheese-s'])
    expect(view.getAllByLabelText('芝士 S')).toHaveLength(1)
  })

  it('没有人在回复时，照旧是说过话的人', () => {
    const view = line(summary({ reply_count: 1, participants: ['lin', 'cheese-s'] }), [])
    expect(view.getAllByLabelText('芝士 S')).toHaveLength(1)
  })
})

describe('点开支线之前', () => {
  it('按下那一行时就去取支线和里面的回复，点开时不用再等；没回来之前再按不会再发', async () => {
    getThread.mockReset().mockReturnValue(new Promise(() => {}))
    listBlocks.mockReset().mockReturnValue(new Promise(() => {}))
    const view = show(summary({ id: 't-warm', reply_count: 2, participants: ['lin'] }))
    const button = view.getByTestId('thread-line')

    await fireEvent.pointerDown(button)
    await fireEvent.pointerDown(button)
    await new Promise((resolve) => setTimeout(resolve, 0))

    expect(getThread.mock.calls).toEqual([['t-warm']])
    expect(listBlocks.mock.calls.map(([id]) => id)).toEqual(['t-warm'])
  })
})
