// 「导出项目」的动作。三件事值得被盯着：
//   1. 打的是后端那条现成的打包端点（`/projects/<id>/export`），不是另造一条；
//   2. 落盘的是一份以项目名命名的 tar；名字还没读到就退回项目 id；
//   3. 打包在服务端现算，期间处于 busy；失败把那句话留着，busy 归位。
import { beforeEach, describe, expect, it, vi } from 'vitest'

const downloadFile = vi.fn()

vi.mock('@/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api')>()
  return {
    ...actual,
    downloadFile: (...a: unknown[]) => downloadFile(...a),
  }
})

import { useProjectExport } from './useProjectExport'

import { setLocale } from '@/i18n'
import { projectExportUrl } from '@/lib/projectExport'

const PROJECT = 'de808b13-ffd2-4b8a-9d1d-fba7babe389f'

beforeEach(() => {
  downloadFile.mockReset()
  setLocale('zh-CN')
})

describe('useProjectExport', () => {
  it('打的是打包端点，落盘的是一份以项目名命名的 tar', async () => {
    downloadFile.mockResolvedValue(undefined)
    const { exportProject } = useProjectExport(
      () => PROJECT,
      () => '毕业设计'
    )

    await exportProject()

    expect(downloadFile).toHaveBeenCalledWith(projectExportUrl(PROJECT), '毕业设计.tar')
  })

  it('项目还没读到名字时退回用项目 id 当文件名', async () => {
    downloadFile.mockResolvedValue(undefined)
    const { exportProject } = useProjectExport(
      () => PROJECT,
      () => ''
    )

    await exportProject()

    expect(downloadFile).toHaveBeenCalledWith(projectExportUrl(PROJECT), `${PROJECT}.tar`)
  })

  it('打包期间 busy，结束归位', async () => {
    let release: () => void = () => {}
    downloadFile.mockReturnValue(new Promise<void>((resolve) => (release = resolve)))
    const { exportProject, exporting } = useProjectExport(
      () => PROJECT,
      () => '毕业设计'
    )

    const done = exportProject()
    expect(exporting.value).toBe(true)

    release()
    await done
    expect(exporting.value).toBe(false)
  })

  it('失败时留下抛出那句话', async () => {
    downloadFile.mockRejectedValue(new Error('导出失败：网络'))
    const { exportProject, exportError, exporting } = useProjectExport(
      () => PROJECT,
      () => '毕业设计'
    )

    await exportProject()

    expect(exportError.value).toBe('导出失败：网络')
    expect(exporting.value).toBe(false)
  })

  it('抛出非 Error 时兜一句通用的失败话术', async () => {
    downloadFile.mockRejectedValue('nope')
    const { exportProject, exportError } = useProjectExport(
      () => PROJECT,
      () => '毕业设计'
    )

    await exportProject()

    expect(exportError.value).toBe('导出失败')
  })
})
