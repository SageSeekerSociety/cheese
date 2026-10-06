// The view AppSignInStart.vue renders: only a line while the page it belongs to
// keeps the app's challenge and redirects.
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import { cleanup, render } from '@testing-library/vue'
import { afterEach, describe, expect, it } from 'vitest'

import AppSignInStartView from './AppSignInStartView.vue'

afterEach(cleanup)

describe('AppSignInStartView', () => {
  it('draws a line while the page decides where to send the sign-in', () => {
    const { container } = render(AppSignInStartView, {
      global: { plugins: [createVuetify({ components })] },
    })
    expect(container.querySelector('.v-progress-linear')).toBeTruthy()
  })
})
