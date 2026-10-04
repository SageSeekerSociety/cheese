// 「导出项目」—— 项目设置里的一节。三件事值得被盯着：
//   1. 按下去打的是后端那条现成的打包端点（`/projects/<id>/export`），不是另造一条；
//   2. 打包在服务端现算，等的时候按钮要进 loading 并禁用，不能连点发两遍；
//   3. 失败了把那句话摆在这一块里，不动整页。
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const downloadFile = vi.fn()

vi.mock('@/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api')>()
  return {
    ...actual,
    downloadFile: (...a: unknown[]) => downloadFile(...a),
  }
})

import ProjectExportSection from './ProjectExportSection.vue'

import { projectExportUrl } from '@/api'
import { setLocale } from '@/i18n'

const PROJECT = 'de808b13-ffd2-4b8a-9d1d-fba7babe389f'

function mountSection(props: { projectName?: string } = {}) {
  return render(ProjectExportSection, {
    props: { projectId: PROJECT, projectName: props.projectName ?? '毕业设计' },
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
  if (!globalThis.matchMedia) {
    globalThis.matchMedia = (() => ({
      matches: false,
      addEventListener() {},
      removeEventListener() {},
      addListener() {},
      removeListener() {},
      dispatchEvent: () => false,
    })) as unknown as typeof globalThis.matchMedia
  }
})

afterEach(() => {
  cleanup()
  downloadFile.mockReset()
})

beforeEach(() => {
  setLocale('zh-CN')
})

describe('「导出项目」那一块', () => {
  it('按下去下的是后端那条打包端点，落盘的是一份以项目名命名的 tar', async () => {
    downloadFile.mockResolvedValue(undefined)
    mountSection()

    await fireEvent.click(exportButton())

    await waitFor(() => expect(downloadFile).toHaveBeenCalledWith(projectExportUrl(PROJECT), '毕业设计.tar'))
  })

  it('项目还没读到名字时退回用项目 id 当文件名', async () => {
    downloadFile.mockResolvedValue(undefined)
    mountSection({ projectName: '' })

    await fireEvent.click(exportButton())

    await waitFor(() => expect(downloadFile).toHaveBeenCalledWith(projectExportUrl(PROJECT), `${PROJECT}.tar`))
  })

  it('打包期间按钮禁用，结束才恢复', async () => {
    let release: () => void = () => {}
    downloadFile.mockReturnValue(new Promise<void>((resolve) => (release = resolve)))
    mountSection()

    const button = exportButton()
    expect(button.disabled).toBe(false)
    await fireEvent.click(button)

    await waitFor(() => expect(button.disabled).toBe(true))
    release()
    await waitFor(() => expect(button.disabled).toBe(false))
  })

  it('失败时把那句话摆在这一块里，按钮还能再按一次', async () => {
    downloadFile.mockRejectedValue(new Error('导出失败：网络'))
    const { container } = mountSection()

    await fireEvent.click(exportButton())

    await waitFor(() => expect(container.textContent).toContain('导出失败：网络'))
    expect(exportButton().disabled).toBe(false)
  })

  it('抛出非 Error 时兜一句通用的失败话术', async () => {
    downloadFile.mockRejectedValue('nope')
    const { container } = mountSection()

    await fireEvent.click(exportButton())

    await waitFor(() => expect(container.textContent).toContain('导出失败'))
  })
})
