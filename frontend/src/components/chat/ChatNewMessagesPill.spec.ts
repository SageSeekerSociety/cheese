// 这颗药丸什么时候露面：**只要读者不在底部**就露面。往上翻着又来了新消息时它
// 报条数；只是往上翻看历史时它写「回到最新」——不再是一颗要等新消息才出现的按钮。
// 底部不露面：最新本来就在眼前，一颗挡着时间线的按钮没有理由。
import type { Component } from 'vue'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import { beforeEach, describe, expect, it } from 'vitest'

import ChatNewMessagesPill from './ChatNewMessagesPill.vue'

import i18n, { setLocale, t } from '@/i18n'

function mount(props: { count: number; hasNewer: boolean; atBottom: boolean }) {
  return render(ChatNewMessagesPill as unknown as Component, {
    props,
    global: { plugins: [createVuetify({ components, directives }), i18n] },
  })
}

const pill = (container: Element) => container.querySelector('.new-pill')

beforeEach(() => {
  // These assertions read the Chinese copy.
  setLocale('zh-CN')
})

describe('回到最新的入口', () => {
  it('停在底部：没有入口——最新本来就在眼前', () => {
    const { container } = mount({ count: 0, hasNewer: false, atBottom: true })
    expect(pill(container)).toBeNull()
  })

  it('只是往上翻了翻、没有新消息：露出「回到最新」', () => {
    const { container } = mount({ count: 0, hasNewer: false, atBottom: false })
    expect(pill(container)?.textContent).toContain(t('work.room.backToLatest'))
  })

  it('翻上去了又来了新的：报条数', () => {
    const { container } = mount({ count: 3, hasNewer: false, atBottom: false })
    expect(pill(container)?.textContent).toContain(t('work.room.newMessages'))
  })

  it('停在历史中间（底下还有更新的）：写「回到最新」，不写条数', () => {
    const { container } = mount({ count: 0, hasNewer: true, atBottom: false })
    expect(pill(container)?.textContent).toContain(t('work.room.backToLatest'))
    expect(pill(container)?.textContent).not.toContain(t('work.room.newMessages'))
  })

  it('点它往上发 jump，去哪儿由上面决定', async () => {
    const { container, emitted } = mount({ count: 0, hasNewer: false, atBottom: false })
    await fireEvent.click(pill(container)!)
    expect(emitted().jump).toHaveLength(1)
  })

  it('底上就这一颗按钮，桌面上不留第二个入口', () => {
    const { container } = mount({ count: 0, hasNewer: false, atBottom: false })
    expect(container.querySelectorAll('button')).toHaveLength(1)
  })
})
