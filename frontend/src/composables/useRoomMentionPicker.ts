/** 输入框里的 `@` 补全：这个房间的人、话题、群播，以及项目的资料库。
 *
 * 从 `components/room/RoomComposer.vue` 里搬出来的（#2143），一行没改。搬出来是
 * 因为它**自己去拉资料库**——那一步不该发生在 `src/components` 下
 * （`.claude/rules/architecture.md`：件只管画，取数在页面或 composable 里）。
 *
 * 两级菜单不是装饰：一个项目的文件会比房间里的人多得多，平铺进候选等于把「@ 一个
 * 人」这件事挤掉。所以没打字的时候资料库只是一行入口（`category`），走进去才列
 * 文件；打了字就不分级了——那时候人要的是搜索，人、话题、文件一起找。
 *
 * 这里不认识「发消息」，也不认识「叫不叫芝士」：它只回答「现在有哪些候选、高亮的
 * 是哪一项、挑中一项之后正文变成什么样」。
 */
import type { Ref } from 'vue'
import type { LibraryFile } from '../api'
import type { Topic } from '../cx_types'

import { computed, nextTick, ref, watch } from 'vue'

import { listProjectLibrary } from '../api'
import { t } from '../i18n'
import { IMAGE_SUFFIXES, suffixOf } from '../lib/fileKind'

/** 能被 @ 到的人：这个房间里的，加上项目里还没进这个房间的。 */
export interface MentionPoolEntry {
  handle: string
  label: string
  /**
   * 这个人**自己挑过的**头像地址；没挑过、或名册上没他时是 null（画彩色首字母）。
   * AI 队友没有这一步，一律画 CheeseAvatar，这里是 null。
   */
  avatar: string | null
  agent: boolean
  external?: boolean
  /** 项目里的人，但没加入这个频道：@ 得到，排在频道里的人后面。 */
  outsideTopic?: boolean
  /** 在这个话题名册上的角色（owner / admin / member）；不在名册上的人没有。 */
  role?: string
}

export interface MentionItem {
  label: string
  kind: 'member' | 'topic' | 'broadcast' | 'file' | 'category'
  /** 挑中之后写在 `@` 后面的那串字（一个 handle / 名字 / token）。 */
  insert: string
  /** 第二行：人的 `@handle`、话题的状态、群播的说明。 */
  sub: string
  agent: boolean
  /** 团队以外、被邀请进这个项目的人：候选里挂「外部」，@ 之前就知道他不是自己人。 */
  external?: boolean
  /** 没加入这个频道的人：排在频道里的人后面。 */
  outsideTopic?: boolean
  /** 二级菜单里这一项属于哪一组（同一组的标题只画一次）。 */
  group?: string
  /** 人的 handle：头像的底色按它算，和时间线上这个人的头像同一个颜色。 */
  handle?: string
  /** 人自己挑过的头像地址；没挑过是 null（画彩色首字母）。见 `MentionPoolEntry.avatar`。 */
  avatar?: string | null
}

// 群播 (fusion-design §3): @all/@here are FIXED-LITERAL tokens (rule 4), pinned
// at the top. expandMentions turns them into <@all>/<@here>. Built per call so
// the labels follow the current language.
const broadcastItems = (): MentionItem[] => [
  {
    label: t('work.room.mention.all'),
    kind: 'broadcast',
    insert: 'all',
    sub: t('work.room.mention.allSub'),
    agent: false,
  },
  {
    label: t('work.room.mention.here'),
    kind: 'broadcast',
    insert: 'here',
    sub: t('work.room.mention.hereSub'),
    agent: false,
  },
]

/** 这一格里「算不算图片」比预览域宽：gif / webp 浏览器也画得出来，而这里只是分组。 */
const PICKER_IMAGE_SUFFIXES = new Set([...IMAGE_SUFFIXES, 'gif', 'webp'])

export interface MentionPickerDeps {
  /** 正文。挑中一项之后由这里改它。 */
  draft: Ref<string>
  topic: () => Topic | null
  mentionPool: () => MentionPoolEntry[]
  topicList: () => Topic[]
  /** 挑完人、点完按钮之后要把光标还给输入框——这是最烦人的一处。 */
  focus: () => void
  /** 挑中的是一份文件：它不是一个能 @ 的人，是附在这条消息上的一样东西。 */
  onAddLibraryFile: (path: string) => void
  /** 用键盘换高亮之后把那一项滚进视野（菜单自己有高度上限、自己滚）。 */
  scrollActiveIntoView: () => void
}

export function useRoomMentionPicker(deps: MentionPickerDeps) {
  // @-autocomplete (§3.1.1 人也能 @): the @token being typed at the end of the
  // draft, and the teammates / topics / broadcast tokens it can complete to.
  // Mirrors TopicView's composer so the root-topic and 私聊 composers get the
  // same picker.
  const query = computed(() => {
    const m = deps.draft.value.match(/@([^\s@]*)$/)
    return m ? m[1] : null
  })

  // 资料库：项目给进来的文件，@ 一下就能带上这条消息。按需拉一次——打开一个房间的
  // 人不一定要引用文件，而打了 @ 的人正要挑东西。
  const libraryFiles = ref<LibraryFile[]>([])
  const libraryFor = ref<string | null>(null)
  // 菜单在第几级。没打字的时候资料库只是一行入口（`category`）：一个项目的文件会比
  // 房间里的人多得多，平铺进来等于把「@ 一个人」这件事挤掉。打了字就不分级了——那时
  // 人要的是搜索，人、话题、文件一起找。
  const level = ref<'root' | 'library'>('root')

  function libraryItems(ql: string): MentionItem[] {
    const rows = libraryFiles.value.filter((f) => f.path.toLowerCase().includes(ql))
    const item = (f: LibraryFile, group: string): MentionItem => ({
      label: f.path,
      kind: 'file',
      insert: f.path,
      sub: group,
      agent: false,
      group,
    })
    return [
      ...rows
        .filter((f) => !PICKER_IMAGE_SUFFIXES.has(suffixOf(f.path)))
        .map((f) => item(f, t('work.room.mention.fileGroup'))),
      ...rows
        .filter((f) => PICKER_IMAGE_SUFFIXES.has(suffixOf(f.path)))
        .map((f) => item(f, t('work.room.mention.imageGroup'))),
    ]
  }

  async function loadLibrary() {
    const projectId = deps.topic()?.project_id
    if (!projectId || libraryFor.value === projectId) return
    libraryFor.value = projectId
    try {
      libraryFiles.value = (await listProjectLibrary(projectId)).data
    } catch {
      // 挑文件是输入栏里的一个便利，不是这条消息发不出去的理由。
      libraryFiles.value = []
      libraryFor.value = null
    }
  }

  watch(
    () => deps.topic()?.project_id,
    () => {
      libraryFiles.value = []
      libraryFor.value = null
    }
  )
  watch(
    () => query.value !== null,
    (open) => {
      if (open) void loadLibrary()
      // 菜单关了就回到一级：下一次打 @ 的人不该落在上一次翻到的地方。
      else level.value = 'root'
    },
    // immediate: 草稿是恢复出来的（上次在这个房间打了一半的 @），输入区建起来的
    // 那一刻菜单就已经是开着的。只等「变成开着」的话，这一次永远等不到，菜单开着
    // 却一份文件都列不出来。
    { immediate: true }
  )

  const matches = computed<MentionItem[]>(() => {
    const q = query.value
    if (q === null) return []
    const ql = q.toLowerCase()
    if (level.value === 'library') return libraryItems(ql).slice(0, 12)
    const broadcast = broadcastItems().filter((b) => b.insert.startsWith(ql) || b.label.includes(q))
    const named: MentionItem[] = [
      ...deps.mentionPool().map((m) => ({
        label: m.label,
        kind: 'member' as const,
        insert: m.label,
        sub: m.handle,
        agent: m.agent,
        external: !!m.external,
        outsideTopic: !!m.outsideTopic,
        handle: m.handle,
        avatar: m.avatar,
      })),
      ...deps
        .topicList()
        .filter((tp) => tp.kind !== 'root')
        .map((tp) => ({
          label: tp.title,
          kind: 'topic' as const,
          insert: tp.title,
          sub: tp.status === 'archived' ? t('work.room.mention.archived') : t('work.room.mention.inProgress'),
          agent: false,
        })),
      // 人和话题都按**名字**搜；人还多认一个 handle（`sub` 里那个 `@xxx`）——有人记
      // 得住 `@cheese-topica`，记不住中文名叫什么，只按名字搜等于他默写一遍也找不到。
      // 群播是 fixed-literal token，它的 insert（`all` / `here`）在别处单独匹配。
    ].filter((i: MentionItem) => i.label.toLowerCase().includes(ql) || !!i.handle?.toLowerCase().includes(ql))
    // Agent 排在最前，群播让位。第一格就是 Enter 的默认答案，而「打一个 @ 然后回
    // 车」在这个产品里压倒性地是「交给芝士」——把 @all 摆在那个位置，等于让最常见
    // 的一次输入默认去打扰整个话题的所有人。群播是 fixed-literal token，换个位置
    // 它还是那两个 token。
    const agents = named.filter((i) => i.agent)
    // 频道里的人在前，没加入的人跟在后面（和 Slack 一样）：在这里说话的多半是在找
    // 频道里的人。
    const rest = [
      ...named.filter((i) => !i.agent && !i.outsideTopic),
      ...named.filter((i) => !i.agent && i.outsideTopic),
    ]
    // 没打字：资料库是一行入口。打了字：文件和人、话题一起被搜出来。
    const files = ql ? libraryItems(ql) : []
    const library: MentionItem[] =
      !ql && libraryFiles.value.length
        ? [
            {
              label: t('work.room.mention.library'),
              kind: 'category',
              insert: 'library',
              sub: t('work.room.mention.fileCount', { count: libraryFiles.value.length }),
              agent: false,
            },
          ]
        : []
    return [...agents, ...library, ...broadcast, ...rest, ...files].slice(0, 7)
  })

  function pick(item: MentionItem) {
    if (item.kind === 'category') {
      level.value = 'library'
      void nextTick(deps.focus)
      return
    }
    if (item.kind === 'file') {
      // 文件不是一个能 @ 的人：挑中它是把它附在这条消息上，所以那个 @ 连同半个
      // 名字都从正文里拿掉，文件去待发条里待着。
      deps.draft.value = deps.draft.value.replace(/@([^\s@]*)$/, '')
      deps.onAddLibraryFile(item.insert)
      void nextTick(deps.focus)
      return
    }
    deps.draft.value = deps.draft.value.replace(/@([^\s@]*)$/, `@${item.insert} `)
    // 挑完一个人，正是你要接着往下打字的时刻。鼠标点菜单会把焦点带到那颗按钮上，
    // 键盘挑则让整块菜单从 DOM 里消失——两条路都可能把光标从输入框里带走，而「@
    // 完人还要再点一次输入框」是这个面板最烦人的地方。
    void nextTick(deps.focus)
  }

  // @ 菜单里的键盘导航。鼠标划过和 ↑/↓ 改的是同一个下标——回车挑的就是它。分开存
  // 只会得到「鼠标指着第三个人、回车却挑了第一个」，而两条路谁也看不见对方存了什么。
  const index = ref(0)
  // Esc 收起候选（@ 还留在正文里，那只是一次临时收起）。再打一个字它就重新打开。
  const closed = ref(false)
  // 菜单画不画：`@` 还留在正文里，也没被 Esc 收起。**没有候选时它不消失**，而是说
  // 一句「暂无匹配」——整块收起来看起来像那个 `@` 没生效，人只会以为自己打错了。
  const menuVisible = computed(() => query.value !== null && !closed.value)
  // 菜单里有东西可挑。键盘只在这个前提下接管 ↑/↓ 和回车：空态里按 ↑/↓ 该去挪光标，
  // 回车该把这条发出去（`@` 那半截字还在正文里，发出去就是一次没 @ 到的普通发言）。
  const menuOpen = computed(() => menuVisible.value && matches.value.length > 0)
  // 读的时候夹一下：候选会自己变短（名册更新、资料库到货），下标不该指着一条已经不在
  // 列表里的项——那样回车一条也挑不动。
  const activeIndex = computed(() => Math.min(index.value, Math.max(0, matches.value.length - 1)))
  // 查询词变了、或者进出一趟资料库，高亮都回到第一项：眼前是一份刚过滤出来的新名单，
  // 旧下标指的是另一个人。收起状态也跟着这两件事复位——打字就是在重新打开它。
  watch(query, () => {
    index.value = 0
    closed.value = false
  })
  watch(level, () => {
    index.value = 0
  })

  function move(delta: number) {
    const n = matches.value.length
    if (!n) return
    index.value = (activeIndex.value + delta + n) % n
    void nextTick(deps.scrollActiveIntoView)
  }

  /** Esc 收起候选（`@` 还留在正文里，那只是一次临时收起）。 */
  function close() {
    closed.value = true
  }

  /** 从资料库退回一级：Esc、← 、查询为空时的退格，和二级菜单头上那颗 ‹。 */
  function backToRoot() {
    level.value = 'root'
    // 点 ‹ 会把焦点带到那颗按钮上；退回来之后人要接着挑，光标得回输入框。
    void nextTick(deps.focus)
  }

  /** 回车挑的是高亮那一项；菜单收起了（Esc）返回 false，把这次回车还给「发送」。 */
  function pickActive(): boolean {
    const item = menuOpen.value ? matches.value[activeIndex.value] : undefined
    if (!item) return false
    pick(item)
    return true
  }

  /** 鼠标划过某一项：和 ↑/↓ 改的是同一个下标。 */
  function hover(i: number) {
    index.value = i
  }

  return {
    query,
    matches,
    level,
    menuVisible,
    menuOpen,
    activeIndex,
    pick,
    move,
    pickActive,
    close,
    backToRoot,
    hover,
  }
}
