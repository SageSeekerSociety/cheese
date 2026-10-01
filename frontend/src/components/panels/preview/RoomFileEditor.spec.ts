// 编辑器打不开时，为什么打不开由后端给一个码（`reason`），这里按读者的语言说出来。
import type { Component } from 'vue'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render } from '@testing-library/vue'
import { afterEach, beforeEach, describe, it, vi } from 'vitest'

const openRoomFileEditor = vi.fn()

vi.mock('../../../api', async () => {
  const actual = await vi.importActual<typeof import('../../../api')>('../../../api')
  return { ...actual, openRoomFileEditor: (...a: unknown[]) => openRoomFileEditor(...a) }
})

import RoomFileEditor from './RoomFileEditor.vue'

import { setLocale } from '@/i18n'

const vuetify = createVuetify({ components, directives })

function mount() {
  return render(RoomFileEditor as unknown as Component, {
    props: { topicId: 'room-1', path: 'output/报告.docx' },
    global: { plugins: [vuetify] },
  })
}

describe('编辑器打不开的理由', () => {
  beforeEach(() => setLocale('zh-CN'))
  afterEach(() => setLocale('zh-CN'))

  it('英文界面说英文', async () => {
    setLocale('en')
    openRoomFileEditor.mockResolvedValue({ enabled: false, reason: 'unsupported' })
    await mount().findByText("This kind of file can't be edited online")
  })

  it('中文界面说中文', async () => {
    openRoomFileEditor.mockResolvedValue({ enabled: false, reason: 'not_configured' })
    await mount().findByText('这个部署没有启用在线编辑')
  })
})
