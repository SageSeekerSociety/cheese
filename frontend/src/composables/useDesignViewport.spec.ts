import { ref } from 'vue'
import { describe, expect, it } from 'vitest'

import { useDesignViewport } from './useDesignViewport'

import { displayedRegion, imagePoint, imageRegion } from '@/components/panels/preview/designRegion'

describe('Design viewport and raster coordinates', () => {
  it('visual zoom leaves the responsive CSS viewport unchanged', () => {
    const available = ref({ width: 720, height: 450 })
    const tools = useDesignViewport(available)
    expect(tools.scale.value).toBe(0.5)
    tools.setZoom(1.25)
    expect(tools.viewport.value).toEqual({ width: 1440, height: 900 })
    expect(tools.displayed.value).toEqual({ width: 1800, height: 1125 })
    tools.selectDevice('mobile')
    expect(tools.viewport.value).toEqual({ width: 390, height: 844 })
    expect(tools.scale.value).toBe(1.25)
    tools.fit()
    expect(tools.scale.value).toBeCloseTo(450 / 844)
    available.value = { width: 300, height: 200 }
    expect(tools.scale.value).toBeCloseTo(200 / 844)
  })
  it('bounds finite zoom and ignores invalid input', () => {
    const tools = useDesignViewport(ref({ width: 0, height: 0 }))
    expect(tools.scale.value).toBe(0.1)
    tools.setZoom(99)
    expect(tools.scale.value).toBe(3)
    tools.setZoom(-9)
    expect(tools.scale.value).toBe(0.1)
    tools.setZoom(NaN)
    expect(tools.scale.value).toBe(0.1)
  })
  it('maps scrolled client coordinates into natural pixels and round-trips displayed regions', () => {
    const image = { left: -50, top: 20, width: 400, height: 200, naturalWidth: 1600, naturalHeight: 800 }
    expect(imagePoint({ x: 50, y: 70 }, image)).toEqual({ x: 400, y: 200 })
    const region = imageRegion({ x: 150, y: 120 }, { x: 50, y: 70 }, image)!
    expect(region).toEqual({ x: 400, y: 200, width: 400, height: 200 })
    expect(displayedRegion(region, image)).toEqual({ x: 100, y: 50, width: 100, height: 50 })
  })
  it('clamps drags to authorized image bounds and rejects invalid geometry/empty regions', () => {
    const image = { left: 20, top: 20, width: 400, height: 200, naturalWidth: 1600, naturalHeight: 800 }
    expect(imageRegion({ x: -50, y: -50 }, { x: 900, y: 900 }, image)).toEqual({ x: 0, y: 0, width: 1600, height: 800 })
    expect(imageRegion({ x: 20, y: 20 }, { x: 20, y: 20 }, image)).toBeNull()
    expect(imagePoint({ x: NaN, y: 0 }, image)).toBeNull()
    expect(imagePoint({ x: 0, y: 0 }, { ...image, width: 0 })).toBeNull()
  })
})
