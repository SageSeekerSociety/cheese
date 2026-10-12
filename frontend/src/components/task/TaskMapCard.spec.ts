/**
 * 任务的卡片：没内容的栏不画；还没写过时不画任何一栏；只有能改的人改得了，改完交出去
 * 的是他写的那几栏。
 */
import type { MapCardView } from '@/types/taskMapCard'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import { describe, expect, it } from 'vitest'

import TaskMapCard from './TaskMapCard.vue'

import { setLocale } from '@/i18n'

setLocale('zh-CN')
const vuetify = createVuetify({ components, directives })

function view(over: Partial<MapCardView> = {}): MapCardView {
  return {
    card: {
      what: '丢东西的人点「是我的」，就能联系到捡到的人',
      progress: '',
      waiting_on: [{ handle: 'bob', ask: '看一下表结构' }],
      terms: [],
      updated_by: 'cheese-a',
      updated_at: '2026-10-11T08:00:00Z',
    },
    can_edit: false,
    ...over,
  }
}

function show(v: MapCardView | null) {
  return render(TaskMapCard, {
    props: {
      view: v,
      memberNames: { bob: '波比' },
      people: [{ handle: 'bob', name: '波比' }],
      saving: false,
      error: null,
    },
    global: { plugins: [vuetify] },
  })
}

describe('TaskMapCard', () => {
  it('shows the fields that have something and leaves out the empty ones', () => {
    const page = show(view())
    expect(page.getByTestId('map-card-what').textContent).toContain('丢东西的人点')
    expect(page.getByTestId('map-card-waiting').textContent).toContain('波比')
    expect(page.queryByTestId('map-card-progress')).toBeNull()
    expect(page.queryByTestId('map-card-terms')).toBeNull()
  })

  it('shows none of the fields before anyone has written the card', () => {
    const page = show(view({ card: null }))
    expect(page.queryByTestId('map-card-what')).toBeNull()
    expect(page.queryByTestId('map-card-waiting')).toBeNull()
  })

  it('offers no editing to someone who may not change the card', () => {
    const page = show(view({ can_edit: false }))
    expect(page.queryByTestId('map-card-edit')).toBeNull()
  })

  it('hands over what the person wrote when they save', async () => {
    const page = show(view({ can_edit: true }))
    await fireEvent.click(page.getByTestId('map-card-edit'))
    const [what] = page.getByTestId('map-card-form').querySelectorAll('input')
    await fireEvent.update(what as HTMLInputElement, '失主能联系上捡到的人')
    await fireEvent.click(page.getByTestId('map-card-save'))

    const saved = page.emitted('save') as unknown as [Record<string, unknown>][]
    expect(saved).toHaveLength(1)
    expect(saved[0][0]).toMatchObject({
      what: '失主能联系上捡到的人',
      waiting_on: [{ handle: 'bob', ask: '看一下表结构' }],
    })
  })
})
