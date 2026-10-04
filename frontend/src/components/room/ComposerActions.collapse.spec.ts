// 手机上输入框下面那一行放不下每一颗：清单、提醒收进一颗「更多操作」，从里面
// 选一项和直接点那一颗是同一件事。只剩一样时不收。桌面上两颗照旧摆在外面。
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, screen } from '@testing-library/vue'
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest'

import ComposerActions from './ComposerActions.vue'

import { setLocale } from '@/i18n'

beforeAll(() => {
  setLocale('zh-CN')
  if (!('ResizeObserver' in globalThis)) {
    ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
  // 底部面板是 VOverlay，happy-dom 没有 visualViewport，不补上浮层挂不起来。
  vi.stubGlobal('visualViewport', {
    width: 375,
    height: 812,
    offsetLeft: 0,
    offsetTop: 0,
    addEventListener() {},
    removeEventListener() {},
  })
})
afterAll(() => vi.unstubAllGlobals())
afterEach(() => {
  document.body.innerHTML = ''
})

function mount(width: number, extras: { canChecklist?: boolean; canRemind?: boolean }) {
  ;(window as unknown as { innerWidth: number }).innerWidth = width
  return render(
    {
      components: { ComposerActions },
      props: ['extras', 'collapse'],
      emits: ['checklist', 'remind'],
      template: `
        <v-app>
          <ComposerActions
            :uploading="false"
            :can-send="true"
            :show-image-picker="collapse"
            :enter-sends="true"
            :always-summon="false"
            :summon-on="false"
            :summon-ready="true"
            agent-name="芝士"
            v-bind="extras"
            :collapse-extras="collapse"
            @checklist="$emit('checklist')"
            @remind="$emit('remind')"
          />
        </v-app>`,
    },
    {
      props: { extras, collapse: width < 960 },
      global: { plugins: [createVuetify({ components, directives })] },
    }
  )
}

describe('输入框下面那一行', () => {
  it('手机上：清单、提醒收进「更多操作」，从里面点「发清单」就是发清单', async () => {
    const { emitted } = mount(375, { canChecklist: true, canRemind: true })
    expect(screen.queryByLabelText('发清单')).toBeNull()

    await fireEvent.click(screen.getByLabelText('更多操作'))
    const items = (await screen.findAllByRole('menuitem')).map((el) => el.textContent?.trim())
    expect(items).toEqual(['发清单', '提醒我', '键盘快捷键（?）'])
    await fireEvent.click(screen.getByRole('menuitem', { name: '发清单' }))

    expect(emitted().checklist).toHaveLength(1)
    expect(emitted().remind).toBeUndefined()
  })

  it('手机上只剩一样时不收：那一颗直接摆在外面', async () => {
    const { emitted } = mount(375, { canRemind: true })
    expect(screen.queryByLabelText('更多操作')).toBeNull()
    await fireEvent.click(screen.getByLabelText('提醒我'))
    expect(emitted().remind).toHaveLength(1)
  })

  it('桌面上两颗都在外面，没有「更多操作」', () => {
    mount(1280, { canChecklist: true, canRemind: true })
    expect(screen.queryByLabelText('更多操作')).toBeNull()
    screen.getByLabelText('发清单')
    screen.getByLabelText('提醒我')
  })
})
