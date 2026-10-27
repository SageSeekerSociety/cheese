import type { Ref } from 'vue'
import type { RememberedPlace, StoredLayout } from './types'

import { ref } from 'vue'

import { storageGetJson, storageSetJson } from '@/lib/browserStorage'

// 当前打开的是哪个项目，和围着它的界面状态（栏宽、上次待的房间、为什么打不开）。
const LAYOUT_KEY = 'cheesex.layout'

function loadLayout(): StoredLayout {
  const saved = storageGetJson<unknown>(LAYOUT_KEY)
  return typeof saved === 'object' && saved !== null ? (saved as StoredLayout) : {}
}

// 存进来的这份是整个浏览器一份，可能是老版本写的、也可能被人手改过：只留下说得通
// 的那些，别的当没记过。老版本记的是 `{kind, id, room}`（只认任务和频道），读不出来
// 就是没记过——回落项目首页，不会把人送进一个拼错的地址。
function validPlaceMap(value: unknown): Record<string, RememberedPlace> {
  if (typeof value !== 'object' || value === null) return {}
  const places: Record<string, RememberedPlace> = {}
  for (const [projectKey, raw] of Object.entries(value as Record<string, unknown>)) {
    if (typeof raw !== 'object' || raw === null) continue
    const { path, room } = raw as { path?: unknown; room?: unknown }
    if (typeof path !== 'string' || path.startsWith('/')) continue
    if (room !== null && room !== undefined && typeof room !== 'string') continue
    places[projectKey] = { path, room: typeof room === 'string' ? room : null }
  }
  return places
}

const clampNum = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v))

const WIDE_SCREEN = 1440

function defaultChatPct(): number {
  return typeof window !== 'undefined' && window.innerWidth >= WIDE_SCREEN ? 45 : 50
}

// 手机底栏「工作区」那一格要在冷启动时就知道该落到哪个项目，那时 store 里还没有
// 打开过任何项目——所以这个值从存储里直接读，不经过 store 实例。
export function lastOpenedProjectId(): string | null {
  return loadLayout().lastProjectId ?? null
}

/**
 * 这个项目上次待的那一页；没记过就是 null。和 `lastOpenedProjectId` 一样从存储里
 * 直接读、不经过 store：手机把后台的页面收走后再打开，是从入口重新落一次，那一下
 * 就得知道该去哪儿（`router/home.ts` 的 `landingForMember`）。记着的那一页还在不在，
 * 由读这个项目的频道清单时校验（`queries.ts` 里 `forgetPlace` 那一条），这里只认
 * 存储里那份。
 */
export function lastPlaceOf(projectKey: string | null): RememberedPlace | null {
  if (!projectKey) return null
  return validPlaceMap(loadLayout().lastPlaceByProject)[projectKey] ?? null
}

/**
 * 这个人自己选过、要记住的界面偏好：栏宽、右侧面板的开合，和每个项目上次待的地方。
 *
 * 它要的只有「当前是哪个项目」——写回存储时 `lastProjectId` 记的是它，所以传进来的是
 * 那个 ref 本身，不是某一刻的值。
 */
export function useLayout(projectId: Ref<string | null>) {
  const stored = loadLayout()
  // 没拖过的人：窄桌面对半分，宽屏（≥ 1440px）把多出来的那块给预览，对话栏占 45%。
  // 拖过一次就记住他选的（`setChatPct`）。
  const chatPct = ref(typeof stored.chatPct === 'number' ? stored.chatPct : defaultChatPct())
  const panelPref = ref<boolean | null>(typeof stored.panelOpen === 'boolean' ? stored.panelOpen : null)
  // 每个项目上次待的地方。rail 的项目格子用它落回那里，而不是每次都落在项目首页
  //（每天都走的主路径不该多加一跳）。
  const lastPlaceByProject = ref<Record<string, RememberedPlace>>(validPlaceMap(stored.lastPlaceByProject))

  function persistLayout() {
    storageSetJson(LAYOUT_KEY, {
      chatPct: chatPct.value,
      panelOpen: panelPref.value ?? undefined,
      lastProjectId: projectId.value ?? undefined,
      lastPlaceByProject: lastPlaceByProject.value,
    })
  }

  function setChatPct(pct: number) {
    chatPct.value = clampNum(pct, 25, 80)
    persistLayout()
  }

  /** 双击分隔条：回到没拖过时的那一份（宽屏 45%，否则对半）。 */
  function resetChatPct() {
    setChatPct(defaultChatPct())
  }

  function setPanelPref(open: boolean) {
    panelPref.value = open
    persistLayout()
  }

  /**
   * 记住正待着的这一页是哪个项目的：回到项目时 rail 那一格落回它。
   *
   * 记的是地址（`tasks/318`、`overview`……），不是「任务」或「频道」那两种：项目里
   * 的每一页都算一个地方，只认那两种的话，从成员页、资料库离开再回来就掉回频道列表。
   */
  function rememberPlace(projectKey: string, place: RememberedPlace) {
    if (!projectKey) return
    const held = lastPlaceByProject.value[projectKey]
    if (held && held.path === place.path && held.room === place.room) return
    lastPlaceByProject.value = { ...lastPlaceByProject.value, [projectKey]: place }
    persistLayout()
  }

  /**
   * 这个项目上次待的地方；没记过就交回 null，落回项目首页。
   *
   * 「还在不在」在读到这个项目的频道清单时校验：记着的那件任务所在频道被删了、或被
   * 收成了别的房间，就不该再把人送进一个打不开的房间。
   */
  function placeFor(projectKey: string): RememberedPlace | null {
    return lastPlaceByProject.value[projectKey] ?? null
  }

  function forgetPlace(projectKey: string) {
    if (!(projectKey in lastPlaceByProject.value)) return
    const next = { ...lastPlaceByProject.value }
    delete next[projectKey]
    lastPlaceByProject.value = next
    persistLayout()
  }

  return {
    chatPct,
    panelPref,
    lastPlaceByProject,
    persistLayout,
    setChatPct,
    resetChatPct,
    setPanelPref,
    rememberPlace,
    placeFor,
    forgetPlace,
  }
}
