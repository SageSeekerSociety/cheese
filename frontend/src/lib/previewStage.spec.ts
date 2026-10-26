/**
 * 预览这一格此刻在哪一步、失败说的是哪一件事。
 *
 * 规则：有东西可看就是就绪（上一版还在时是「已更新」，不把「看到的不是最新的」说成
 * 打不开）；没得看且在路上，就是走那一步（应用先在「连接」）；都落定还是空的，按失败
 * 说清是哪一种。
 */
import { expect, it } from 'vitest'

import { previewStage } from './previewStage'

const base = {
  loading: false,
  shown: false,
  stale: false,
  rendererMissing: false,
  cannotRender: false,
}

it('有东西可看就是就绪；上一版还在时是「已更新」，不是「打不开」', () => {
  expect(previewStage({ ...base, shown: true })).toEqual({ stage: 'ready' })
  expect(previewStage({ ...base, shown: true, stale: true })).toEqual({ stage: 'updated', stale: true })
})

it('没得看、在路上就是「导航」', () => {
  expect(previewStage({ ...base, loading: true })).toEqual({ stage: 'navigate' })
})

it('落定还是空的：按失败说清是哪一种，缺服务和转不了分开说', () => {
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

it('什么都没有、也没报错：停在授权那一步等着', () => {
  expect(previewStage(base)).toEqual({ stage: 'auth' })
})
