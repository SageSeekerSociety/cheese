// What a chart or a diagram draws from the document's text.
import { describe, expect, it } from 'vitest'

import { chartData } from './chartOption'
import { drawnSource } from './mermaidView'

describe('a chart drawn from its table', () => {
  it('draws an empty or unreadable cell as a gap, never as zero', () => {
    const data = chartData([
      ['周', '次数'],
      ['一', '10'],
      ['二', ''],
      ['三', '1,200'],
    ])
    expect(data.series[0].values).toEqual([10, null, 1200])
  })

  it('labels each value as the table writes it', () => {
    const data = chartData([
      ['月', '收入'],
      ['一', '$1,274'],
      ['二', '71%'],
    ])
    expect(data.series[0].texts).toEqual(['$1,274', '71%'])
  })

  it('leaves out rows with nothing in them', () => {
    expect(
      chartData([
        ['a', 'b'],
        ['x', '1'],
        ['', ''],
      ]).categories
    ).toEqual(['x'])
  })
})

describe('a diagram on a narrow screen', () => {
  it('draws a sideways flowchart top to bottom', () => {
    expect(drawnSource('flowchart LR\n  A --> B', true)).toBe('flowchart TD\n  A --> B')
    expect(drawnSource('graph RL\n  A --> B', true)).toBe('graph TD\n  A --> B')
  })

  it('draws it as written on a wide screen, and leaves other diagrams alone', () => {
    expect(drawnSource('flowchart LR\n  A --> B', false)).toBe('flowchart LR\n  A --> B')
    expect(drawnSource('sequenceDiagram\n  A->>B: LR', true)).toBe('sequenceDiagram\n  A->>B: LR')
  })
})
