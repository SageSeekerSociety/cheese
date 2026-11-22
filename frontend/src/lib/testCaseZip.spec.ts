/**
 * 「导入用例」：zip 里成对的文件配成输入/输出用例，大小和个数照后端的上限把关，
 * 超了整包不导入。字节是真的 zip（存储和 deflate 两种都有）。
 */
import { describe, expect, it } from 'vitest'

import { casesFromZip, MAX_INLINE_BYTES, pairCaseFiles, readTestFile } from './testCaseZip'
import { zipFixture } from './zipFixture.spec.helper'

describe('pairCaseFiles', () => {
  it('配 1.in/1.out、1.in/1.ans、input1.txt/output1.txt，按名字的自然顺序', () => {
    const { pairs, unpaired } = pairCaseFiles([
      '10.in',
      '10.out',
      '2.in',
      '2.ans',
      'input3.txt',
      'output3.txt',
      'README.md',
      '4.in',
      'data/',
      '__MACOSX/._2.in',
      '.DS_Store',
    ])
    expect(pairs).toEqual([
      { name: '2', input: '2.in', output: '2.ans' },
      { name: '3', input: 'input3.txt', output: 'output3.txt' },
      { name: '10', input: '10.in', output: '10.out' },
    ])
    // README 不是用例文件，4.in 没有配对的输出：都只计数。
    expect(unpaired).toBe(2)
  })

  it('.out 和 .ans 都在时用 .out；同名的出现在几个目录里，名称带上目录', () => {
    const { pairs } = pairCaseFiles(['a/1.in', 'a/1.ans', 'a/1.out', 'b/1.in', 'b/1.out'])
    expect(pairs).toEqual([
      { name: 'a/1', input: 'a/1.in', output: 'a/1.out' },
      { name: 'b/1', input: 'b/1.in', output: 'b/1.out' },
    ])
  })
})

describe('casesFromZip', () => {
  it('拆开 zip 读成隐藏用例，每个 10 分', async () => {
    const bytes = await zipFixture([
      { name: 'tests/1.in', data: '1 2\n' },
      { name: 'tests/1.out', data: '3\n', method: 8 },
      { name: 'tests/2.in', data: '5 5\n'.repeat(50), method: 8 },
      { name: 'tests/2.out', data: '10\n' },
      { name: 'tests/notes.txt', data: 'x' },
    ])
    const result = await casesFromZip(bytes)
    expect(result).toEqual({
      ok: true,
      unpaired: 1,
      cases: [
        { name: '1', points: 10, visibility: 'HIDDEN', input: '1 2\n', expected: '3\n' },
        { name: '2', points: 10, visibility: 'HIDDEN', input: '5 5\n'.repeat(50), expected: '10\n' },
      ],
    })
  })

  it('一边超过 64 KB：整包不导入，说是哪一份', async () => {
    const bytes = await zipFixture([
      { name: '1.in', data: 'a' },
      { name: '1.out', data: 'b' },
      { name: '2.in', data: 'x'.repeat(MAX_INLINE_BYTES + 1), method: 8 },
      { name: '2.out', data: 'y' },
    ])
    expect(await casesFromZip(bytes)).toEqual({
      ok: false,
      error: { key: 'tooLarge', params: { file: '2.in', kb: 64 } },
    })
  })

  it('加上已有的超过 200 个：不导入', async () => {
    const bytes = await zipFixture([
      { name: '1.in', data: 'a' },
      { name: '1.out', data: 'b' },
      { name: '2.in', data: 'a' },
      { name: '2.out', data: 'b' },
    ])
    expect(await casesFromZip(bytes, 199)).toEqual({
      ok: false,
      error: { key: 'tooMany', params: { n: 2, max: 200, existing: 199 } },
    })
    expect((await casesFromZip(bytes, 198)).ok).toBe(true)
  })

  it('不是 zip、没有配得上对的、不是 UTF-8 文本：各说各的', async () => {
    expect(await casesFromZip(new TextEncoder().encode('not a zip').buffer as ArrayBuffer)).toEqual({
      ok: false,
      error: { key: 'notZip' },
    })
    expect(await casesFromZip(await zipFixture([{ name: '1.in', data: 'a' }]))).toEqual({
      ok: false,
      error: { key: 'empty' },
    })
    const binary = await zipFixture([
      { name: '1.in', data: new Uint8Array([0xff, 0xfe, 0x00]) },
      { name: '1.out', data: 'b' },
    ])
    expect(await casesFromZip(binary)).toEqual({ ok: false, error: { key: 'notText', params: { file: '1.in' } } })
  })
})

describe('readTestFile', () => {
  it('UTF-8 文本送 content，读不了的送 base64', async () => {
    expect(await readTestFile(new File(['def test_a():\n  pass\n'], 'test_a.py'))).toEqual({
      path: 'test_a.py',
      content: 'def test_a():\n  pass\n',
      bytes: 21,
    })
    expect(await readTestFile(new File([new Uint8Array([0xff, 0x00, 0x01])], 'data.bin'), 'tests/data.bin')).toEqual({
      path: 'tests/data.bin',
      base64: '/wAB',
      bytes: 3,
    })
  })
})
