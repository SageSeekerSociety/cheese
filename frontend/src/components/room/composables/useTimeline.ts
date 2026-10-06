/**
 * 对话栏此刻显示时间线的哪一段，以及这一段怎么变。
 *
 * 平常显示的是**最新的一段**：最新的一页，往上翻时一页页补上更早的（`hasMore` 说
 * 上面还有没有）。从一条旧消息打开对话（搜索结果、被引用的那一条）时，显示的是
 * **历史中间的一段**，下面还有更新的（`hasNewer`）。这时最新的那一段并没有丢：它
 * 在背后照常收实时推来的块，所以「回到最新」不用再取一次，往下翻到和它接上时，两段
 * 合成一段，又回到平常的样子。
 *
 * 块从哪来——打开房间时读的历史、socket 推来的一帧、自己改完的那一条——是房间壳的事；
 * 这里只管它们怎么落进这两段：同一块不出现两次，改了的原地换掉，撤回的拿走。
 *
 * 取数、缓存、滚动都不在这里。
 */

import type { Block } from '../../../cx_types'
import type { BlockWindow } from '../../../lib/blockPaging'

import { ref } from 'vue'

import { capWindow, joinNewest, placeBlock, prependOlder } from '../../../lib/blockPaging'

/**
 * 新来的一块落在哪：显示出来了、收在背后的最新一段里、本来就有，还是比这一段更早、
 * 留给往上翻的那一页带回来（见 placeBlock）。
 */
export type Landing = 'shown' | 'held' | 'known' | 'above'

/**
 * `renders` 说一块落进这段时画不画得出来。房间里那些不露面的块（`in_room:false` 的
 * 事件、前端错误、不在白名单里的）画不出任何一行，却和消息一样占窗口额度：上限一满，
 * 它们会被当成「最新的一截」收进背后，让「回到最新」换上一屏什么都看不见的块，把最新
 * 那条看得见的消息挤丢。所以只把画得出来的块装进窗口，上限数的就是画得出来的那些。
 * 缺省是都画（站内转录等自己带块的地方不受影响）。
 */
export interface TimelineOptions {
  renders?: (block: Block) => boolean
}

export function useTimeline(options: TimelineOptions = {}) {
  const renders = options.renders ?? (() => true)
  /** 显示着的块，从旧到新。 */
  const messages = ref<Block[]>([])
  /** 显示的这一段上面还有更早的块。 */
  const hasMore = ref(false)
  /** 显示的这一段停在历史中间，下面还有更新的块。 */
  const hasNewer = ref(false)
  /** 停在中间时，背后那段最新的。平常是 null：显示的就是它。 */
  let newestHeld: BlockWindow | null = null
  /**
   * 窗口装进来过的最老那一块（**原始的**，不露面的也算）——往上翻时的游标。
   *
   * 不能拿 `messages[0]` 当游标：不露面的块不进窗口（见 renders），最新那一页整页不
   * 露面时窗口里一条都没有，游标也跟着消失，往上翻第一步就迈不出去（房间开出来是空的）。
   * 游标认的是「读到哪了」，和「画得出来什么」是两件事，所以单独记。
   */
  let oldestId: string | null = null

  /** 整个换成这一段最新的。 */
  function show(window: BlockWindow) {
    newestHeld = null
    hasNewer.value = false
    messages.value = window.blocks.filter(renders)
    hasMore.value = window.hasMore
    oldestId = window.blocks[0]?.id ?? null
  }

  /** 此刻显示的这一段。 */
  function current(): BlockWindow {
    return { blocks: messages.value, hasMore: hasMore.value }
  }

  /** 最新的那一段，存进缓存用：停在中间时是背后那段，缓存里只放最新的。 */
  function newest(): BlockWindow {
    return newestHeld ?? current()
  }

  function find(id: string): Block | undefined {
    return messages.value.find((m) => m.id === id)
  }

  /** 新来的一块按时间落进最新一段。停在中间时它收在背后，不显示。 */
  function append(block: Block): Landing {
    if (!renders(block)) return 'known'
    if (newestHeld) {
      if (newestHeld.blocks.some((m) => m.id === block.id)) return 'known'
      const placed = placeBlock(newestHeld, block)
      if (!placed) return 'above'
      newestHeld = { ...newestHeld, blocks: placed }
      return 'held'
    }
    if (messages.value.some((m) => m.id === block.id)) return 'known'
    const last = messages.value.at(-1)
    // 平常新来的都比末尾那块新：原地接上，不换掉整个数组。
    if (!last || Date.parse(block.created_at) >= Date.parse(last.created_at)) {
      messages.value.push(block)
      // 整页不露面、窗口空着那阵子来了条新消息：它就成了窗口里最老的一条。
      if (oldestId === null) oldestId = block.id
      return 'shown'
    }
    const placed = placeBlock(current(), block)
    if (!placed) return 'above'
    messages.value = placed
    return 'shown'
  }

  /** 一块变了：两段里有它的地方都原地换掉。只有它的时间也变了（例行任务跑完，
   *  那条消息挪到跑完的那一刻）才按新时间重新落位，落法和新来的一块一样；
   *  不在窗口里、又比窗口里最新的还新的，就当新来的一块接上。 */
  function replace(block: Block): Landing | null {
    const shown = messages.value.find((m) => m.id === block.id)
    const heldBlock = newestHeld?.blocks.find((m) => m.id === block.id)
    const before = shown ?? heldBlock
    if (before && before.created_at !== block.created_at) {
      remove(block.id)
      return append(block)
    }
    if (!before) {
      const newest = newestHeld?.blocks.at(-1) ?? messages.value.at(-1)
      if (newest && Date.parse(block.created_at) > Date.parse(newest.created_at)) return append(block)
      return null
    }
    const at = messages.value.findIndex((m) => m.id === block.id)
    if (at >= 0) messages.value.splice(at, 1, block)
    const held = newestHeld?.blocks.findIndex((m) => m.id === block.id) ?? -1
    if (newestHeld && held >= 0) newestHeld.blocks.splice(held, 1, block)
    return null
  }

  function remove(id: string) {
    messages.value = messages.value.filter((m) => m.id !== id)
    if (newestHeld) newestHeld = { ...newestHeld, blocks: newestHeld.blocks.filter((m) => m.id !== id) }
  }

  /**
   * 往上翻到的那一页拼到显示的这一段顶上。回这一页**多画出来**了几行（`renders` 过掉、
   * 又不在窗口里的那些）：最新那一带几乎全是 `in_room:false` 的回合事件时，整页可能一行
   * 都画不出来（回 0），调用方据此知道该接着往回读——不然这一页没让任何东西长高，就没有
   * 下一次滚动事件，翻页停在那儿。见 composables/useChatPaging 的补窗。
   */
  function prepend(older: Block[], more: boolean): number {
    const before = messages.value.length
    const next = prependOlder(current(), older.filter(renders), more)
    messages.value = next.blocks
    hasMore.value = next.hasMore
    // 游标记这一页（原始的）最老那条：不露面的块进了窗口的只有前面那几个，但更早
    // 的块是在它们上面。拿窗口里最老的那条当游标会把不露面那一段反复问一遍。
    if (older.length) oldestId = older[0].id
    return next.blocks.length - before
  }

  /**
   * 显示的这一段涨过上限时，把**最新**的那一截从屏上挪到背后去。
   *
   * 挪走而不是删掉：新消息要有个地方落（`append` 收进 `newestHeld`，算「held」），
   * 「回到最新」要有东西可换（`backToNewest`），往下翻也要有东西可以接（`appendNewer`
   * 拿 after 游标接上它就合成一段）。于是「停在历史中间」那套原样生效，只是它是被
   * 裁出来的，不是从一条旧消息打开的。
   *
   * 单独一步、不在 `prepend` 里做：裁掉的行在视口下方，删掉不该动滚动位置，而
   * `prepend` 的滚动补偿量的是 scrollHeight 的差；两者同一次落进 DOM 的话补偿会少
   * 掉这一截，读的人每翻一页就被往上拽一下——正是这套窗口要避免的事。调用方在补偿
   * 之后再叫它。
   */
  function capNewest() {
    const capped = capWindow(messages.value)
    if (!capped) return
    messages.value = capped.keep
    // 停在中间（背后已经有一段最新的）时，裁下来的这一截夹在显示段和它之间，留着会
    // 让两段之间出现一个从没取过的洞，`joinNewest` 却以为接上了。丢掉即可：往下翻时
    // 用 after 游标还会再取回来。
    if (!newestHeld) newestHeld = { blocks: capped.dropped, hasMore: true }
    hasNewer.value = true
  }

  /**
   * 换成历史中间的一段（`around` 一条消息取回来的那一页）。它要是已经和最新的一段
   * 接上了，就直接合成一段最新的。
   */
  function showMiddle(middle: BlockWindow, reachedNewest: boolean) {
    const fresh: BlockWindow = { blocks: middle.blocks.filter(renders), hasMore: middle.hasMore }
    const held = newest()
    const joined = joinNewest(fresh, held, reachedNewest)
    if (joined) {
      show(joined)
      return
    }
    newestHeld = held
    messages.value = fresh.blocks
    hasMore.value = fresh.hasMore
    hasNewer.value = true
    oldestId = middle.blocks[0]?.id ?? null
  }

  /** 往下翻到的那一页接到显示的这一段末尾；接上最新的一段就合成一段。 */
  function appendNewer(page: Block[], reachedNewest: boolean) {
    if (!newestHeld) return
    const known = new Set(messages.value.map((m) => m.id))
    const grown = {
      blocks: [...messages.value, ...page.filter((m) => !known.has(m.id) && renders(m))],
      hasMore: hasMore.value,
    }
    const joined = joinNewest(grown, newestHeld, reachedNewest)
    if (joined) show(joined)
    else messages.value = grown.blocks
  }

  /** 回到最新：背后那段直接换上来，不用再取。 */
  function backToNewest() {
    if (newestHeld) show(newestHeld)
  }

  /** 窗口读到哪了：往上翻时拿它当 `before` 游标（原始的，不是画得出来的最老那条）。 */
  function oldestLoaded(): string | null {
    return oldestId
  }

  return {
    messages,
    hasMore,
    hasNewer,
    oldestLoaded,
    show,
    current,
    newest,
    find,
    append,
    replace,
    remove,
    prepend,
    capNewest,
    showMiddle,
    appendNewer,
    backToNewest,
  }
}
