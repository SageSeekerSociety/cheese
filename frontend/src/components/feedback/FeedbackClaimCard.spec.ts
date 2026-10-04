/**
 * 「处理人」那一格：谁领着这条，以及这个读者能按哪一个。按钮只在服务端说能按时出现，
 * 按下去交给页面去发请求。
 */
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import { beforeEach, describe, expect, it } from 'vitest'

import FeedbackClaimCard from './FeedbackClaimCard.vue'

import i18n, { setLocale } from '@/i18n'

const vuetify = createVuetify({ components, directives })
const WHO = { props: ['handle'], template: '<span class="who">{{ handle }}</span>' }

function mountCard(props: { holder: string | null; canClaim: boolean; canRelease: boolean }) {
  return render(FeedbackClaimCard, {
    props: { busy: false, ...props },
    global: {
      plugins: [vuetify, i18n],
      stubs: { UserRef: WHO, UserRefLink: WHO },
    },
  })
}

beforeEach(() => {
  setLocale('zh-CN')
})

describe('处理人', () => {
  it('没人领着、读者能领：说还没人领取，按领取交给页面', async () => {
    const view = mountCard({ holder: null, canClaim: true, canRelease: false })

    expect(view.getByText('还没人领取')).toBeTruthy()
    expect(view.queryByRole('button', { name: '放弃' })).toBeNull()
    await fireEvent.click(view.getByRole('button', { name: '领取' }))
    expect(view.emitted('claim')).toHaveLength(1)
  })

  it('持有人自己看：写出是谁领着，只有放弃', async () => {
    const view = mountCard({ holder: 'ana', canClaim: false, canRelease: true })

    expect(view.container.querySelector('.who')?.textContent).toBe('ana')
    expect(view.queryByRole('button', { name: '领取' })).toBeNull()
    await fireEvent.click(view.getByRole('button', { name: '放弃' }))
    expect(view.emitted('release')).toHaveLength(1)
  })

  it('别人领着：只写出是谁，一个按钮也没有', () => {
    const view = mountCard({ holder: 'ana', canClaim: false, canRelease: false })

    expect(view.container.querySelector('.who')?.textContent).toBe('ana')
    expect(view.queryAllByRole('button')).toHaveLength(0)
  })

  it('换成英文：按钮跟着读者的语言', () => {
    setLocale('en')
    const view = mountCard({ holder: null, canClaim: true, canRelease: false })

    expect(view.getByRole('button', { name: 'Claim' })).toBeTruthy()
    expect(view.getByText('Nobody has claimed it yet')).toBeTruthy()
  })
})
