// 「说明」页签的视频：只嵌 B 站；别的平台给一条链接；不是 https 的连链接都不给。
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, render } from '@testing-library/vue'
import { afterEach, describe, expect, it } from 'vitest'

import BriefView from './BriefView.vue'

import i18n from '@/i18n'

function mount(videoUrl: string) {
  return render(BriefView, {
    props: {
      taskData: { id: 42, description: '', videoUrl } as never,
      attachments: [],
      canDownload: false,
      downloadingId: null,
    },
    global: { plugins: [createVuetify({ components, directives }), i18n] },
  })
}

describe('说明页签的视频', () => {
  afterEach(() => cleanup())

  it('B 站链接嵌播放器', () => {
    const { container } = mount('https://www.bilibili.com/video/BV1xx411c7mD')

    expect(container.querySelector('iframe')?.getAttribute('src')).toBe(
      '//player.bilibili.com/player.html?bvid=BV1xx411c7mD&autoplay=0'
    )
  })

  it('别的平台只给一条链接，不假装能嵌', () => {
    const { container } = mount('https://example.com/lesson.mp4')

    expect(container.querySelector('iframe')).toBeNull()
    expect(container.querySelector('a')?.getAttribute('href')).toBe('https://example.com/lesson.mp4')
  })

  it('不是 https 的链接连链接都不给', () => {
    const { container } = mount('http://example.com/lesson.mp4')

    expect(container.querySelector('iframe')).toBeNull()
    expect(container.querySelector('a')).toBeNull()
  })
})
