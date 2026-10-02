export type Point = { x: number; y: number }
export type RasterRegion = { x: number; y: number; width: number; height: number }
export type RasterSelection = {
  region: RasterRegion
  identity: string
  src: string
  naturalWidth: number
  naturalHeight: number
}
export type ImageGeometry = {
  left: number
  top: number
  width: number
  height: number
  naturalWidth: number
  naturalHeight: number
}

/** client pixels → natural image pixels. Geometry must describe the displayed image, not the pane. */
export function imagePoint(point: Point, image: ImageGeometry): Point | null {
  if (
    ![
      point.x,
      point.y,
      image.left,
      image.top,
      image.width,
      image.height,
      image.naturalWidth,
      image.naturalHeight,
    ].every(Number.isFinite) ||
    image.width <= 0 ||
    image.height <= 0 ||
    image.naturalWidth <= 0 ||
    image.naturalHeight <= 0
  )
    return null
  return {
    x: Math.max(0, Math.min(image.naturalWidth, ((point.x - image.left) * image.naturalWidth) / image.width)),
    y: Math.max(0, Math.min(image.naturalHeight, ((point.y - image.top) * image.naturalHeight) / image.height)),
  }
}
export function imageRegion(start: Point, end: Point, image: ImageGeometry): RasterRegion | null {
  const a = imagePoint(start, image)
  const b = imagePoint(end, image)
  if (!a || !b) return null
  const x = Math.floor(Math.min(a.x, b.x))
  const y = Math.floor(Math.min(a.y, b.y))
  const right = Math.ceil(Math.max(a.x, b.x))
  const bottom = Math.ceil(Math.max(a.y, b.y))
  if (right <= x || bottom <= y) return null
  return { x, y, width: right - x, height: bottom - y }
}
export function displayedRegion(region: RasterRegion, image: ImageGeometry): RasterRegion {
  return {
    x: (region.x * image.width) / image.naturalWidth,
    y: (region.y * image.height) / image.naturalHeight,
    width: (region.width * image.width) / image.naturalWidth,
    height: (region.height * image.height) / image.naturalHeight,
  }
}
