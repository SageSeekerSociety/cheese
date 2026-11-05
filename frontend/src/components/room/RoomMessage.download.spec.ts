/** 芝士摆出来的一份文件（`cheese show`、`SendUserFile`）在对话里也能直接下载。
 *
 * 附件一直有那一颗下载按钮，摆出来的东西没有：交付的 zip 要先「保存到资料库」再
 * 去资料库下载，绕一圈。这一份钉住「摆出来的真文件有下载、只有一句说明的应用和
 * MCP 视图没有」。
 */
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import { beforeEach, describe, expect, it } from 'vitest'

import RoomMessage from './RoomMessage.vue'

import i18n, { setLocale, t } from '@/i18n'

function mount(block: Record<string, unknown>) {
  return render(RoomMessage, {
    props: {
      block: { id: 'b1', author: 'cheese-t1', created_at: '', ...block },
      parent: null,
      parentName: null,
      runStart: true,
      mine: false,
      topicId: 't1',
      authorName: '芝士',
      avatar: null,
      isAgent: true,
      time: '10:00',
      refs: { mentionNames: {}, topicTitles: {} },
      viewer: 'alice',
    } as never,
    global: { plugins: [createVuetify({ components, directives }), i18n] },
  })
}

beforeEach(() => setLocale('zh-CN'))

describe('芝士摆出来的文件能下载', () => {
  it('认不出后缀的交付物（zip）有下载，点了往上发 download', async () => {
    const view = mount({ kind: 'artifact', content: 'Obsidian-Notes.zip', mime_type: 'text/html' })

    const title = t('work.room.message.downloadFile', { name: 'Obsidian-Notes.zip' })
    const button = view.container.querySelector<HTMLElement>(`[title="${title}"]`)
    expect(button, '摆出来的 zip 应当有一颗下载').not.toBeNull()
    await fireEvent.click(button!)
    expect(view.emitted().download).toHaveLength(1)
  })

  it('正在跑的应用只有一句说明，没有下载', () => {
    const view = mount({
      kind: 'artifact',
      content: 'Vue dev server',
      mime_type: 'application/x-cheesex-app',
    })
    expect(view.container.querySelector('.im-file [title^="下载"]')).toBeNull()
  })
})
