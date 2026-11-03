/**
 * 预览这一格此刻的样子：屏幕上有没有可读的东西，以及正在取的那一份走到哪一步。
 *
 * 规则：「有东西可读」和「在不在取」是两件事，可以同时成立——帧那一档换一页时上一帧还
 * 摆着，它要同时说「能看」和「在加载」。两者的组合由 `shown` 那一支的 `loading` 字段给出，
 * 不是把它压成互斥的一个值。
 */
import type { PreviewStageInput } from './previewStage'

import { expect, it } from 'vitest'

import { previewStage } from './previewStage'

const base: PreviewStageInput = {
  loading: null,
  shown: false,
  stale: false,
  rendererMissing: false,
  cannotRender: false,
}

it('屏幕上有东西可读：就是「shown」；上一版还在时带 stale，不把它说成打不开', () => {
  expect(previewStage({ ...base, shown: true })).toEqual({ stage: 'shown', stale: false, loading: null })
  expect(previewStage({ ...base, shown: true, stale: true })).toEqual({ stage: 'shown', stale: true, loading: null })
})

it('摆着一份旧的、同时还在取新的：两件事一起说，不互相吃掉', () => {
  // 帧那一档的常态：换一页时上一帧不卸。套用「有内容 = ready」会让这行「正在加载」消失。
  expect(previewStage({ ...base, shown: true, loading: 'navigate' })).toEqual({
    stage: 'shown',
    stale: false,
    loading: 'navigate',
  })
  // 文档那一档：屏幕上有东西就够，但「还在取」照样是说得出的一件事实。
  expect(previewStage({ ...base, shown: true, stale: true, loading: 'navigate' })).toEqual({
    stage: 'shown',
    stale: true,
    loading: 'navigate',
  })
})

it('屏幕上是空的、在路上：停在取数那一步等着，说得出是授权还是取字节', () => {
  expect(previewStage({ ...base, loading: 'auth' })).toEqual({ stage: 'waiting', step: 'auth' })
  expect(previewStage({ ...base, loading: 'navigate' })).toEqual({ stage: 'waiting', step: 'navigate' })
})

it('空着且落定：按失败说清是哪一种，缺服务和转不了分开说', () => {
  expect(previewStage({ ...base, rendererMissing: true })).toEqual({
    stage: 'failed',
    failure: 'rendererMissing',
  })
  expect(previewStage({ ...base, cannotRender: true })).toEqual({
    stage: 'failed',
    failure: 'cannotRender',
  })
  // 两样同时成立时，先说这个部署的问题（换个文件也一样），它不是这一份的错。
  expect(previewStage({ ...base, rendererMissing: true, cannotRender: true })).toEqual({
    stage: 'failed',
    failure: 'rendererMissing',
  })
})

it('屏幕上明明有一份可读的，就不报「打不开」——失败说的是这一份取不到', () => {
  // 有内容时 `cannotRender` 常常也为真（`docError` 有值），那是「看到的不是最新的」。
  expect(previewStage({ ...base, shown: true, stale: true, cannotRender: true })).toEqual({
    stage: 'shown',
    stale: true,
    loading: null,
  })
})

it('空着、没在取、没报错：歇着，不假装在等哪一步', () => {
  // 帧那一档没在导航时就是这样；它不该显示任何一句「正在……」。
  expect(previewStage(base)).toEqual({ stage: 'idle' })
})
