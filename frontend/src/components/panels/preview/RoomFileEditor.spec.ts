// 编辑器打不开时，为什么打不开由后端给一个码（`reason`），这里按读者的语言说出来。
import type { Component } from 'vue'

import { defineComponent, h } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render } from '@testing-library/vue'
import { afterEach, beforeEach, describe, it, vi } from 'vitest'

import { useRoomFileEditor } from '../../../composables/useRoomFileEditor'
import { useRoomFileHistory } from '../../../composables/useRoomFileHistory'

const openRoomFileEditor = vi.fn()
const roomFileRevisions = vi.fn()

vi.mock('../../../api', async () => {
  const actual = await vi.importActual<typeof import('../../../api')>('../../../api')
  return {
    ...actual,
    openRoomFileEditor: (...a: unknown[]) => openRoomFileEditor(...a),
    roomFileRevisions: (...a: unknown[]) => roomFileRevisions(...a),
  }
})

import RoomFileEditor from './RoomFileEditor.vue'

import { setLocale } from '@/i18n'

const vuetify = createVuetify({ components, directives })

// 这一只只画：编辑器的会话由宿主取（产品里是 `components/work/PanelPreviewHost.vue`），
// 所以这里也搭一个最小的宿主，取数照旧走被 mock 掉的接口层。
function mount() {
  const Host = defineComponent({
    setup() {
      const editor = useRoomFileEditor({ topicId: () => 'room-1', path: () => 'output/报告.docx' })
      const fileHistory = useRoomFileHistory({ topicId: () => 'room-1', path: () => 'output/报告.docx' })
      return () => h(RoomFileEditor as unknown as Component, { editor, fileHistory, path: 'output/报告.docx' })
    },
  })
  return render(Host, { global: { plugins: [vuetify] } })
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
