// 内测版本徽标只画壳递进来的那个构建：没开徽标（或没取到）就什么都不画，开了就画短 sha。
import { cleanup, render } from '@testing-library/vue'
import { afterEach, describe, expect, it } from 'vitest'

import VersionBadge from './VersionBadge.vue'

afterEach(cleanup)

describe('VersionBadge', () => {
  it('draws nothing without a build, or when the box did not opt in', () => {
    expect(render(VersionBadge, { props: { version: null } }).container.querySelector('.version-badge')).toBeNull()
    cleanup()
    const off = render(VersionBadge, { props: { version: { sha: 'abcdef0123', short: 'abcdef0', badge: false } } })
    expect(off.container.querySelector('.version-badge')).toBeNull()
  })

  it('draws the short sha when the box opted in', () => {
    const view = render(VersionBadge, { props: { version: { sha: 'abcdef0123', short: 'abcdef0', badge: true } } })
    expect(view.getByText('abcdef0')).toBeTruthy()
  })
})
