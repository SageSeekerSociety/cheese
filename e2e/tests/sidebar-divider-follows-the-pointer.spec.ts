import { expect, test, type Page } from '@playwright/test'
import { apiLogin } from './helpers'

// 宽档里侧栏是外壳布局区钉住的一格（layouts/AppShell.vue），拖它和页面之间那条分隔线就是
// 改侧栏宽度：分界线得跟着指针走，松手停在那儿，刷新回来还是这个宽度。
//
// 只有真浏览器量得出来：happy-dom 没有布局，宽度是多少都看不出来。

/** 侧栏右缘现在在哪儿。 */
function edge(page: Page) {
  return page.evaluate(() => document.getElementById('secondary-sidebar')!.getBoundingClientRect().right)
}

test('拖侧栏和页面之间的分隔线，分界线跟着指针走，刷新后还是那个宽度', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await apiLogin(page)
  await page.goto('/inbox')
  await expect(page.locator('#secondary-sidebar')).toBeVisible({ timeout: 30_000 })
  // 拖动要等布局区的分隔线挂上（dockview 是按需加载的）。
  await page.locator('.dv-sash').first().waitFor({ state: 'attached', timeout: 30_000 })

  // 指针从右缘出发，一步 24px 往右拖，每走一步量一次：分界线此刻应该已经跟到了。
  const start = await edge(page)
  const y = 400
  await page.mouse.move(start, y)
  await page.mouse.down()
  let worst = 0
  for (let step = 1; step <= 5; step++) {
    const x = start + step * 24
    await page.mouse.move(x, y)
    worst = Math.max(worst, Math.abs(x - (await edge(page))))
  }
  expect(worst, '拖着走的时候分界线落在指针后面').toBeLessThanOrEqual(2)

  // 松手就停在指针停下的地方，不再自己滑一段。
  await page.mouse.up()
  await expect.poll(() => edge(page), { timeout: 2_000 }).toBeCloseTo(start + 120, -1)

  // 刷新回来是拖出来的宽度。
  await page.reload()
  await expect(page.locator('#secondary-sidebar')).toBeVisible({ timeout: 30_000 })
  await expect.poll(() => edge(page), { timeout: 10_000 }).toBeCloseTo(start + 120, -1)
})
