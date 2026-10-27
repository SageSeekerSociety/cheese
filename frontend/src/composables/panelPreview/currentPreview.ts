// 「预览」这一格看的房间里当前的那一件：芝士最后摆出来的那一样。问它换没换过、这一帧的
// 实例有没有被换掉，然后签一张授权把它挂上屏幕。
//
// 这一层管「拿到什么」：当前预览是哪一件、它读成的字节是什么、授权签下来没有。用哪种查看器
// 画、空态写哪句话、全屏按钮在不在，是画的那一半的事（判据都在递下去的 props 里）。
import type { PreviewInfo } from '../../cx_types'
import type { PreviewState } from './previewState'
import type { PanelPreviewOptions, PanelPreviewProps, PreviewFrames } from './types'

import { readPreviewFile, requestPreviewSession } from '../../api'
import { APP_NAVIGATION_BUDGET_MS } from '../usePreviewFrames'

import { t } from '@/i18n'
import { MCP_APP_MIME } from '@/lib/mcpAppBridge'
import { readPreview } from '@/query/room'

export interface CurrentPreviewDeps {
  props: PanelPreviewProps
  options: PanelPreviewOptions
  /** 这一格摆着的那一份：元数据、字节和授权的结果都写在这上面。 */
  state: PreviewState
  /** 帧的托管：授权表投进哪一帧、帧报到什么状态，都在它那儿。 */
  frames: PreviewFrames
  /** 指定了文件的那一格走另一条路（`namedFile.ts`）。 */
  loadNamed: (path: string, opts: { silent?: boolean; reload?: boolean }) => Promise<void>
}

/** 房间里当前那一件产物的取数：问元数据、签授权、挂上屏幕。 */
export function useCurrentPreview(deps: CurrentPreviewDeps) {
  const { props, options, state, frames: host, loadNamed } = deps

  // 一秒内刚问过的指针算数，再旧就现问。比最快那一档轮询（2 秒）短：断线时按 2 秒问的那
  // 几次每一次都得是真的问。
  const SHARED_POINTER_MS = 1_000
  // 换实例后跟上去最多试多久（毫秒）：应用冷启动、授权一时被拒都在这段时间里重试，过了就
  // 认输，把「刷新」那条路还给用户——不能一直无声地跟。
  const FOLLOW_WINDOW_MS = 60_000
  // 第一次看到「屏幕上这一帧的实例被换掉」的时刻；不在替换状态时为 null。
  let followSince: number | null = null

  // 一次刷新正在跑时又有人要（收工、指针换了）：不丢，等这一次跑完再问一次。定时轮询不算：
  // 下一次轮询本来就会来。
  let rereadWanted = false

  /** 跟上去的那个窗口重新计时：这一格下去了、换话题了、或者换了产物。 */
  function resetFollow() {
    followSince = null
  }

  async function load(opts: { silent?: boolean; reload?: boolean; poll?: boolean } = {}) {
    if (props.path) return loadNamed(props.path, opts)
    // Metadata polling must not cancel an explicit refresh's pending grant.
    if (opts.silent && !opts.reload && (state.loading.value || state.refreshing.value)) {
      if (!opts.poll) rereadWanted = true
      return
    }
    const tid = props.topicId
    const pid = props.projectId
    if (!tid || !pid) return
    const current = state.begin()
    const stillCurrent = () => state.stillCurrent(current)
    if (opts.silent) state.refreshing.value = true
    else state.loading.value = true
    try {
      let art: PreviewInfo | null
      try {
        // 正在问的那一次（路由守卫替这个房间先起的头、面板的指针轮询）还没回来就等它
        // （`query/room`）。首屏和定时轮询还认几秒内刚问到的那一份：别把又一轮网络压在
        // 「面板挂载之后」的临界路径上，两处轮询同时开着时也只问一次。收工重取、点了
        // 「重新载入」要的是此刻的，现问。
        const recent = (opts.poll || !opts.silent) && !opts.reload
        art = await readPreview(tid, recent ? SHARED_POINTER_MS : 0)
      } catch (e) {
        if (!stillCurrent()) return
        state.previewUrl.value = null
        state.previewError.value = e instanceof Error ? e.message : t('work.room.preview.loadFailedShort')
        return
      }
      if (!stillCurrent()) return
      state.previewError.value = null
      state.previewReadError.value = null
      state.previewNamed.value = !!art
      state.previewNamedPath.value = art?.path ?? ''
      options.onLoaded?.(art?.artifact_id ?? null)
      if (!art) {
        host.reset()
        state.previewUrl.value = null
        state.previewFile.value = null
        state.previewAppNote.value = ''
        return
      }
      const identity = JSON.stringify([
        tid,
        art.kind ?? 'file',
        art.artifact_id ?? art.path,
        art.url,
        art.version ?? null,
        art.instance ?? null,
      ])
      const known = art.kind === 'app' || art.kind === 'mcp-app' || !!art.version
      const unchanged =
        known &&
        (host.incoming.value?.identity === identity ||
          (host.displayed.value?.identity === identity && host.navigation.value !== 'failed'))
      state.previewAppNote.value = art.kind === 'app' ? art.path : ''
      state.previewTunnelUp.value = !!art.tunnel_up
      // 屏幕上这一帧被换掉了实例、或者断线后又回来了：不能只把状态画出来——旧那一帧的请求
      // 可能已经被拒了。换实例就跟上去：`observeConnection` 在替换期间每一轮都报，所以一次
      // 跟丢（授权 404、应用还在冷启动）还能在后面的轮询里再试，试到 FOLLOW_WINDOW_MS 为止；
      // 已经有一帧在往新实例上装时不踩它。
      //
      // 同一实例断线又回来通常不用动——应用没死，页面自己会接上（HMR 重连后客户端自己重载），
      // 替它重载反而抹掉表单和 SPA 状态（#2349 避开的正是这个）。只有这一帧的导航真的失败过
      // （从没装上、或加载超时），回来时才替它重来一次。
      const change = art.kind === 'app' ? host.observeConnection(art.instance, !!art.url && !!art.tunnel_up) : null
      let follow = false
      if (art.kind === 'app') {
        const displayed = host.displayed.value
        const sameArtifact =
          !!displayed?.identity && JSON.parse(displayed.identity)[2] === (art.artifact_id ?? art.path)
        const replaced = change === 'instance-changed' && sameArtifact
        // 已经有一帧在往新实例上装（授权或导航中）：这一程还没成也没败，别踩掉它。
        const pending =
          host.incoming.value?.identity === identity &&
          (host.navigation.value === 'authorizing' || host.navigation.value === 'navigating')
        // 手动刷新（opts.reload）是用户明确的动作：无论跟了多久都重来一次，并且把窗口重新
        // 计时。不这样做的话，那句「认输」会把刷新按钮也一起堵死——冷启动起过一分钟是常事，
        // 堵死了就再没有别的出路。
        if (!replaced || opts.reload) followSince = null
        else if (followSince === null) followSince = Date.now()
        const expired = replaced && !opts.reload && followSince !== null && Date.now() - followSince >= FOLLOW_WINDOW_MS
        if (expired && !pending) {
          // 跟了一分钟还没跟上：应用一直没起来，或授权一直被拒。别再无声白等，把「刷新」那条
          // 路还给用户（这句报错下面自带一个「重试」按钮）。第一次报过之后就别每轮重设，免得
          // 把那句提示反复刷掉；也不动 previewUrl，免得同一条报错里又冒出一句「应用不可用」，
          // 和「刷新以打开当前实例」自相矛盾。
          if (host.error.value !== t('work.room.preview.instanceGoneManual')) {
            host.authorize(identity)
            host.fail(t('work.room.preview.instanceGoneManual'))
          }
          return
        }
        // 跟上去，并说一句让人看见（`replaced`、且看的是同一件产物时，下面两处「还是同一件」
        // 的近路都要让开；换了产物那本来就是一次正常的换页，不该说成「应用重启」）。同实例断线
        // 又回来时，只有屏幕上这一帧自己都没装成、才替它重来：看的是它自己的导航结果，不是宿主
        // 那一层的 navigation——一次「跟到新实例去」的失败不该算到还健康的旧帧头上。
        follow =
          (replaced && !pending) ||
          (change === 'instance-recovered' && sameArtifact && displayed?.navigationFailed === true)
        if (replaced && !pending) state.autoReloaded.value = true
        if (!opts.reload && !follow && displayed?.live && displayed.instance && sameArtifact) {
          return
        }
        state.previewFile.value = null
        if (!art.url || !art.tunnel_up) {
          state.previewUrl.value = null
          host.authorize(identity)
          host.fail(t('tasks.preview.unavailable'))
          return
        }
      } else if (art.kind === 'mcp-app') {
        // 第三方 View：没有房间文件可读。字节在远端服务器上，由内容域按自己的策略取；
        // 这一层只记下它是哪一种资源。
        state.previewMime.value = art.mime || MCP_APP_MIME
      } else {
        state.previewMime.value = art.mime || 'text/html'
        try {
          const content = await readPreviewFile(tid)
          if (!stillCurrent()) return
          state.previewFile.value = content
        } catch (e) {
          if (!stillCurrent()) return
          state.previewUrl.value = null
          state.previewFile.value = null
          state.previewReadError.value = e instanceof Error ? e.message : t('work.room.preview.fileReadFailed')
          return
        }
        if (!stillCurrent()) return
        // 两种「读不到文本」的情形分开走：
        // - 图片：它的内容本来就是字节，null 是正常的，交给 iframe 直接显示，
        //   否则会掉进下面那句「这个文件不是文本」——预览域本身是拿 image/*
        //   把这些字节发出来的，浏览器画得出来。
        // - 其它二进制（docx/xlsx 走 documentType 那份分支，这里指没认出来的）：
        //   没有 iframe 能显示它，停下。
        if (
          state.previewFile.value.content === null &&
          !state.previewFile.value.too_large &&
          !state.isImageArtifact.value
        ) {
          host.reset()
          state.previewUrl.value = null
          return
        }
        // 认得出的文档类型一律由查看器画，不进 iframe。Markdown 是因为预览域按
        // text/markdown 发出来浏览器只显示源码；其余是因为「读得成文本」不代表它是
        // 网页：一份字节其实是文字的 .pdf 会被按 application/pdf 发进沙箱 iframe，
        // 浏览器画不出也不报错，面板就是一片空白。交给查看器，它会说清楚。
        if (state.documentType.value || state.isVideoArtifact.value) {
          host.reset()
          state.previewUrl.value = null
          return
        }
      }
      if (!opts.reload && !follow && (unchanged || (opts.silent && host.failedIdentity.value === identity))) return
      if (!art.url) {
        state.previewUrl.value = null
        state.previewError.value = t('work.room.preview.urlUnavailable')
        return
      }
      try {
        host.authorize(identity)
        const session = await requestPreviewSession(
          tid,
          art.kind === 'app'
            ? art.instance
              ? { artifact_id: art.artifact_id, instance: art.instance }
              : undefined
            : art.kind === 'mcp-app'
              ? { artifact_id: art.artifact_id }
              : { artifact_id: art.artifact_id, version: art.version, path: art.path }
        )
        if (!stillCurrent()) return
        state.previewUrl.value = art.url
        await host.navigate(
          session,
          {
            url: art.url,
            label: art.path,
            mime: art.mime || (art.kind === 'mcp-app' ? MCP_APP_MIME : 'text/html'),
            version: art.version ?? null,
            live: art.kind === 'app',
            identity,
            // 应用可能正赶上机器冷启动，那 30 秒的默认档会把它误报成超时。
            budgetMs: art.kind === 'app' ? APP_NAVIGATION_BUDGET_MS : undefined,
          },
          stillCurrent
        )
      } catch (e) {
        if (!stillCurrent()) return
        if (!host.displayed.value) state.previewUrl.value = null
        state.previewError.value = e instanceof Error ? e.message : t('work.room.preview.authFailed')
        host.fail(state.previewError.value)
      }
    } finally {
      if (stillCurrent()) {
        state.loading.value = false
        state.refreshing.value = false
        if (rereadWanted) {
          rereadWanted = false
          void load({ silent: true })
        }
      }
    }
  }

  return { load, resetFollow }
}
