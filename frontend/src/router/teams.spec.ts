// A team's pages sit at /teams/<handle>. Whatever name a link uses to get there, the
// page it lands on has its content: a link that names the frame above the default
// tab gets the frame alone — the right address, the header, and nothing under it.
import { createMemoryHistory, createRouter } from 'vue-router'
import { describe, expect, it } from 'vitest'

import TeamsRoutes from './teams'

const router = createRouter({ history: createMemoryHistory(), routes: [TeamsRoutes] })
const named = router.getRoutes().filter((r) => r.name)

describe('/teams/…', () => {
  it('opens a team on its projects', () => {
    const to = router.resolve('/teams/zhishi')
    expect(to.matched.at(-1)?.name).toBe('TeamsDetailDefault')
  })

  it.each(named.map((r) => [String(r.name)]))('a link to %s lands on a page', (name) => {
    const to = router.resolve({ name, params: { handle: 'zhishi' } })
    const landed = to.matched.at(-1)
    expect(landed?.children.some((child) => child.path === '')).toBe(false)
  })
})
