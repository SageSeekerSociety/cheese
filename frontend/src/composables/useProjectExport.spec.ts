// 「导出项目」的动作。两件事值得被盯着：
//   1. 打的是后端那条现成的打包端点（`/projects/<id>/export`），不是另造一条；
//   2. 落盘的是一份以项目名命名的 tar；名字还没读到就退回项目 id。
//
// 打包在服务端现算，期间处于 busy。成没成由 `downloadFile` 那一层弹一句（`lib/download`），
// 这里不再留一行错话，所以本文件不再有那两条失败用例。
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

  it('底下没成时也不把 busy 卡住', async () => {
    // 真实的 `downloadFile` 现在自己把失败说掉、不再往上抛，所以这一条是防它哪天
    // 又开始抛时 busy 被卡住。
    downloadFile.mockRejectedValue(new Error('导出失败：网络'))
    const { exportProject, exporting } = useProjectExport(
      () => PROJECT,
      () => '毕业设计'
    )

    await exportProject().catch(() => undefined)

    expect(exporting.value).toBe(false)
  })
})
