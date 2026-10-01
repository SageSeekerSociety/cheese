// 选团队领题：领不了的团队要说清为什么，说的是中文，不是后端的英文日志句。
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeAll, describe, expect, it } from 'vitest'

import TeamSelectionDialog from './TeamSelectionDialog.vue'

import i18n, { setLocale } from '@/i18n'

const ENGLISH = 'A member of this team has already claimed this task through another team.'

function team(id: number, eligible: boolean, code?: string) {
  return {
    team: { id, name: `队伍${id}`, intro: '', avatarId: null },
    eligibility: { eligible, reasons: code ? [{ code, message: ENGLISH }] : [] },
  }
}

describe('选团队领题', () => {
  beforeAll(() => {
    if (!('ResizeObserver' in globalThis)) {
      ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
        observe() {}
        unobserve() {}
        disconnect() {}
      }
    }
    if (!globalThis.visualViewport) {
      ;(globalThis as unknown as { visualViewport: unknown }).visualViewport = {
        width: 1024,
        height: 768,
        offsetLeft: 0,
        offsetTop: 0,
        scale: 1,
        addEventListener() {},
        removeEventListener() {},
        dispatchEvent: () => false,
      }
    }
  })

  afterEach(() => cleanup())

  it('队里有人已经用别的团队领了：说中文的原因，不露后端的英文', async () => {
    setLocale('zh-CN')
    render(TeamSelectionDialog, {
      props: {
        open: true,
        taskData: { id: 1, name: '题', requireRealName: false } as never,
        availableTeams: [team(1, true), team(2, false, 'MEMBER_ALREADY_PARTICIPATING')] as never,
        loading: false,
      },
      global: { plugins: [createVuetify({ components, directives }), i18n] },
    })

    await waitFor(() => expect(document.body.textContent).toContain('队伍2'))
    expect(document.body.textContent).not.toContain(ENGLISH)
    expect(document.body.textContent).toContain(i18n.global.t('tasks.eligibility.MEMBER_ALREADY_PARTICIPATING'))
  })
})
