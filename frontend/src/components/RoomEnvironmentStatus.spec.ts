import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, render } from '@testing-library/vue'
import { afterEach, expect, it, vi } from 'vitest'

const getRoomEnvironment = vi.hoisted(() => vi.fn())
vi.mock('../api', () => ({ getRoomEnvironment }))
import RoomEnvironmentStatus from './RoomEnvironmentStatus.vue'

afterEach(cleanup)

function mount() {
  return render(RoomEnvironmentStatus, {
    props: { projectId: 'p', topicId: 't' },
    global: { plugins: [createVuetify({ components, directives })] },
  })
}

it('shows preparation while task delivery is waiting and renders logs literally', async () => {
  getRoomEnvironment.mockResolvedValue({ state: 'preparing', stage: 'setup', log: '<script>output</script>' })
  const view = mount()
  expect(await view.findByText('正在安装工具，完成后芝士会继续处理你的消息')).toBeTruthy()
  expect(await view.findByText('<script>output</script>')).toBeTruthy()
  expect(view.container.querySelector('script')).toBeNull()
})

it('shows the overview handoff and the failed automatic repair outcome', async () => {
  getRoomEnvironment.mockResolvedValue({ state: 'failed', recovery_state: 'requested' })
  const view = mount()
  expect(await view.findByText('已交给总览芝士检查，可在总览查看处理情况。')).toBeTruthy()
  getRoomEnvironment.mockResolvedValue({ state: 'failed', recovery_state: 'needs_help' })
  await view.rerender({ topicId: 'other' })
  expect(await view.findByText('自动处理未能恢复环境，请在总览查看需要的协助。')).toBeTruthy()
})
