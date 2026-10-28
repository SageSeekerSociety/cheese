import type { ImageAdjust } from '@/lib/imageEdit'

import { sourcePoint } from '@/lib/imageEdit'

export type Point = { x: number; y: number }
export type RasterRegion = { x: number; y: number; width: number; height: number }
/** 图上框出来的一处评论：id 用来打开那一串，矩形是**原图像素**——和框选发出去的那份
 *  引用同一套坐标，显示时按屏幕上的缩放换算。 */
export type ImageRegionMark = { id: string; region: RasterRegion }
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
  /**
   * 屏幕上的这张图转过、翻过没有。
   *
   * 转了的话 `left/top/width/height` 是**转完看到**的那个框，而框选、笔画、评论标记
   * 一律按原图像素记；换算时先按它把看到的点折回原图，否则转 90° 之后框出来的位置
   * 会整体错开。
   */
  adjust?: Pick<ImageAdjust, 'rotate' | 'flip'>
  /** 原图像素 → 屏幕像素的比例。转过 90° 时 `width` 是转后的宽，和原图宽对不上，所以
   *  叠在图上那些（选中框、评论点）要用它，不能拿 width/naturalWidth 现推。 */
  scale?: number
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
  const shown = {
    x: Math.max(0, Math.min(1, (point.x - image.left) / image.width)),
    y: Math.max(0, Math.min(1, (point.y - image.top) / image.height)),
  }
  const at = image.adjust ? sourcePoint(shown, image.adjust) : shown
  return {
    x: Math.max(0, Math.min(image.naturalWidth, at.x * image.naturalWidth)),
    y: Math.max(0, Math.min(image.naturalHeight, at.y * image.naturalHeight)),
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
  const k = image.scale ?? image.width / image.naturalWidth
  return {
    x: region.x * k,
    y: region.y * k,
    width: region.width * k,
    height: region.height * k,
  }
}
