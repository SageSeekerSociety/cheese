/** 任务的现场里，频道主线转达给任务的话和任务向主线的报告各占一行：谁说的、往哪边说，
 *  下面是那句话本身。 */
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render, screen } from '@testing-library/vue'
import { beforeEach, describe, expect, it } from 'vitest'

import SiteRecordRow from './SiteRecordRow.vue'

import i18n, { setLocale, t } from '@/i18n'

function mount(meta: Record<string, unknown>, content: string) {
  return render(SiteRecordRow, {
    props: {
      block: {
        id: 'd1',
        kind: 'event',
        author_type: 'platform',
        author: 'cheese-main',
        created_at: '2026-11-17T06:00:00Z',
        content,
        meta: { cursor: 'c', to: 'cheese-task', ...meta },
      },
      raw: false,
      loadImage: async () => '',
      memberNames: { 'cheese-main': '芝士' },
    } as never,
    global: { plugins: [createVuetify({ components, directives }), i18n] },
  })
}

beforeEach(() => setLocale('zh-CN'))

describe('现场里主线和任务之间的往来', () => {
  it('主线转达的话写明是谁在主线转达的', () => {
    mount({ exchange: 'told' }, '按钮改成蓝色')
    expect(screen.getByText(t('work.room.site.exchange.told', { name: '芝士' }))).toBeTruthy()
    expect(screen.getByText('按钮改成蓝色')).toBeTruthy()
  })

  it('任务的提问写明是向主线提的', () => {
    mount({ exchange: 'report', kind: 'question' }, '蓝色用哪一个色号？')
    expect(screen.getByText(t('work.room.site.exchange.asked', { name: '芝士' }))).toBeTruthy()
    expect(screen.getByText('蓝色用哪一个色号？')).toBeTruthy()
  })
})
