// 一版是在哪个房间做出来的：房间名用读者语言的引号括起来，还没起名的房间按语言叫。
import type { ArtifactVersion } from '@/api'

import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render } from '@testing-library/vue'
import { afterEach, expect, it } from 'vitest'

import ArtifactVersionList from './ArtifactVersionList.vue'

import i18n, { setLocale } from '@/i18n'

function version(room: ArtifactVersion['room']): ArtifactVersion {
  return {
    number: 1,
    card_id: 'card-1',
    subject: 'feat: first cut',
    delivered_at: '2026-09-30T08:00:00Z',
    decided_by: null,
    kind: 'file',
    filename: 'report.pdf',
    url: null,
    bytes: 10,
    room,
  }
}

async function mount(room: ArtifactVersion['room']) {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/projects/:projectId/topics/:topicId', name: 'workspace-topic', component: { template: '<div />' } },
    ],
  })
  await router.push('/projects/p1/topics/r1')
  return render(ArtifactVersionList, {
    props: { projectId: 'p1', versions: [version(room)], downloading: '' },
    global: { plugins: [router, i18n, createVuetify({ components, directives })], stubs: ['UserRef'] },
  })
}

afterEach(() => setLocale('zh-CN'))

it('in English the room is quoted the English way, and an unnamed one is New channel', async () => {
  setLocale('en')
  const named = await mount({ id: 'r1', title: 'Pricing', title_source: 'human' })
  expect(named.getByText('“Pricing”')).toBeTruthy()
  named.unmount()

  const unnamed = await mount({ id: 'r1', title: '新频道', title_source: 'placeholder' })
  expect(unnamed.getByText('“New channel”')).toBeTruthy()
})

it('in Chinese the room keeps its 《》', async () => {
  setLocale('zh-CN')
  const view = await mount({ id: 'r1', title: '定价', title_source: 'human' })
  expect(view.getByText('《定价》')).toBeTruthy()
})
