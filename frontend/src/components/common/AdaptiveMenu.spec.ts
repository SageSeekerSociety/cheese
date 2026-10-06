// 手机上一组操作从底部面板里选：点一项就执行它、面板关上；点不了的那一项点了也
// 不执行。桌面上同一份清单是一个下拉菜单，选中同样执行。
import type { MenuAction } from './menuAction'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest'

import { useRowMenu } from '@/composables/useRowMenu'

import AdaptiveMenu from './AdaptiveMenu.vue'

beforeAll(() => {
  if (!('ResizeObserver' in globalThis)) {
    ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
  // 面板和菜单都是 VOverlay，happy-dom 没有 visualViewport，不补上浮层挂不起来。
  vi.stubGlobal('visualViewport', {
    width: 390,
    height: 844,
    offsetLeft: 0,
    offsetTop: 0,
    addEventListener() {},
    removeEventListener() {},
  })
  // 桌面菜单的定位要读它。
  vi.stubGlobal('devicePixelRatio', 1)
  // happy-dom 没有它，而弹在鼠标那一点上的菜单定位时要读（缺了会在浮层自己的 effect 里
  // 抛出来，把那一拍的更新队列打断）。
  if (!document.elementFromPoint) document.elementFromPoint = () => null
})
afterAll(() => vi.unstubAllGlobals())

afterEach(() => {
  document.body.innerHTML = ''
})

function mount(width: number, actions: MenuAction[]) {
  ;(window as unknown as { innerWidth: number }).innerWidth = width
  const vuetify = createVuetify({ components, directives })
  const Host = {
    components: { AdaptiveMenu },
    props: ['actions'],
    template: `
      <v-app>
        <AdaptiveMenu :actions="actions" title="话题">
          <template #activator="{ props }">
            <button type="button" v-bind="props">更多</button>
          </template>
        </AdaptiveMenu>
      </v-app>`,
  }
  return render(Host, { props: { actions }, global: { plugins: [vuetify] } })
}

describe('AdaptiveMenu', () => {
  it('手机上：点一项就执行它，面板关上', async () => {
    const rename = vi.fn()
    mount(390, [
      { key: 'rename', label: '重命名', icon: 'mdi-pencil', onSelect: rename },
      { key: 'archive', label: '归档', icon: 'mdi-archive', onSelect: vi.fn() },
    ])
    await fireEvent.click(screen.getByText('更多'))
    await fireEvent.click(await screen.findByRole('menuitem', { name: '重命名' }))
    expect(rename).toHaveBeenCalledTimes(1)
    await waitFor(() => expect(screen.queryByRole('menuitem', { name: '重命名' })).toBeNull())
  })

  it('手机上：点不了的那一项不执行', async () => {
    const remove = vi.fn()
    mount(390, [{ key: 'remove', label: '删除', icon: 'mdi-delete', danger: true, disabled: true, onSelect: remove }])
    await fireEvent.click(screen.getByText('更多'))
    await fireEvent.click(await screen.findByRole('menuitem', { name: '删除' }))
    expect(remove).not.toHaveBeenCalled()
  })

  it('桌面上：同一份清单是下拉菜单，选中执行', async () => {
    const rename = vi.fn()
    mount(1280, [{ key: 'rename', label: '重命名', icon: 'mdi-pencil', onSelect: rename }])
    await fireEvent.click(screen.getByText('更多'))
    await fireEvent.click(await screen.findByText('重命名'))
    expect(rename).toHaveBeenCalledTimes(1)
  })

  it('桌面上：在第二处右键，第一处的菜单收起来', async () => {
    ;(window as unknown as { innerWidth: number }).innerWidth = 1280
    const vuetify = createVuetify({ components, directives })
    // 两处各有一份右键菜单，像消息、日程、通知那样：每处自己记着自己那份开没开。
    const Host = {
      components: { AdaptiveMenu },
      setup() {
        const first = useRowMenu<string>()
        const second = useRowMenu<string>()
        const actions = (label: string): MenuAction[] => [{ key: 'open', label, icon: 'mdi-open-in-new' }]
        return { first, second, actions }
      },
      template: `
        <v-app>
          <span @contextmenu="first.open('a', $event)">第一处</span>
          <span @contextmenu="second.open('b', $event)">第二处</span>
          <AdaptiveMenu v-bind="first.bind('a')" :actions="actions('第一处的操作')">
            <template #activator />
          </AdaptiveMenu>
          <AdaptiveMenu v-bind="second.bind('b')" :actions="actions('第二处的操作')">
            <template #activator />
          </AdaptiveMenu>
        </v-app>`,
    }
    render(Host, { global: { plugins: [vuetify] } })

    // 露在外面的菜单就是「还开着」的那些：关掉的那一份 transition 过后才离开 DOM（测试
    // 环境里 transition 是桩，走不掉），所以照用户看得到的问，而不是照 DOM 里有没有问。
    const showing = () =>
      Array.from(document.querySelectorAll('.v-overlay--active .v-list-item-title')).map((el) => el.textContent?.trim())

    await fireEvent.contextMenu(screen.getByText('第一处'), { clientX: 40, clientY: 40 })
    await waitFor(() => expect(showing()).toEqual(['第一处的操作']))

    await fireEvent.contextMenu(screen.getByText('第二处'), { clientX: 40, clientY: 120 })
    await waitFor(() => expect(showing()).toEqual(['第二处的操作']))
  })
})
