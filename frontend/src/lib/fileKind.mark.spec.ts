/**
 * The mark beside a file in a task's 文件 panel says what kind of file it is,
 * from its name alone.
 */
import { describe, expect, it } from 'vitest'

import { fileMark } from './fileKind'

describe('the kind of a file, from its name', () => {
  it.each([
    ['csim.c', 'c'],
    ['src/gemm.cpp', 'cpp'],
    ['include/cachelab.h', 'header'],
    ['matrix.hpp', 'header'],
    ['train.py', 'python'],
    ['app/main.ts', 'typescript'],
    ['index.js', 'javascript'],
    ['Main.java', 'java'],
    ['server.go', 'go'],
    ['lib.rs', 'rust'],
    ['README.md', 'markdown'],
    ['package.json', 'config'],
    ['config.yaml', 'config'],
    ['Cargo.toml', 'config'],
    ['Makefile', 'makefile'],
    ['CMakeLists.txt', 'makefile'],
    ['build.sh', 'shell'],
    ['.gitignore', 'git'],
    ['notes.txt', 'text'],
  ])('%s is %s', (name, kind) => {
    expect(fileMark(name)).toBe(kind)
  })

  it('reads the extension whatever its case', () => {
    expect(fileMark('LEGACY.C')).toBe('c')
    expect(fileMark('docs/GUIDE.MD')).toBe('markdown')
  })

  it('takes a name it does not know for a plain file', () => {
    expect(fileMark('csim-ref')).toBe('file')
    expect(fileMark('traces/case0.trace')).toBe('file')
    expect(fileMark('.bashrc')).toBe('file')
    expect(fileMark('photo.png')).toBe('file')
  })
})
