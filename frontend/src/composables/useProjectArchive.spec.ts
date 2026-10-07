// 「归档项目」的动作：打的是这个项目的归档接口；成了才收尾（刷清单、离开）；
// 被拒时理由留着、busy 归位、不收尾。
import { beforeEach, describe, expect, it, vi } from 'vitest'

const archiveProject = vi.fn()

vi.mock('@/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api')>()
  return { ...actual, archiveProject: (...a: unknown[]) => archiveProject(...a) }
})

import { useProjectArchive } from './useProjectArchive'

import { setLocale } from '@/i18n'

beforeEach(() => {
  archiveProject.mockReset()
  setLocale('zh-CN')
})

describe('useProjectArchive', () => {
  it('归档这个项目，busy 归位之后才收尾', async () => {
    archiveProject.mockResolvedValue({})
    const seen: boolean[] = []
    const state = useProjectArchive(
      () => 'p1',
      () => {
        seen.push(state.archiving.value)
      }
    )

    await state.archiveProject()

    expect(archiveProject).toHaveBeenCalledWith('p1')
    expect(seen).toEqual([false])
    expect(state.archiveError.value).toBe('')
  })

  it('被拒时理由留着，不收尾', async () => {
    archiveProject.mockRejectedValue(new Error('只有项目所有者能归档或取消归档项目'))
    const onArchived = vi.fn()
    const state = useProjectArchive(() => 'p1', onArchived)

    await state.archiveProject()

    expect(state.archiveError.value).toBe('只有项目所有者能归档或取消归档项目')
    expect(state.archiving.value).toBe(false)
    expect(onArchived).not.toHaveBeenCalled()
  })
})
