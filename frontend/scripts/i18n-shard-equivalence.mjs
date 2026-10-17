// 一个命名空间被拆成分片目录之后，「只是换了个位置」这句话要能验：把分片按原文件的键序
// 合起来，和拆分前的那一版逐字节比。
//
// 用法：node frontend/scripts/i18n-shard-equivalence.mjs <命名空间> <拆分前的 git 版本>
//
//   node frontend/scripts/i18n-shard-equivalence.mjs roomNotice \
//     "$(git rev-parse HEAD~1)"        # 拆分的那一个提交的父提交
//
// 为什么是脚本而不是常跑的测试：分片是活的，以后改一条文案，「和拆分前逐字节相同」就不
// 成立了——把它当常跑测试，等于让每个改文案的 PR 都去改一个共享的期望值，正是拆分要消掉
// 的那种「人人必改一份文件」。所以这条断言按需跑、跑一次就够，键序那件事也留在这里。
// 常跑的是分片树自己的不变量（两个 locale 的分片名一一对应），在
// `frontend/src/i18n/catalog.spec.ts`。
//
// 复用它验别的拆分：apiError 这类平表命名空间同样吃。

import { execFileSync } from 'node:child_process'
import { readdirSync, readFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const REPO = resolve(dirname(fileURLToPath(import.meta.url)), '../..')
const LOCALES = ['zh-CN', 'en']

const [namespace, rev] = process.argv.slice(2)
if (!namespace || !rev) {
  console.error('用法: node frontend/scripts/i18n-shard-equivalence.mjs <命名空间> <拆分前的 git 版本>')
  process.exit(2)
}

const git = (...args) => execFileSync('git', ['-C', REPO, ...args], { encoding: 'utf8', maxBuffer: 64 << 20 })

// 分片文件写回磁盘用的就是这个序列化：indent 2、键按写入顺序、末尾一个换行。
// 拆分脚本也是这么写的，所以这里能逐字节比。
const serialize = (table) => JSON.stringify(table, null, 2) + '\n'

let failed = false
for (const locale of LOCALES) {
  const relative = `frontend/src/i18n/messages/${locale}/${namespace}.json`
  const before = git('show', `${rev}:${relative}`)
  const original = JSON.parse(before)

  const dir = join(REPO, 'frontend/src/i18n/messages', locale, namespace)
  const shards = readdirSync(dir)
    .filter((name) => name.endsWith('.json'))
    .sort()
  const merged = new Map()
  for (const shard of shards) {
    for (const [key, value] of Object.entries(JSON.parse(readFileSync(join(dir, shard), 'utf8')))) {
      if (merged.has(key)) {
        console.error(`${locale}: ${key} 在两个分片里都有（${shard}）`)
        failed = true
      }
      merged.set(key, value)
    }
  }

  // 按原文件的键序合起来——顺序也是这次搬动要保住的东西之一。
  const rebuilt = {}
  const missing = Object.keys(original).filter((key) => !merged.has(key))
  for (const key of Object.keys(original)) {
    if (merged.has(key)) rebuilt[key] = merged.get(key)
  }
  const added = [...merged.keys()].filter((key) => !(key in original))

  const after = serialize(rebuilt)
  const same =
    Buffer.from(after, 'utf8').equals(Buffer.from(before, 'utf8')) && missing.length === 0 && added.length === 0
  const keys = Object.keys(original).length
  console.log(
    `${locale}  ${shards.length} 片  ${keys} 键  ${Buffer.byteLength(before, 'utf8')}B  ` +
      `逐字节相同 ${same ? '✓' : '✗'}`
  )
  if (missing.length) {
    console.error(`  分片里少了这些键：${missing.join(', ')}`)
  }
  if (added.length) {
    console.error(`  分片里多了这些键：${added.join(', ')}`)
  }
  if (!same) failed = true
}

if (failed) {
  console.error(`\n拆出来的分片和 ${rev}:${namespace}.json 不是同一份内容。`)
  process.exit(1)
}
console.log(`\n分片合起来与 ${rev} 的那一版逐字节相同。`)
