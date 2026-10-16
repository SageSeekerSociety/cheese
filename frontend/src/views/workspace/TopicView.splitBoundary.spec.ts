/// <reference types="node" />
// 话题视图这一页的拆分边界。
//
// `TopicView.vue` 曾经 974 行，逼近 `.claude/scripts/check-file-sizes.py` 的 1000 行红线。
// 拆不改行为：对外还是同一个路由入口、同一组 props、同一份模板。行为由同目录那六份
// `.spec.ts` 锁着——它们一行没改，拆分后照样通过；谁把哪一块的接线掏空，它们就红
// （把 `useTopicAddress` 那一行换成注释，`TopicView.deepLink.spec.ts` 立刻红 4 条）。
//
// 这一份锁的是它们锁不到的三件事：这一页和 `topicView/` 下的每一块都在预算内（预算不是
// 只套在这份名单上，名单对不上目录也红）；那些职责真的搬进了模块、主文件里没有留一份；
// 对外那个路由入口没变——四个 prop 一个不多一个不少。扫源码而不是挂载：它断言的是「哪
// 一段代码在哪个文件里」，那是文件本身说的事，挂载这一页要拖进来 Vuetify 和十几次接口
// 调用，也答不出多一行少一行。
import { readdirSync, readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { describe, expect, it } from 'vitest'

const HERE = dirname(fileURLToPath(import.meta.url))
const raw = (rel: string) => readFileSync(join(HERE, rel), 'utf8')

/** 去掉注释之后的源码：断言的是代码，不是注释里提到的名字。`https://` 那种不算注释。 */
const code = (text: string) =>
  text
    .replace(/<!--[\s\S]*?-->/g, '')
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/(^|[^:])\/\/.*$/gm, '$1')

/** 一个文件几行。和 `.claude/scripts/check-file-sizes.py` 的 `count_lines` 同一个算法：
 *  末行没有换行时也是一行，写成 `split('\n').length - 1` 会少算它。 */
const lines = (text: string) => text.split('\n').length - (text.endsWith('\n') ? 1 : 0)

/** 这次拆分给自己定的预算，比仓库那条 1000 行红线紧：红线只保证它不再长回去，
 *  650 才是这次拆分要成的形状。 */
const BUDGET = 650

const PAGE = 'TopicView.vue'

/** `topicView/` 下现在有哪些文件，按路径排。契约允许拆成子组件或逻辑模块，所以 `.vue`
 *  也算，子目录里的也算——只数平铺的 `.ts`，换一种拆法整块就没人看着了。 */
function walk(rel: string): string[] {
  return readdirSync(join(HERE, rel), { withFileTypes: true }).flatMap((entry) => {
    const path = `${rel}/${entry.name}`
    if (entry.isDirectory()) return walk(path)
    return /\.(ts|vue)$/.test(entry.name) ? [path] : []
  })
}
const onDisk = walk('topicView').sort()

/** 拆出去的那几块，和它们各自管的事。 */
const MODULES: readonly [string, string][] = [
  ['topicView/shared.ts', '共用的类型：这个地址指着谁，两块组件往回够的那几处'],
  ['topicView/address.ts', '地址：打开哪一页、哪一段，面板开着哪一格'],
  ['topicView/panel.ts', '右侧面板的收 / 开和那条 Esc 栈'],
  ['topicView/conversation.ts', '这一段对话：支线、任务页、署名'],
  ['topicView/header.ts', '页头和这一页的标题'],
  ['topicView/live.ts', '这个房间此刻是不是活的'],
  ['topicView/actions.ts', '对话栏和面板回上来的那几件事各归哪一处'],
]

/** 每一块交出来的入口，主文件按名字从它那儿取。 */
const FACTORIES: readonly [string, string][] = [
  ['topicView/address.ts', 'useTopicAddress'],
  ['topicView/panel.ts', 'useTopicPanelState'],
  ['topicView/conversation.ts', 'useTopicConversation'],
  ['topicView/header.ts', 'useTopicHeader'],
  ['topicView/live.ts', 'useTopicLive'],
  ['topicView/actions.ts', 'useTopicActions'],
]

/** 从主文件搬进哪一块的那几件事：拆之前每一处都是主文件自己 new 出来的。 */
/** 主文件只要类型的那几处。`shared.ts` 里全是类型，上面那条查的是取值 import，够不到它：
 *  这一页把两侧组件的把手类型从那儿引进来，删掉这一行就等于少了一块没人看着。 */
const TYPE_IMPORTS: readonly [string, string][] = [
  ['topicView/shared.ts', 'ChatColumnHandle'],
  ['topicView/shared.ts', 'WorkPanelHandle'],
]

const MOVED: readonly [string, string][] = [
  ['topicView/address.ts', 'useRoomTabHistory'],
  ['topicView/address.ts', 'useTopBarBack'],
  ['topicView/address.ts', 'usePlace'],
  ['topicView/panel.ts', 'useTopicPanel'],
  ['topicView/panel.ts', 'useEscapeLayer'],
  ['topicView/conversation.ts', 'useChannelThreads'],
  ['topicView/conversation.ts', 'useConversationOpening'],
  ['topicView/conversation.ts', 'useTaskPage'],
  ['topicView/conversation.ts', 'roomMembersQuery'],
  ['topicView/header.ts', 'usePageTitle'],
]

const esc = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
const escStem = (file: string) => esc(file.replace(/^topicView\//, '').replace(/\.ts$/, ''))
/** 这一段代码里调没调这个名字。`useTopicPanelState(` 不算调了 `useTopicPanel`。 */
const calls = (text: string, fn: string) => new RegExp(`(?<![\\w.$])${esc(fn)}\\s*\\(`).test(text)

/** 页面从 `./topicView/<module>` 取值的 import 语句取了哪几个名字（`import type` 不算）。
 *  取的那几个名字里不许再出现 `from`：否则这一条会从更早的一条 import 一路吞下来，
 *  把别的模块的名字也算成它取的，接错模块就查不出来了。 */
function valueImports(text: string, module: string): string[] {
  const re = new RegExp(
    `^import\\s+(?!type\\b)((?:(?!\\bfrom\\b)[^;])*?)\\sfrom\\s+'\\./topicView/${escStem(module)}'`,
    'gm'
  )
  return [...text.matchAll(re)].map((m) => m[1]!)
}

/** 页面是不是按这个名字、从这个模块取的，而且真的调了它。 */
const wired = (text: string, module: string, fn: string) =>
  valueImports(text, module).some((clause) => new RegExp(`(^|[\\s{,])${esc(fn)}([\\s,}]|$)`).test(clause)) &&
  calls(text, fn)

/** 页面调它的时候接住了返回值：`const x = useXxx(` 或者拆开来接。光调一下不算取了值。
 *  泛型那一截粗略带过就好：`useTopicHeader<TaskHalf extends Record<string, unknown>>`
 *  里面还嵌着尖括号，硬配会把它整条漏掉。 */
const assigned = (text: string, fn: string) => new RegExp(`=\\s*${esc(fn)}\\s*(?:<[^;()]*>)?\\s*\\(`).test(text)

/** 主文件自己又声明了一遍这个名字。上面那几处全是按名字查的，同名的局部函数能顶替
 *  掉搬走的那一块：import 留着、名字也叫着，跑的却是主文件里那一份。 */
const shadowed = (text: string, fn: string) => new RegExp(`\\b(?:function|const|let|var)\\s+${esc(fn)}\\b`).test(text)

/** 页面 `import type { … } from './topicView/<module>'` 里有没有这个名字。 */
const wiredType = (text: string, module: string, name: string) => {
  const re = new RegExp(
    `^import\\s+type\\s+((?:(?!\\bfrom\\b)[^;])*?)\\sfrom\\s+'\\./topicView/${escStem(module)}'`,
    'gm'
  )
  return [...text.matchAll(re)].some((m) => new RegExp(`(^|[\\s{,])${esc(name)}([\\s,}]|$)`).test(m[1]!))
}

/** 这一块自己交出来的就是那个工厂：`export function useXxx`。 */
const exportsFactory = (file: string, fn: string) =>
  new RegExp(`\\bexport\\s+function\\s+${esc(fn)}\\b`).test(code(raw(file)))

const page = code(raw(PAGE))

describe('话题视图的拆分边界', () => {
  it('主文件和 topicView 下的每一块都在 650 行以内', () => {
    // 按目录数，不按上面那份名单数：单是名单的话，新搬出去的一块就没预算管着了。
    const files = [PAGE, ...onDisk]
    const sizes = files.map((file) => [file, lines(raw(file))] as const)
    expect(sizes.filter(([, n]) => n > BUDGET)).toEqual([])
  })

  it('名单就是目录：搬出去的那几块一个不少', () => {
    expect(MODULES.map(([file]) => file).sort()).toEqual([...onDisk].sort())
  })

  it('主文件从同目录那几块取值，并且真的调了它们', () => {
    for (const [file, fn] of FACTORIES) {
      // 五件事一起查：那一块交出了这个工厂、页面按名字从它那里取值、页面调了它、
      // 接住了返回值、而且主文件没有自己再声明一个同名的顶替它。少哪一件都可能是
      // 线断了，而剩下几件还照样成立。
      expect([`${file} 交出了 ${fn}`, exportsFactory(file, fn)]).toEqual([`${file} 交出了 ${fn}`, true])
      expect([fn, wired(page, file, fn)]).toEqual([fn, true])
      expect([`${fn} 的返回值被接住`, assigned(page, fn)]).toEqual([`${fn} 的返回值被接住`, true])
      expect([`主文件自己又声明了一遍 ${fn}`, shadowed(page, fn)]).toEqual([`主文件自己又声明了一遍 ${fn}`, false])
    }
  })

  it('只要类型的那几处也在：`shared.ts` 被页面按类型引到了', () => {
    for (const [file, name] of TYPE_IMPORTS) {
      expect([`页面从 ${file} 引了类型 ${name}`, wiredType(page, file, name)]).toEqual([
        `页面从 ${file} 引了类型 ${name}`,
        true,
      ])
    }
  })

  it('那些职责真的搬走了：模块里在调，主文件里没有了', () => {
    for (const [file, fn] of MOVED) {
      expect([`${fn} 在 ${file} 里`, calls(code(raw(file)), fn)]).toEqual([`${fn} 在 ${file} 里`, true])
      expect([`${fn} 还在 ${PAGE} 里`, calls(page, fn)]).toEqual([`${fn} 还在 ${PAGE} 里`, false])
    }
  })

  it('对外还是同一个路由入口：props 和名字都还在这一页', () => {
    const props = page.match(/defineProps<\{([\s\S]*?)\}>\(\)/)?.[1] ?? ''
    // 名字、类型和可选性一起钉：四个 prop 怎么算填、怎么算漏，是路由给这一页定的对
    // 外契约，`taskId?: string` 写成必填会打退调用方，光查名字查不出来。
    const contract: readonly [string, RegExp][] = [
      ['projectId', /\bprojectId\s*:\s*string\b/],
      ['topicId', /\btopicId\s*:\s*string\b/],
      ['taskId', /\btaskId\s*\?:\s*string\b/],
      ['threadId', /\bthreadId\s*\?:\s*string\b/],
    ]
    expect(contract.filter(([, re]) => !re.test(props)).map(([name]) => name)).toEqual([])
    // 一个不多：多一个 prop，路由那边没有第五个键给它，收到的永远是 undefined。
    // 上面那张表只管「这四个还在不在」，多出来的它看不见。带引号的键（`'data-x': string`）
    // 也是 prop，所以引号的两种写法都算进名单里。
    const declared = [...props.matchAll(/(?:^|[\s;{])(?:([A-Za-z_$][\w$]*)|'([^']+)'|"([^"]+)")\s*\??\s*:/g)].map(
      (m) => m[1] ?? m[2] ?? m[3]!
    )
    expect(declared).toEqual(['projectId', 'topicId', 'taskId', 'threadId'])
    expect(page).toMatch(/defineOptions\(\{\s*name:\s*'TopicView'\s*\}\)/)
    expect(page).toContain('<template>')
    // 支线那一半仍然是这里异步取的：它是这一页模板里画的，不是搬走的那几块之一。
    expect(page).toMatch(/defineAsyncComponent\(\(\)\s*=>\s*import\('@\/views\/workspace\/ThreadPane\.vue'\)\)/)
  })
})
