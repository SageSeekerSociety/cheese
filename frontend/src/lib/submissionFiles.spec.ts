import { describe, expect, it } from 'vitest'

import { mergeFiles, siblingResolver } from './submissionFiles'

describe('an image a submitted report refers to', () => {
  const resolve = siblingResolver([
    { name: 'report.md', url: '/api/uploads/r' },
    { name: 'autograder.png', url: '/api/uploads/a' },
    { name: 'chart 1.png', url: '/api/uploads/c' },
    { name: 'Tests.PNG', url: '/api/uploads/t' },
  ])

  it('is the file of that name handed in with it', () => {
    expect(resolve('autograder.png')).toBe('/api/uploads/a')
    expect(resolve('./autograder.png')).toBe('/api/uploads/a')
  })

  it('is found by its file name when the report kept it in a folder', () => {
    expect(resolve('./img/autograder.png')).toBe('/api/uploads/a')
    expect(resolve('../shots/autograder.png?raw=1')).toBe('/api/uploads/a')
  })

  it('is found when its name is written escaped or in another case', () => {
    expect(resolve('chart%201.png')).toBe('/api/uploads/c')
    expect(resolve('tests.png')).toBe('/api/uploads/t')
  })

  it('is no image at all when nothing handed in has that name', () => {
    expect(resolve('missing.png')).toBeNull()
  })

  it('keeps an address of its own as written', () => {
    for (const src of ['https://example.com/a.png', 'data:image/png;base64,AAAA', '/static/a.png']) {
      expect(resolve(src)).toBe(src)
    }
  })
})

describe('picking more files for an item', () => {
  const file = (name: string, body = name) => new File([body], name)

  it('adds them after the ones already picked, and replaces one of the same name', () => {
    const merged = mergeFiles([file('report.md'), file('a.png', 'old')], [file('a.png', 'newer'), file('b.png')])
    expect(merged.map((f) => f.name)).toEqual(['report.md', 'a.png', 'b.png'])
    expect(merged[1].size).toBe('newer'.length)
  })
})
