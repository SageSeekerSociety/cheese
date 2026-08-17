import { beforeEach, describe, expect, it } from 'vitest'

import { withSessionToken } from './api'

/**
 * 现场实时终端 / 运行环境预览 are backend reverse proxies loaded by an <iframe>,
 * and a browser can set no header on one — so the session token has to ride in
 * the query string. Shipping the bare URL is what made the terminal answer 404
 * and render as a white box, so this is the seam worth pinning.
 */
describe('proxy iframe credential', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('appends the session token to a proxy URL', () => {
    localStorage.setItem('accessToken', 'jwt-123')
    expect(withSessionToken('/api/topics/abc/terminal/live/')).toBe('/api/topics/abc/terminal/live/?token=jwt-123')
  })

  it('keeps an existing query string intact', () => {
    localStorage.setItem('accessToken', 'jwt-123')
    expect(withSessionToken('/api/topics/abc/app/?x=1')).toBe('/api/topics/abc/app/?x=1&token=jwt-123')
  })

  it('escapes the token instead of splicing it in raw', () => {
    localStorage.setItem('accessToken', 'a+b/c=')
    expect(withSessionToken('/api/topics/abc/app/')).toBe('/api/topics/abc/app/?token=a%2Bb%2Fc%3D')
  })

  it('leaves the URL alone when signed out — the caller then has nothing to embed', () => {
    expect(withSessionToken('/api/topics/abc/app/')).toBe('/api/topics/abc/app/')
  })
})
