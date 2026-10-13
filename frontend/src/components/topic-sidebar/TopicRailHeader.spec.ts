/** 项目名那颗按钮：开着的是菜单里的一页时，它就是那一页在侧栏上的位置。 */
import type { Component } from 'vue'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, render, screen } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import TopicRailHeader from './TopicRailHeader.vue'

import { setLocale } from '@/i18n'

const MENU = [
  { key: 'project-members', label: 'navigation.project.members', icon: 'mdi-account-group-outline' },
  { key: 'project-skills', label: 'navigation.project.skills', icon: 'mdi-book-cog-outline' },
]

function mount(over: { routeName: string | null; docsActive?: boolean }) {
  render(TopicRailHeader as unknown as Component, {
    props: {
      page: false,
      column: false,
      projectName: '组会',
      team: null,
      privateUnreadTotal: 0,
      searchTitle: '',
      menuOpen: false,
      menuPages: MENU,
      docsActive: false,
      terms: { project: '项目', topic: '频道' },
      projectSelected: true,
      canTransfer: false,
      canLeave: false,
      ...over,
    },
    global: { plugins: [createVuetify({ components, directives })], stubs: ['router-link'] },
  })
  return screen.getByTitle('组会')
}

beforeEach(() => setLocale('zh-CN'))
afterEach(cleanup)

describe('项目名那颗按钮', () => {
  it.each(['project-skills', 'project-members', 'project-settings'])('开着 %s 时标成当前位置', (routeName) => {
    expect(mount({ routeName }).getAttribute('aria-current')).toBe('page')
  })

  it('开着项目文档时也是', () => {
    expect(mount({ routeName: 'project-docs', docsActive: true }).getAttribute('aria-current')).toBe('page')
  })

  it.each(['workspace-overview', 'topic'])('开着侧栏上自己有一行的 %s 时不标', (routeName) => {
    expect(mount({ routeName }).getAttribute('aria-current')).toBeNull()
  })
})
