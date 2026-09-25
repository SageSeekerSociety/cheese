// A person's page sits at /users/<handle>, on the same level as the settings
// pages; those stay reachable whatever people are called.
import { createMemoryHistory, createRouter } from 'vue-router'
import { describe, expect, it } from 'vitest'

import UserRoutes from './user'

const router = createRouter({ history: createMemoryHistory(), routes: [UserRoutes] })

describe('/users/…', () => {
  it('opens a person by their handle', () => {
    const to = router.resolve('/users/linzhiyuan')
    expect(to.name).toBe('UserPage')
    expect(to.params.handle).toBe('linzhiyuan')
  })

  it.each([
    ['/users/settings/profile', 'UserSettingsProfile'],
    ['/users/settings/security', 'UserSettingsSecurity'],
  ])('still opens %s rather than a person named like it', (path, name) => {
    expect(router.resolve(path).name).toBe(name)
  })
})
