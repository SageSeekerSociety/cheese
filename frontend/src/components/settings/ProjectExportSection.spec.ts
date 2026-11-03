// 「导出项目」那一块。它是哑的：只从 props 画状态、只往外 emit `export`（发请求与
// 状态在 composables/useProjectExport，另有一份 spec）。这里钉三件事：
//   1. 画出这一块的标题、说明和那颗按钮；
//   2. 点下去把 `export` 抛给页面；
//   3. busy 时按钮禁用（打包期间不许连点）。
//
// 失败不再画在这里：成没成由 `downloadFile` 那一层弹一句（`lib/download`），所以这一块
// 没有可画的失败话术。
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import ProjectExportSection from './ProjectExportSection.vue'

import { setLocale } from '@/i18n'

function mountSection(props: { busy?: boolean; onExport?: () => void } = {}) {
  return render(ProjectExportSection, {
    props: { busy: props.busy ?? false, onExport: props.onExport },
    global: { plugins: [createVuetify({ components, directives })] },
  })
}

const exportButton = () => screen.getByRole('button', { name: '导出项目' }) as HTMLButtonElement

beforeAll(() => {
  if (!('ResizeObserver' in globalThis)) {
    ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
})

beforeEach(() => {
  setLocale('zh-CN')
})

afterEach(cleanup)

describe('「导出项目」那一块', () => {
  it('画出标题、说明和按钮', () => {
    const { container } = mountSection()
    expect(container.textContent).toContain('导出项目')
    expect(container.textContent).toContain('打包整个项目')
    expect(exportButton()).toBeTruthy()
  })

  it('点下去把 export 抛给页面', async () => {
    const onExport = vi.fn()
    mountSection({ onExport })

    await fireEvent.click(exportButton())

    expect(onExport).toHaveBeenCalledOnce()
  })

  it('busy 时按钮禁用', () => {
    mountSection({ busy: true })
    expect(exportButton().disabled).toBe(true)
  })
})
