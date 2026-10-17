/// <reference types="node" />
// 「邀请谁来回答」弹窗里那份名单的画面。这一件只吃 props、只发事件：名单从哪来、
// 「已经邀请过谁」怎么比出来、点下去发哪趟请求，全是页面那一层的事
// （`composables/useQuestionInvitations.spec.ts` 钉那些）。这里钉画面本身：
//
//   1. **一行一个人**，谁在名单里就画谁。
//   2. **已经邀请过的人，那颗按钮当场就是「已邀请」**，而且按不动。推荐名单是全量
//      档案（后端 `get_recommendations` 不过滤谁被邀请过），所以「点没点过」只能靠
//      外面那份 id 比对出来；没比对上就会把已经邀请过的人再画成可点一次。
//   3. **点得动的那一颗，把点的人原样交上去**——发不发请求不归它管。
import type { User } from '@/types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render } from '@testing-library/vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

// 只换掉 `useI18n`，其余照旧：这一件不碰接口，替掉它只是为了下面照 key 断言文案。
vi.mock('vue-i18n', async () => {
  const actual = await vi.importActual<typeof import('vue-i18n')>('vue-i18n')
  return { ...actual, useI18n: () => ({ t: (key: string) => key }) }
})

import InvitationList from '../InvitationList.vue'

const INVITE = 'questions.invitationList.buttons.invite'
const INVITED = 'questions.invitationList.buttons.invited'

const FULU: User = {
  id: 7,
  username: 'fulu',
  nickname: '福禄',
  avatarId: 3,
  intro: '在做题',
  question_count: 0,
  answer_count: 0,
}
const ZHISHI: User = { ...FULU, id: 9, username: 'zhishi', nickname: '芝士', intro: '在攒题' }

function mountList(props: { people?: User[]; invitedIds?: number[] } = {}) {
  return render(InvitationList, {
    props: { people: [FULU, ZHISHI], invitedIds: [], ...props },
    global: { plugins: [createVuetify({ components, directives })] },
  })
}

afterEach(cleanup)

describe('the invitation list', () => {
  it('lists everyone in the roster, one row each', () => {
    const { container } = mountList()

    expect(container.textContent).toContain('福禄')
    expect(container.textContent).toContain('在做题')
    expect(container.textContent).toContain('芝士')
    expect(container.querySelectorAll('button')).toHaveLength(2)
  })

  it('shows someone already invited as invited, not as someone to invite again', () => {
    const { container } = mountList({ invitedIds: [FULU.id] })

    // 两颗按钮：福禄那颗说「已邀请」、而且是按不动的——这个人不必再被邀请一次。
    const buttons = [...container.querySelectorAll('button')]
    expect(buttons[0].textContent).toContain(INVITED)
    expect(buttons[0].hasAttribute('disabled')).toBe(true)
    // 不在那份 id 里的照旧可点。
    expect(buttons[1].textContent).toContain(INVITE)
    expect(buttons[1].hasAttribute('disabled')).toBe(false)
  })

  it('hands the person that was clicked back to whoever owns the request', async () => {
    const { container, emitted } = mountList()

    await fireEvent.click(container.querySelectorAll('button')[1])

    // 交上去的是那个人本身，不是他的 id 也不是这一行——发请求的那一层要用它。
    expect(emitted()['invite']).toEqual([[ZHISHI]])
  })
})
