// 首页外框只看一格 meta：公开首页自己占满整屏，其余几页套 `.home-shell`。
// 它从 useNavigation() 读这格 meta，所以没装路由时也要能渲染（按「不是公开首页」画）。
import { createMemoryHistory, createRouter } from 'vue-router'
import { cleanup, render } from '@testing-library/vue'
import { afterEach, describe, expect, it } from 'vitest'

import Home from './Home.vue'

afterEach(cleanup)

const Page = { template: '<p data-testid="page">page</p>' }

async function mountAt(path: string) {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      {
        path: '/',
        component: Home,
        children: [
          { path: '', component: Page, meta: { publicLanding: true } },
          { path: 'teams', component: Page },
        ],
      },
    ],
  })
  await router.push(path)
  await router.isReady()
  return render({ template: '<router-view />' }, { global: { plugins: [router] } })
}

describe('Home layout', () => {
  it('lets the public landing page fill the screen without the shell', async () => {
    const view = await mountAt('/')
    expect(view.getByTestId('page')).toBeTruthy()
    expect(view.container.querySelector('.home-shell')).toBeNull()
  })

  it('wraps every other home page in the shell', async () => {
    const view = await mountAt('/teams')
    expect(view.container.querySelector('.home-shell [data-testid="page"]')).toBeTruthy()
  })

  it('renders the shell when there is no router at all', () => {
    const view = render(Home, { global: { stubs: { RouterView: true } } })
    expect(view.container.querySelector('.home-shell')).toBeTruthy()
  })
})
