import { afterEach, expect, it, vi } from 'vitest'

import { addTopicMember, listRoomTasks, listTopicMembers } from './api'

function response(data: unknown) {
  return new Response(JSON.stringify({ code: 200, data }), { headers: { 'Content-Type': 'application/json' } })
}
afterEach(() => {
  vi.unstubAllGlobals()
  localStorage.clear()
})

it('room panels share pending reads, while later refreshes fetch fresh data', async () => {
  let resolve!: (value: Response) => void
  const fetcher = vi
    .fn()
    .mockReturnValueOnce(
      new Promise((yes) => {
        resolve = yes
      })
    )
    .mockImplementation(() => Promise.resolve(response({ data: [] })))
  vi.stubGlobal('fetch', fetcher)
  const reads = [listTopicMembers('room'), listTopicMembers('room'), listTopicMembers('room')]
  await Promise.resolve()
  expect(fetcher).toHaveBeenCalledTimes(1)
  resolve(response({ data: [{ member_handle: 'alice' }] }))
  expect((await Promise.all(reads)).map((value) => value.data[0].member_handle)).toEqual(['alice', 'alice', 'alice'])
  await listTopicMembers('room')
  expect(fetcher).toHaveBeenCalledTimes(2)
  await Promise.all([
    listRoomTasks('room', { limit: 1 }),
    listRoomTasks('room', { limit: 1 }),
    listRoomTasks('room', { limit: 2 }),
  ])
  expect(fetcher).toHaveBeenCalledTimes(4)
})

it('an invitation invalidates older pending roster reads', async () => {
  let resolve!: (value: Response) => void
  const fetcher = vi
    .fn()
    .mockReturnValueOnce(
      new Promise((yes) => {
        resolve = yes
      })
    )
    .mockImplementation(() => Promise.resolve(response({ data: [] })))
  vi.stubGlobal('fetch', fetcher)
  const oldRead = listTopicMembers('room')
  await Promise.resolve()
  await addTopicMember('room', 'bob', 'member', 'alice')
  await listTopicMembers('room')
  expect(fetcher).toHaveBeenCalledTimes(3)
  resolve(response({ data: [] }))
  await oldRead
})
