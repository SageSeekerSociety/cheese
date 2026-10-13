import type { Notification } from '@/types/apiNotifications'

import { render } from '@testing-library/vue'
import { beforeAll, expect, it } from 'vitest'

import { getNotificationMark } from '../registry'

import RenderRoomNoticeNotification from './RenderRoomNoticeNotification.vue'

import i18n, { setLocale } from '@/i18n'

beforeAll(() => setLocale('zh-CN'))

// 平台在房间里说的、要人动手的那一句：标题是房间那一行（键 + 参数，按读者的语言
// 渲染），副标题说在哪个房间。卡一有决定，服务端把结局并进 `outcome`。
function notice(outcome?: string): Notification {
  return {
    id: 1,
    type: 'ROOM_NOTICE',
    read: true,
    createdAt: 0,
    entities: {},
    contextMetadata: {
      projectId: 'p1',
      topicId: 't1',
      topicTitle: '云端',
      message: { key: 'acceptReady', params: { pr: 7, reviewer: 'lz123y' } },
      ...(outcome ? { outcome } : {}),
    },
  }
}

function open(notification: Notification) {
  return render(RenderRoomNoticeNotification, {
    props: { notification },
    global: { plugins: [i18n] },
  })
}

it('an open notice says the room line and where it is', () => {
  const view = open(notice())

  expect(view.getByText('PR #7 可以合并了，等 lz123y 采纳')).toBeTruthy()
  expect(view.getByText('在「云端」')).toBeTruthy()
})

it('a settled notice says the outcome instead of still waiting', () => {
  const view = open(notice('accepted'))

  expect(view.getByText('在「云端」，已采纳')).toBeTruthy()
})

it('an outcome this build does not know still reads as settled', () => {
  const view = open(notice('superseded'))

  expect(view.getByText('在「云端」，已办结')).toBeTruthy()
})

// 画成警示色、铃铛，等于说它还在等人：办完的那一条换成对勾，颜色退成 success。
it('a settled notice stops being drawn as waiting on someone', () => {
  expect(getNotificationMark(notice('rejected'))).toEqual({
    icon: 'mdi-check-circle-outline',
    color: 'success',
  })
  expect(getNotificationMark(notice()).color).toBe('warning')
})
