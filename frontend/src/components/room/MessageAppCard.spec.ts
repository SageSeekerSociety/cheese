/**
 * 对话里的应用卡片说它此刻怎样：这一条是房间当前的预览且应用应答了，是「运行中」；
 * 通道在、应用没应答，是「应用没在跑」；芝士后来摆了别的，这一条就「已换成更新的预览」。
 * 状态按这条消息所在的那段对话去问。
 */
import { createVuetify } from 'vuetify'
import { cleanup, render, screen } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import MessageAppCard from './MessageAppCard.vue'

import { setLocale } from '@/i18n'
import { FILE_FACTS_SOURCE, type LivePreview } from '@/lib/fileFactsSource'

const livePreview = vi.fn()

beforeEach(() => setLocale('zh-CN'))
afterEach(cleanup)

function mount(live: LivePreview | null) {
  livePreview.mockResolvedValue(live)
  return render(MessageAppCard, {
    props: { conversationId: 'task-1', blockId: 'b1', description: '报名系统' },
    global: {
      plugins: [createVuetify()],
      provide: {
        [FILE_FACTS_SOURCE as symbol]: {
          versions: () => Promise.resolve([]),
          versionBytes: () => Promise.reject(new Error('x')),
          livePreview,
        },
      },
    },
  })
}

it('says it is running when it is the current preview and the app answers', async () => {
  mount({ artifactId: 'b1', running: true, tunnelUp: true })
  expect(await screen.findByText('实时运行中')).toBeTruthy()
  expect(livePreview).toHaveBeenCalledWith('task-1')
})

it('tells an exited app from an offline machine', async () => {
  mount({ artifactId: 'b1', running: false, tunnelUp: true })
  expect(await screen.findByText('机器在线，应用没在跑')).toBeTruthy()
  cleanup()
  mount({ artifactId: 'b1', running: false, tunnelUp: false })
  expect(await screen.findByText('运行环境离线')).toBeTruthy()
})

it('says a newer preview replaced it when the room now shows something else', async () => {
  mount({ artifactId: 'b2', running: true, tunnelUp: true })
  expect(await screen.findByText('已换成更新的预览')).toBeTruthy()
})
