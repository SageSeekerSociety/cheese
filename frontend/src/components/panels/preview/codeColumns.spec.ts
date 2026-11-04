// 长行横滚时「现在露出来的是第几列到第几列」。
//
// 这一格量的是读者能看见的东西：一行三十万字节的 JSON 展开两百多万像素，滚到哪儿屏幕
// 上都一样，没有这个数读者说不出自己在哪一段，也没法在对话里指出来。
import { expect, it } from 'vitest'

import { columnsOf, visibleColumns } from './codeColumns'

it('制表符按它实际占的格子数算列，不是一个字符一列', () => {
  expect(columnsOf('\tx', 4)).toBe(5)
  expect(columnsOf('a\tb', 4)).toBe(6)
  expect(columnsOf('abc', 4)).toBe(3)
})

it('一行装得下时，露出来的就是整行', () => {
  expect(visibleColumns({ scrollLeft: 0, viewWidth: 600, gutterWidth: 50, charWidth: 8, total: 20 })).toEqual({
    start: 1,
    end: 20,
    total: 20,
  })
})

it('滚到中间时报出此刻那一段，而不是从头数', () => {
  // 可用的宽度 550 px、一个字符 8 px：露得出 68 列；滚了 800 px = 100 列。
  expect(visibleColumns({ scrollLeft: 800, viewWidth: 600, gutterWidth: 50, charWidth: 8, total: 500 })).toEqual({
    start: 101,
    end: 168,
    total: 500,
  })
})

it('钉住的行号列不算进可读的那段宽度里', () => {
  const narrow = visibleColumns({ scrollLeft: 0, viewWidth: 600, gutterWidth: 50, charWidth: 8, total: 500 })
  const none = visibleColumns({ scrollLeft: 0, viewWidth: 600, gutterWidth: 0, charWidth: 8, total: 500 })
  expect(narrow.end).toBeLessThan(none.end)
})

it('滚到最右边也不会报出超过整行的列号', () => {
  const near = visibleColumns({ scrollLeft: 10_000, viewWidth: 600, gutterWidth: 50, charWidth: 8, total: 120 })
  expect(near.end).toBeLessThanOrEqual(near.total)
  expect(near.start).toBeLessThanOrEqual(near.end)
})

it('量不出字宽时退回整行，不给出一个假的坐标', () => {
  expect(visibleColumns({ scrollLeft: 100, viewWidth: 600, gutterWidth: 50, charWidth: 0, total: 42 })).toEqual({
    start: 1,
    end: 42,
    total: 42,
  })
})
