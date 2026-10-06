// 房间里右侧那几格挂的是不是产品里那几件。`DemoView.spec.ts` 走的是剧本那条路
// （改动那一格），这里补上预览：没有哪张剧本声明过它，所以拿一份合成的帧来挂，
// 免得这一段接线没人走过。
import type { Component } from 'vue'
import type { Scene } from './demoScene'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render, waitFor } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { installDemoBackend } from './demoBackend'
import DemoRoom from './DemoRoom.vue'
import { demoRouter } from './demoRouter'
import { frameAt } from './demoScene'

import i18n, { setLocale } from '@/i18n'

const Room = DemoRoom as unknown as Component

const SCENE: Scene = {
  title: '合成的一间房',
  project: '知是',
  topic: '话题',
  machine: '本话题运行在：云机器 cm-0000',
  people: { wang: { name: '王长鑫' }, cheese: { name: '芝士', agent: true } },
  steps: [
    {
      label: '摆出一份文件',
      panel: 'preview',
      preview: { path: '报告.md', content: '# 报告\n\n写好了。' },
      events: [{ at: 200, do: 'say', who: 'cheese', text: '报告写好了。' }],
    },
  ],
}

describe('DemoRoom', () => {
  let original: typeof fetch

  beforeEach(() => {
    setLocale('zh-CN')
    original = globalThis.fetch
    globalThis.fetch = vi.fn() as unknown as typeof fetch
    // 真组件自己去接口取数，演示页没有后端：装上面那层假的（和 DemoView 里一样）。
    installDemoBackend()
  })
  afterEach(() => {
    globalThis.fetch = original
    vi.restoreAllMocks()
  })

  it('mounts the product panel a frame asks for, fed from the scene', async () => {
    const view = render(Room, {
      props: { scene: SCENE, frame: frameAt(SCENE, 0, 0) },
      global: { plugins: [createVuetify({ components, directives }), createPinia(), demoRouter(), i18n] },
    })
    const selected = () =>
      view.container.querySelector('[data-region="tabs"] [role="tab"][aria-selected="true"]')?.textContent?.trim()
    await waitFor(() => expect(selected()).toContain('预览'))
    // 预览那一格是产品自己的组件，正文来自剧本里那一份。
    await waitFor(() => expect(view.container.querySelector('.panel-preview')).toBeTruthy())
    await waitFor(() => expect(view.container.textContent).toContain('写好了。'))
    // 现场还挂着（切走的那几格不卸），只是藏着。
    expect(view.container.querySelector<HTMLElement>('.panel-site')?.style.display).toBe('none')
  })

  // 总览那一格从「面板自己去取数」改成「接线外壳去取数」之后，演示页有一阵子是空的：
  // 外壳是产品里的新一层，剧本喂的还是老地方的 props。这一份钉着它真的接上了。
  it('总览那一格也是产品自己的，剧本里的活经演示后端喂到它手上', async () => {
    const scene: Scene = {
      ...SCENE,
      steps: [
        {
          label: '摆出总览',
          panel: 'overview',
          overview: { tasks: [{ id: 't1', title: '分页接口', who: 'cheese', column: 'building' }] },
          events: [],
        },
      ],
    }
    const view = render(Room, {
      props: { scene, frame: frameAt(scene, 0, 0) },
      global: { plugins: [createVuetify({ components, directives }), createPinia(), demoRouter(), i18n] },
    })

    await waitFor(() =>
      expect(
        view.container.querySelector('[data-region="tabs"] [role="tab"][aria-selected="true"]')?.textContent
      ).toContain('总览')
    )
    await waitFor(() => expect(view.container.querySelector('.panel-overview')).toBeTruthy())
    // 看板默认折着，先看那一行摘要：它上面的「1 件」只可能来自演示后端答回来的那条活。
    const head = () => view.container.querySelector<HTMLElement>('.task-progress__head')
    await waitFor(() => expect(head()?.textContent).toContain('1 件'))
    head()!.click()
    await waitFor(() => expect(view.container.textContent).toContain('分页接口'))
  })
})
