// 面板里「看这张图」那几件：旋转、翻转、裁剪、亮度和饱和度，以及还原。
//
// 和对话里的看图浮层是同一套数（`@/lib/imageEdit`）：屏幕上用 transform / filter / clip-path
// 画，屏幕上和导出用同一份 `ImageAdjust`。区别只在落点——浮层是铺满窗口的一层，这里是面板里
// 的一格，所以「还原」回到原图而不是关掉什么。
import type { CropBox, ImageAdjust } from '@/lib/imageEdit'

import { computed, ref, watch } from 'vue'

import { carryCrop, cssClip, cssFilter, isEdited, ORIGINAL, sideways } from '@/lib/imageEdit'

export function useDesignAdjust(identity: () => string) {
  const adjust = ref<ImageAdjust>({ ...ORIGINAL })
  /** 正在框裁剪范围：这时图不转也不平移，指针归裁剪框。 */
  const cropping = ref(false)
  const draftCrop = ref<CropBox>({ x: 0.1, y: 0.1, w: 0.8, h: 0.8 })

  const edited = computed(() => isEdited(adjust.value))
  const turned = computed(() => sideways(adjust.value))
  const filter = computed(() => cssFilter(adjust.value))
  const clip = computed(() => cssClip(adjust.value))

  function change(next: Partial<ImageAdjust>) {
    adjust.value = { ...adjust.value, ...next }
  }
  function rotate() {
    const next = { ...adjust.value, rotate: (adjust.value.rotate + 90) % 360 }
    change({ rotate: next.rotate, crop: adjust.value.crop && carryCrop(adjust.value.crop, adjust.value, next) })
  }
  function flip() {
    const next = { ...adjust.value, flip: !adjust.value.flip }
    change({ flip: next.flip, crop: adjust.value.crop && carryCrop(adjust.value.crop, adjust.value, next) })
  }
  function startCrop() {
    if (cropping.value) return finishCrop(false)
    draftCrop.value = adjust.value.crop ?? { x: 0.1, y: 0.1, w: 0.8, h: 0.8 }
    cropping.value = true
  }
  function finishCrop(apply: boolean) {
    if (apply) change({ crop: { ...draftCrop.value } })
    cropping.value = false
  }
  function restore() {
    adjust.value = { ...ORIGINAL }
    cropping.value = false
  }

  // 换了图（或换了版本）：上一张的转动和调色不该落到下一张上。
  watch(identity, () => restore(), { flush: 'sync' })

  return {
    adjust,
    edited,
    turned,
    filter,
    clip,
    cropping,
    draftCrop,
    rotate,
    flip,
    startCrop,
    finishCrop,
    restore,
    change,
  }
}
