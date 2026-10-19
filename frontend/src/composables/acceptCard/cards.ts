// 成果待采纳框 (eval C5/A3) 里「卡从哪来」的那一半：这一话题现在有哪几张卡、其中
// 哪一张在台面上，以及卡上那张 PR 的检查读到哪了。
//
// 这个框拥有自己的数据（卡的清单、PR 检查的轮询），而不是把数据当 props 收进来：
// 这里每一件事都只关乎**这一个话题的这几张卡**，外面没有人要读它们。TopicView
// 只会说一句「重新拉一次」（`load`），它说这句的时候是芝士递了卡、或者一条
// `cheese` 命令在中途改了卡。
import type { AcceptCard, PrChecks } from '@/cx_types'

import { computed, nextTick, onUnmounted, ref, watch } from 'vue'

import { getAcceptCards, getPrChecks } from '@/api'
import { reconcile } from '@/lib/reconcile'

/** 读哪个话题的卡：话题自己，以及它下面那件任务（卡只递在任务上）。 */
export interface AcceptCardsHost {
  /** 读成函数：话题和任务都会换，读的时候才取值。 */
  topicId: () => string
  taskId: () => string | null | undefined
}

export function useAcceptCards(props: AcceptCardsHost) {
  const acceptCards = ref<AcceptCard[]>([])
  // This topic's cards are in. Distinguishes 「没有卡」 from 「还没问过」 for anyone
  // reading the phase from outside.
  const loaded = ref(false)
  // 卡的出现和收走演不演。打开房间 / 切话题那一次读到的不演（那张卡本来就在），读完
  // 之后再变的才演。
  const animate = ref(false)

  // Newest pending card (the list comes newest-first). A card in `conflict`
  // (采纳时合并冲突，芝士被派去解决) keeps the merge box up — as a STATE, with a
  // retry button — instead of pretending the accept went through.
  const pendingCard = computed<AcceptCard | null>(
    () => acceptCards.value.find((c) => c.status === 'pending' || c.status === 'conflict') ?? null
  )
  // The accepted card on an archived topic — its presence lets us offer 撤回采纳.
  const acceptedCard = computed<AcceptCard | null>(() => acceptCards.value.find((c) => c.status === 'accepted') ?? null)
  // 已采纳等合并 (`pr_open`, #718 退役): 历史状态。采纳现在当场合并，什么都不再
  // 写这个状态，存量卡也已迁回 pending —— 这张脸和闸门那两张一样，只为库里的
  // 极端残留兜底，只读。
  const deliveringCard = computed<AcceptCard | null>(
    () => acceptCards.value.find((c) => c.status === 'pr_open') ?? null
  )
  // 机器闸门 (eval C2, 已退役): a card left in a gate state by the mechanism that
  // used to run the project's check before the card reached its reviewer. Only the
  // newest card can carry one (one live card per topic is enforced server-side).
  const gateCard = computed<AcceptCard | null>(() => {
    const c = acceptCards.value[0]
    return c && (c.status === 'gate_failed' || c.status === 'gate_blocked') ? c : null
  })

  // 后发的请求先回来、先发的后回来时，晚到的那份是旧的，不许盖掉新的。
  let requested = 0
  let applied = 0

  // `alongside`：和卡一起变的另一份数据（话题状态）。等它也回来再换卡：采纳之后卡先
  // 变成 accepted、话题还没归档的那一拍，框会先收走再长回来。
  async function load(alongside?: Promise<unknown>) {
    const tid = props.topicId()
    const task = props.taskId()
    if (!tid) return
    // 卡只递在任务上，频道自己没有卡。按频道问回来的是它下面所有任务的卡（一个热闹的
    // 频道两三兆），读来也全要扔掉，所以不读，直接答「没有」。
    if (!task) {
      acceptCards.value = []
      loaded.value = true
      return
    }
    const seq = ++requested
    try {
      const [payload] = await Promise.all([getAcceptCards(task), alongside?.catch(() => undefined)])
      if (props.topicId() !== tid || props.taskId() !== task || seq < applied) return
      applied = seq
      const first = !loaded.value
      acceptCards.value = reconcile(acceptCards.value, payload.data)
      loaded.value = true
      // 这个话题第一次读到的卡本来就在，不演入场；之后再出现、再收走的才演。
      if (first) void nextTick(() => (animate.value = true))
    } catch {
      // Best-effort; the cards on screen stay as they were.
    }
  }

  // 换了话题（或任务）才清空，那时屏幕上的卡属于别处；其余每一次重读——轮询、点完
  // 一个按钮、宿主说「重新拉一次」——都把读回来的那份合到屏幕上那份上，卡一直在，
  // 没变的卡连对象都不换（lib/reconcile.ts）。以前是一个 silent 开关由调用方自己选，
  // 按钮那几处选了不静默，于是每点一下整张卡消失、再带着入场动画长回来。
  // 展开的小表单由调用方自己收（见 acceptCard/actions.ts 的 `resetForms`）。
  function reset() {
    animate.value = false
    acceptCards.value = []
    loaded.value = false
  }

  // 采纳 PR 化 (#188 §5.1): live CI state of the card's PR. Polled slowly while such
  // a card is on screen — checks take minutes, not seconds. Both the pending card
  // (人还没点) and the delivering one (点完了，CI 在跑) ride the same PR, and
  // /pr-checks answers for any card that has a pr_number.
  const prCheckCard = computed<AcceptCard | null>(() => pendingCard.value ?? deliveringCard.value)
  const prChecks = ref<PrChecks | null>(null)
  let prPollTimer: number | null = null
  async function loadPrChecks() {
    const tid = props.topicId()
    const task = props.taskId()
    if (!prCheckCard.value?.pr_number) return
    try {
      const payload = await getPrChecks(task ?? tid)
      if (props.topicId() === tid && props.taskId() === task) prChecks.value = payload
    } catch {
      // Best-effort; the PR row just shows the link without CI state.
    }
  }
  watch(
    () =>
      prCheckCard.value?.pr_number ? `${props.topicId()}:${props.taskId() ?? ''}:${prCheckCard.value.pr_number}` : null,
    (active) => {
      prChecks.value = null
      if (active) void loadPrChecks()
      if (active && prPollTimer === null) {
        prPollTimer = window.setInterval(() => {
          void loadPrChecks()
          // 卡本身也跟着刷——待采纳和交付中都要，而且理由是同一个：**这张卡是快照，
          // 而它描述的东西还在变**，界面不重新读就会停在它到达的那一刻。
          //
          // 交付中那一支变的是合并时间、note、最终 accepted。
          //
          // 待采纳那一支变的是 `merge_state`，而它决定采纳按钮亮不亮：递卡的一瞬间
          // PR 刚从草稿翻成待看，GitHub 还没算完能不能合，后端如实给 `unknown` ——
          // 不可采纳，注释里写着「下一轮读到真值自然收敛」（domain/review/
          // merge_state.py）。可这里原本没有下一轮，于是那颗按钮就一直灰着，验收人
          // 只能靠刷新页面或切一次话题才点得动。实测 #888：01:23 还是 unstable，
          // 01:24 已经 clean，后端确实在收敛，看不见的是界面。
          void load()
        }, 15000)
      } else if (!active && prPollTimer !== null) {
        window.clearInterval(prPollTimer)
        prPollTimer = null
      }
    },
    { immediate: true }
  )
  onUnmounted(() => {
    if (prPollTimer !== null) window.clearInterval(prPollTimer)
  })

  return { acceptCards, loaded, animate, pendingCard, acceptedCard, deliveringCard, gateCard, prChecks, load, reset }
}
