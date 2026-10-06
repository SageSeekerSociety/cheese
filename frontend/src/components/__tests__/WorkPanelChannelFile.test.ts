/** 频道里点一份项目文件，开的是什么。
 *
 * 频道里没有人改项目的文件，所以频道里提到的一份文件就是项目现在的样子：右侧开一格
 * 只读的页签，读的是项目当前版本，不是哪件任务改过的那份。当前版本里没有它时，说清
 * 这一点，并给出改过它的任务。
 */
import type { Topic } from '../../cx_types'

import { defineComponent, h, ref } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, waitFor } from '@testing-library/vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '@/api'
import i18n, { setLocale } from '@/i18n'

beforeEach(() => setLocale('zh-CN'))

vi.mock('../CodeEditor.vue', () => ({
  default: {
    name: 'CodeEditor',
    props: ['modelValue', 'filename', 'readonly', 'lines'],
    template:
      '<textarea class="stub-editor" :readonly="readonly" :value="modelValue" :data-lines="lines ? lines.start + `-` + lines.end : ``" />',
  },
}))

const readPreviewFile = vi.fn()
const readFile = vi.fn()
const getGitDiff = vi.fn()

vi.mock('../../api', async () => {
  const actual = await vi.importActual<typeof import('../../api')>('../../api')
  return {
    ...actual,
    readPreviewFile: (...a: unknown[]) => readPreviewFile(...a),
    readFile: (...a: unknown[]) => readFile(...a),
    getGitDiff: (...a: unknown[]) => getGitDiff(...a),
    getPreview: vi.fn().mockResolvedValue(null),
    getTopicWorkSummary: vi.fn().mockResolvedValue({ changed_files: [], has_run: false }),
    listRoomTasks: vi.fn().mockResolvedValue({
      data: [
        {
          id: 'task-1',
          project_id: 'p1',
          room_id: 'chan-1',
          title: '报名表单改版',
          status: 'open',
          branch_name: 'task/form',
          presentation: { column: 'building', phrase: 'running' },
          blocks: [],
        },
      ],
      total: 1,
    }),
  }
})

import WorkPanel from '../WorkPanel.vue'

function channel(): Topic {
  return { id: 'chan-1', project_id: 'p1', title: '报名表单', status: 'active' } as Topic
}

async function flush() {
  for (let i = 0; i < 8; i += 1) await new Promise((r) => setTimeout(r, 0))
}

function mountChannel() {
  const panel = ref<{ openFile: (path: string) => Promise<void> } | null>(null)
  const openCard = vi.fn()
  const Host = defineComponent(
    () => () => h(WorkPanel, { ref: panel, topic: channel(), projectId: 'p1', activityTick: 0, onOpenCard: openCard })
  )
  const ui = render(Host, { global: { plugins: [createVuetify({ components, directives }), i18n] } })
  return { ...ui, panel, openCard }
}

describe('频道里点一份项目文件', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    // 不是频道自己的文件（芝士交付的、人传的）。
    readPreviewFile.mockRejectedValue(new ApiError(404, 'not found'))
  })

  it('开一格只读页签，读的是项目当前版本，滚到 chip 指着的那几行', async () => {
    readFile.mockResolvedValue({
      path: 'src/form.ts',
      content: 'export const fields = 4\n',
      version: 'v1',
      bytes: 24,
      binary: false,
      too_large: false,
    })
    const { container, panel, getByRole } = mountChannel()
    await flush()
    await panel.value!.openFile('src/form.ts:42-44')
    await flush()

    expect(readFile).toHaveBeenCalledWith('p1', 'src/form.ts', 'chan-1', null, 'committed')
    const editor = container.querySelector<HTMLTextAreaElement>('.stub-editor')
    expect(editor?.value).toBe('export const fields = 4\n')
    expect(editor?.readOnly).toBe(true)
    expect(editor?.dataset.lines).toBe('42-44')
    expect(getByRole('tab', { name: /form\.ts/ }).getAttribute('aria-selected')).toBe('true')
  })

  it('项目当前版本里没有它：说清楚，并给出改过它的任务', async () => {
    readFile.mockRejectedValue(new ApiError(404, 'not found'))
    getGitDiff.mockResolvedValue({
      diff: 'diff --git a/src/new.ts b/src/new.ts\nnew file mode 100644\n--- /dev/null\n+++ b/src/new.ts\n@@ -0,0 +1 @@\n+x\n',
    })
    const { panel, findByText, openCard } = mountChannel()
    await flush()
    await panel.value!.openFile('src/new.ts')
    await flush()

    expect(await findByText('项目当前版本里没有这个文件')).toBeTruthy()
    await fireEvent.click(await findByText('在任务「报名表单改版」里看'))
    await waitFor(() => expect(openCard).toHaveBeenCalledWith('task-1'))
  })
})
