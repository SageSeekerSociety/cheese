// 代码文件认得出、按代码读：预览、资料库、对话附件都按这一张表挑阅读器。
import { describe, expect, it } from 'vitest'

import { codeLanguageOf, DOCUMENT_TYPES, fileIcon, isWebPage, previewCanShowInRoom, suffixOf } from './fileKind'

describe('代码文件的类型', () => {
  it.each([
    ['tool.py', 'python'],
    ['src/main.ts', 'typescript'],
    ['App.tsx', 'typescript'],
    ['index.mjs', 'javascript'],
    ['Main.java', 'java'],
    ['lib.c', 'c'],
    ['lib.h', 'c'],
    ['lib.cpp', 'cpp'],
    ['main.go', 'go'],
    ['lib.rs', 'rust'],
    ['run.sh', 'bash'],
    ['a.json', 'json'],
    ['ci.yml', 'yaml'],
    ['pyproject.toml', 'ini'],
    ['q.sql', 'sql'],
    ['a.css', 'css'],
    ['notes.txt', 'plaintext'],
  ])('%s 按代码读，语法是 %s', (path, language) => {
    expect(DOCUMENT_TYPES[suffixOf(path)]?.view).toBe('code')
    expect(codeLanguageOf(suffixOf(path))).toBe(language)
  })

  it('房间里的代码文件开在预览里', () => {
    expect(previewCanShowInRoom('scripts/tool.py')).toBe(true)
    expect(fileIcon('scripts/tool.py')).toBe('mdi-language-python')
  })

  it('网页仍是网页，源码是网页那一格「看原文」时的读法', () => {
    expect(DOCUMENT_TYPES.html).toBeUndefined()
    expect(isWebPage('index.html')).toBe(true)
    expect(codeLanguageOf('md')).toBeNull()
  })
})
