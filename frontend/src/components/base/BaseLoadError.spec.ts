/** 读失败的共同长相：这块内容不在了，换成一句话加一条重试的路。这里锁三件：
 *  出错原因有就照原样显示、没有就不显示那一行、点重试只发一次事件。 */
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import BaseLoadError from './BaseLoadError.vue'

import { setLocale } from '@/i18n'

beforeEach(() => setLocale('en'))
afterEach(cleanup)

function mount(props: Record<string, unknown> = {}) {
  return render(BaseLoadError, {
    props,
    global: { plugins: [createVuetify({ components, directives })] },
  })
}

describe('BaseLoadError', () => {
  it('shows the given title and the server reason', async () => {
    mount({ title: "Couldn't load overview", error: 'HTTP 503 for /overview' })
    expect(await screen.findByText("Couldn't load overview")).toBeTruthy()
    expect(screen.getByText('HTTP 503 for /overview')).toBeTruthy()
  })

  it('falls back to the global title and hides a blank reason', async () => {
    const view = mount({ error: '   ' })
    expect(await screen.findByText("Couldn't load")).toBeTruthy()
    // 只有空白的 text 不画出来：这一块里就只剩标题那一个字串。
    expect(view.container.querySelector('.v-alert__content')?.textContent?.trim()).toBe("Couldn't load")
  })

  it('emits retry once when the button is clicked', async () => {
    const view = mount({ title: "Couldn't load overview" })
    await fireEvent.click(await screen.findByRole('button', { name: 'Try again' }))
    expect(view.emitted('retry')).toHaveLength(1)
  })

  it('says no access and offers no retry when forbidden', async () => {
    // 401/403：说没权限，不给重试——重试一次还是同一个 401。
    mount({ title: "Couldn't load overview", forbidden: true })
    expect(await screen.findByText("You don't have access to this")).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Try again' })).toBeNull()
    expect(screen.queryByText("Couldn't load overview")).toBeNull()
  })

  it('still shows the server reason under the no-access title', async () => {
    mount({ forbidden: true, error: 'HTTP 403 for /library' })
    expect(await screen.findByText("You don't have access to this")).toBeTruthy()
    expect(screen.getByText('HTTP 403 for /library')).toBeTruthy()
  })
})
