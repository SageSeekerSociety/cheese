// 文档那一格的取数：正文、评论、节点，以及「什么时候、把哪一版写回去」。
//
// 和「改动」(#2130)、「预览」(#2158) 分家是同一个形状：
//   - 取数（正文的读写、评论、节点、自动保存的那只时钟、冲突与草稿的状态机）
//     → 这一层
//   - 画（编辑器和它的装饰、浮层、横条上写哪句话）
//     → components/panels/PanelDocView.vue 和它底下的 doc/DocSurface.vue，只凭 props 渲染
// 这一层不碰 DOM，也不认识编辑器：凡是要落在编辑器上的两件事都走 hooks —— 读它现在
// 是什么（`serializeVisual`），和把服务端那一版装进去（`installMarkdown`）。判据留在
// 这一层，编辑器留在画的那一半，两边各管各的。
//
// 军规 1（绝不静默丢内容）的那几条路都还在这里，原样搬过来：读到的正文与解析回来的
// 不一致就不自动保存、服务端和本地都动过就两个版本都留着、切模式带不过去的改动先存
// 进栈里再问人。
import type { Block, Topic } from '../cx_types'

import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useDisplay } from 'vuetify'

import { addComment, ApiError, getComments, getDoc, getDocNodes, putDoc, workspaceFileRawUrl } from '../api'
import {
  autosavePaused,
  docSaveStatus,
  dropStash,
  planExternalUpdate,
  planSourceModeEntry,
  popStash,
  pushStash,
} from '../lib/docEditState'
import { compareRoundTrip } from '../lib/docMarkdown'
import { createDocRequestGate } from '../lib/docRequestGate'
import { myHandle } from '../me'

import { t } from '@/i18n'

export interface PanelDocProps {
  topic: Topic | null
  /** 父层在 AI 动过之后加一：文档那一格据此重读芝士刚写的那一版。 */
  activityTick: number
  topicList?: Topic[]
  /** 项目 AI 队友的名字：文档被它改过时，提示里说的是它。 */
  agentName?: string
}

export interface PanelDocHooks {
  /** 编辑器里现在这一版正文 markdown（不含被剥掉的标题那一行）。没有编辑器时 null。 */
  serializeVisual: () => string | null
  /** 把服务端这一版正文装进编辑器。 */
  installMarkdown: (body: string) => void
}

/** 「文档」这一格的全部取数：状态进、动作出，一个 DOM 都不碰。 */
export function usePanelDoc(props: PanelDocProps, hooks: PanelDocHooks) {
  const AUTHOR = myHandle()
  const { mdAndUp } = useDisplay()
  const projectId = computed<string | null>(() => props.topic?.project_id ?? null)

  // Set when the panel unmounts. Requests still in flight then land on a destroyed
  // editor, so every write after an await checks it.
  let disposed = false
  const requests = createDocRequestGate()
  let pendingReload = false
  let commentSequence = 0
  const topicDrafts = new Map<string, string[]>()

  function owns(request: ReturnType<typeof requests.begin>) {
    return !disposed && props.topic?.id === request.topicId && requests.owns(request)
  }

  // ---- 这一篇现在是什么状态 ----
  const editable = ref(true)
  const loading = ref(false)
  const saving = ref(false)
  const savedAt = ref<number | null>(null)
  const errorMsg = ref<string | null>(null)

  // Last markdown we know is persisted on the server. Used to (a) skip no-op
  // saves and (b) detect whether an incoming reload actually changed the doc, so
  // we don't clobber the user's in-progress local edits on every activity tick.
  const lastSavedMarkdown = ref<string>('')
  const dirty = ref(false)

  // ---- 军规 1: never silently drop content. ----
  // The raw doc exactly as stored on the server (the git-tracked markdown file).
  const rawDoc = ref<string>('')
  // The `# title\n` line stripped for display (the panel shows the topic title
  // itself); re-prepended on save so the FILE keeps its heading.
  const titlePrefix = ref<string>('')
  // Lossy load detected: parse→serialize differs from the file beyond the
  // tolerances documented in docMarkdown.ts. Autosave pauses; manual save asks.
  const lossy = ref(false)
  const lossyConfirmOpen = ref(false)
  // 源码模式: edit the raw markdown in Monaco — the lossless escape hatch.
  const sourceMode = ref(false)
  const sourceDraft = ref('')
  // 军规 1: unsaved edits that a mode switch could NOT carry over (see
  // planSourceModeEntry). Held here and offered back in the UI instead of being
  // dropped — the 「用源码模式」 button used to delete them outright. A stack, so
  // a second set-aside can't overwrite the first.
  const pendingEdits = ref<string[]>([])
  const hasPendingEdits = computed(() => pendingEdits.value.length > 0)
  // 军规 1: a newer version arrived from the server while we had unsaved local
  // edits. Neither side wins silently; both are held until the user picks.
  const externalDoc = ref<string | null>(null)
  // The doc_version this panel's content is based on. Every save sends it; the
  // backend refuses a save based on a version somebody has already moved past,
  // which is what keeps a 2.5-second autosave from erasing what 芝士 just wrote.
  const docVersion = ref(0)
  // True while server content is being installed into the editor: the editor's
  // own onUpdate must not read that as a person typing.
  const installing = ref(false)

  // 军规 1: the header used to show 「编辑中…」 while a lossy doc's autosave was
  // paused — the edits were stranded in memory and would NEVER be written. The
  // status now names that state instead of impersonating a save in progress.
  const saveStatus = computed(() =>
    docSaveStatus({
      loading: loading.value,
      saving: saving.value,
      dirty: dirty.value,
      lossy: lossy.value,
      sourceMode: sourceMode.value,
      editable: editable.value,
      savedAt: savedAt.value,
    })
  )
  const paused = computed(() =>
    autosavePaused({
      dirty: dirty.value,
      lossy: lossy.value,
      sourceMode: sourceMode.value,
      editable: editable.value,
    })
  )
  const pausedHint = computed(() =>
    editable.value ? t('work.room.doc.pausedEditable') : t('work.room.doc.pausedReadOnly')
  )

  // 手机上不提供源码模式：软键盘配 Monaco 不是能救的组合。而源码模式恰恰是
  // 「这份文档可视化编辑会丢格式」时唯一存得下去的那条路——escape hatch 不在，就不
  // 该让人先编辑一通再发现存不了。所以这种文档在手机上是只读的，并且明说去哪儿改。
  const editingBlocked = computed(() => !mdAndUp.value && lossy.value)
  watch(
    editingBlocked,
    (blocked) => {
      if (!blocked) return
      editable.value = false
    },
    { immediate: true }
  )

  // ---- 评论 (B4): 常驻评论区读的就是这两样 ----
  // Inline comments anchored to doc nodes. anchorNodes lists the doc's
  // paragraphs so an anchored comment (reply_to = node id) can be located and
  // flashed; comments without an anchor are page-level.
  const comments = ref<Block[]>([])
  const anchorNodes = ref<Block[]>([])

  const liveRefFingerprint = computed(() =>
    (props.topicList ?? []).map((t) => `${t.id}\u0000${t.title}\u0000${t.status ?? ''}`).join('\n')
  )
  // 同一次请求还要喂编辑器里的两种装饰，所以「哪一段上有徽章、哪一段上压着下划线」
  // 在这一层就算好，画的那一半照着往编辑器里投。装饰本身长什么样是 docDecorations.ts
  // 的事 —— 那份表不碰面板，也不该为了读一行徽章文案去翻这个文件。
  const liveRefIndex = computed(() => {
    const next = new Map<number, string>()
    // 徽章上写的是一个**话题**的标题与状态，那两样不在 anchorNodes 里，在 topicList 上，
    // 所以算索引的时候要把它们读一遍：支线改了标题、跑完收了工，索引本身（哪一段→哪条
    // 支线）一个字都没变，可装饰上要重建成新文案。原实现是每次拉完节点都无条件地
    // dispatch 一记 poke；这里靠「索引是谁算出来的」把同一件事说清楚 —— 输入变过，
    // 索引就是新的一份，画的那一半的 watch 照常重建装饰。
    void liveRefFingerprint.value
    anchorNodes.value.forEach((n, i) => {
      // 房间里的文档节点升级出来的是一条支线；私聊里的才是房间。取到哪个都是一个
      // 「地点 id」，打开的方式一样。
      const target = n.upgraded_to_task_id || n.upgraded_to_topic_id
      if (target) next.set(i, target)
    })
    return next
  })
  // The comment underlines: node id → top-level index, then group the anchored
  // comments (with usable quotes) under their block's index.
  const commentMarkIndex = computed(() => {
    const idToIndex = new Map(anchorNodes.value.map((n, i) => [n.id, i]))
    const next = new Map<number, { id: string; quote: string }[]>()
    for (const c of comments.value) {
      const anchorId = c.reply_to
      const quote = (c.anchor_quote || '').trim()
      if (!anchorId || !quote) continue
      const idx = idToIndex.get(anchorId)
      if (idx === undefined) continue
      const list = next.get(idx) ?? []
      list.push({ id: c.id, quote })
      next.set(idx, list)
    }
    return next
  })

  // ---- 读 ----
  async function loadComments(tid: string) {
    const request = requests.begin(tid)
    const sequence = ++commentSequence
    const [cs, ns] = await Promise.all([getComments(tid), getDocNodes(tid)])
    if (!owns(request) || sequence !== commentSequence) return
    comments.value = cs.data
    anchorNodes.value = ns.data
  }

  // 评论区自己是一个组件 (doc/DocComments.vue)：列表、折叠、写评论的输入框都在里面。
  // 留在取数这一层的只有**拉取**，因为同一次请求还要喂编辑器里的评论下划线装饰 ——
  // 拆开会变成两次请求、两份可能不一致的真相。
  async function refreshComments() {
    const tid = props.topic?.id
    if (tid) await loadComments(tid).catch(() => {})
  }

  // The panel already renders the topic title as the page title (Feishu Docs).
  // A doc whose first line is an H1 EXACTLY equal to that title would show it
  // twice — strip it for DISPLAY but remember the exact prefix: save() prepends
  // it again, so the file never loses its heading (pure string equality, no
  // guessing, no loss).
  function splitDuplicateTitle(md: string): { prefix: string; body: string } {
    const title = props.topic?.title?.trim()
    if (!title) return { prefix: '', body: md }
    const m = md.match(/^#\s+(.+?)\s*\n+/)
    return m && m[1].trim() === title ? { prefix: m[0], body: md.slice(m[0].length) } : { prefix: '', body: md }
  }

  // Install fresh server content into the panel state (editor + source draft +
  // fidelity check). The one place load & reload share.
  function installDoc(full: string) {
    const { prefix, body } = splitDuplicateTitle(full)
    rawDoc.value = full
    titlePrefix.value = prefix
    lastSavedMarkdown.value = body
    sourceDraft.value = full
    installMarkdown(body)
    checkFidelity(body)
  }

  function installMarkdown(body: string) {
    installing.value = true
    try {
      hooks.installMarkdown(body)
    } finally {
      installing.value = false
    }
  }

  // Compare the loaded markdown against its immediate parse→serialize round
  // trip. Runs on every load/reload; result drives the banner + autosave pause.
  function checkFidelity(md: string) {
    const visual = hooks.serializeVisual()
    if (visual === null) return
    const report = compareRoundTrip(md, visual)
    lossy.value = !report.clean
    if (!report.clean) {
      console.debug('[doc] lossy load detected — visual edit would rewrite these lines:\n' + report.diff)
    }
  }

  function reconcileSnapshot(block: Block | null) {
    const full = block?.content ?? ''
    const plan = planExternalUpdate({ dirty: dirty.value, incoming: full, rawDoc: rawDoc.value })
    docVersion.value = block?.doc_version ?? 0
    if (plan === 'install') {
      installDoc(full)
      externalDoc.value = null
      savedAt.value = null
    } else if (plan === 'conflict') {
      externalDoc.value = full
    } else {
      externalDoc.value = null
    }
  }

  async function loadDoc(topicId: string) {
    const request = requests.begin(topicId)
    errorMsg.value = null
    loading.value = true
    try {
      const block = await getDoc(topicId)
      if (!owns(request)) return
      if (saving.value || requests.invalidatedByWrite(request)) {
        if (saving.value) pendingReload = true
        else void reloadFromActivity(topicId)
        return
      }
      if (!requests.acceptRead(request, block?.doc_version ?? 0)) return
      // Check dirty AFTER the await: typing while the initial GET is slow must
      // have the same protection as typing during an activity refresh.
      reconcileSnapshot(block)
      void loadComments(topicId).catch(() => {})
    } catch (e) {
      if (owns(request)) errorMsg.value = e instanceof Error ? e.message : t('work.room.doc.loadFailed')
    } finally {
      if (owns(request)) loading.value = false
    }
  }

  // Notifications are hints; a settled GET is the authoritative snapshot.
  async function reloadFromActivity(topicId: string) {
    if (disposed || props.topic?.id !== topicId) return
    if (saving.value) {
      pendingReload = true
      return
    }
    const request = requests.begin(topicId)
    try {
      const block = await getDoc(topicId)
      if (!owns(request)) return
      if (saving.value || requests.invalidatedByWrite(request)) {
        if (saving.value) pendingReload = true
        else void reloadFromActivity(topicId)
        return
      }
      if (!requests.acceptRead(request, block?.doc_version ?? 0)) return
      reconcileSnapshot(block)
      loading.value = false
      void loadComments(topicId).catch(() => {})
    } catch {
      // Silent: activity-driven refresh is best-effort, with no retry loop.
    }
  }

  // ---- 写 ----
  // Feishu-style autosave: an explicit 保存 button reads as unfinished software.
  // Debounced from the LAST keystroke (not the dirty flip, which only fires
  // once per dirty cycle); ⌘S still saves immediately.
  // 军规 1: when a lossy load was detected, VISUAL-mode autosave is paused —
  // writing the round-tripped doc back would destroy the unsupported syntax.
  // Source mode edits the raw text, so its autosave is always safe.
  let autosaveTimer: ReturnType<typeof setTimeout> | null = null
  function queueAutosave() {
    if (autosaveTimer) clearTimeout(autosaveTimer)
    autosaveTimer = setTimeout(() => {
      if (lossy.value && !sourceMode.value) return
      // While the conflict bar is up, both versions are still on the table and
      // the person has not chosen. A timer that saved anyway would resolve it for
      // them — and win, since the panel is now holding the server's version.
      if (externalDoc.value !== null) return
      if (dirty.value && editable.value && !saving.value) void save()
    }, 2500)
  }

  // Full markdown the file should contain if we saved right now.
  function currentFullMarkdown(): string {
    if (sourceMode.value) return sourceDraft.value
    const visual = hooks.serializeVisual()
    if (visual === null) return rawDoc.value
    return titlePrefix.value + visual
  }

  async function save(force = false) {
    const topic = props.topic
    if (!topic || disposed || loading.value || saving.value) return
    // Lossy visual save needs explicit confirmation (源码模式 is the safe path).
    if (lossy.value && !sourceMode.value && !force) {
      lossyConfirmOpen.value = true
      return
    }
    const full = currentFullMarkdown()
    if (full === rawDoc.value) {
      dirty.value = false
      return
    }
    const request = requests.beginWrite(topic.id)
    const expectedVersion = docVersion.value
    saving.value = true
    errorMsg.value = null
    try {
      const saved = await putDoc(topic.id, full, expectedVersion)
      if (!owns(request)) return
      if (!requests.acceptWrite(request, saved.doc_version ?? 0)) {
        pendingReload = true
        return
      }
      const unchanged = currentFullMarkdown() === full
      const canonical = saved.content
      // The receipt, not the submitted draft, establishes the persisted base.
      // Do not install it over text typed while PUT was in flight.
      docVersion.value = saved.doc_version ?? expectedVersion
      if (unchanged && canonical !== full) installDoc(canonical)
      else {
        rawDoc.value = canonical
        lastSavedMarkdown.value = splitDuplicateTitle(canonical).body
        if (!sourceMode.value) sourceDraft.value = canonical
      }
      savedAt.value = Date.now()
      dirty.value = !unchanged && currentFullMarkdown() !== canonical
      if (dirty.value) queueAutosave()
      if (force && canonical === full) lossy.value = false
      externalDoc.value = null
    } catch (e) {
      if (!owns(request)) return
      // A failed response may follow a committed write. Reconcile once without
      // replaying the PUT; a 409 uses the existing explicit conflict surface.
      pendingReload = true
      if (e instanceof ApiError && e.status === 409) {
        pendingReload = false
        await showConflictWithServerDoc(topic.id)
      } else {
        errorMsg.value = e instanceof Error ? e.message : t('work.room.doc.saveFailed')
      }
    } finally {
      if (owns(request)) {
        saving.value = false
        if (pendingReload) {
          pendingReload = false
          void reloadFromActivity(topic.id)
        }
      }
    }
  }

  // A refused save, turned into the conflict the person can act on. The local
  // edits are untouched (still dirty, still in the editor); the bar's two buttons
  // are the only ways out, exactly as when the activity poll spots the same thing.
  async function showConflictWithServerDoc(topicId: string) {
    const request = requests.begin(topicId)
    try {
      const block = await getDoc(topicId)
      if (!owns(request) || !requests.acceptRead(request, block?.doc_version ?? 0)) return
      docVersion.value = block?.doc_version ?? 0
      externalDoc.value = block?.content ?? ''
    } catch {
      if (owns(request)) errorMsg.value = t('work.room.doc.changedElsewhere')
    }
  }

  // The lossy-confirm dialog's 「仍要保存」.
  function confirmLossySave() {
    lossyConfirmOpen.value = false
    void save(true)
  }

  function onBlur() {
    if (externalDoc.value !== null) return // the conflict is the person's to settle
    if (dirty.value && !(lossy.value && !sourceMode.value)) void save()
  }

  /** ⌘S / Ctrl-S：不等那只 2.5 秒的钟，现在就存。 */
  function onDocKeydown(e: KeyboardEvent) {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 's') {
      e.preventDefault()
      if (dirty.value && editable.value) void save()
    }
  }

  /** 有人在编辑器里改了东西：脏了、存档时间戳作废，重新起一次计时。 */
  function markEdited() {
    dirty.value = true
    savedAt.value = null
    queueAutosave()
  }

  /** 当场要说的失败（目前只有复制代码失败）；null = 把它关掉。 */
  function setError(message: string | null) {
    errorMsg.value = message
  }

  function toggleEditable() {
    if (editingBlocked.value) return
    editable.value = !editable.value
    // 人离开编辑模式时手里还有没存的东西 = 现在就存（飞书语义）。编辑器那一侧的
    // setEditable 由画的那一半跟着这个 ref 走 —— 它才知道编辑器在哪。
    if (!editable.value && dirty.value) void save()
  }

  // ---- 源码模式: raw markdown in Monaco. Entering shows the exact file
  // content (or the current unsaved visual edits, serialized); leaving parses
  // the draft back into the visual editor and re-runs the fidelity check. ----
  // 军规 1: on a lossy doc the source view must show the FILE, never the degraded
  // serialization — otherwise the escape hatch itself corrupts the syntax it
  // exists to protect. But the user's unsaved visual edits live ONLY in that
  // serialization, so they are stashed (pendingEdits) and offered back by the
  // bar above the editor. Nothing is dropped; the user decides.
  function enterSourceMode() {
    lossyConfirmOpen.value = false
    const plan = planSourceModeEntry({
      lossy: lossy.value,
      dirty: dirty.value,
      rawDoc: rawDoc.value,
      visualMarkdown: currentFullMarkdown(),
    })
    if (plan.stashed !== null) pendingEdits.value = pushStash(pendingEdits.value, plan.stashed)
    sourceDraft.value = plan.draft
    dirty.value = plan.dirty
    sourceMode.value = true
    // Source-mode autosave is never paused, so edits carried in here must not be
    // left stranded waiting for the next keystroke.
    if (dirty.value) queueAutosave()
  }

  function exitSourceMode() {
    sourceMode.value = false
    const { prefix, body } = splitDuplicateTitle(sourceDraft.value)
    titlePrefix.value = prefix
    installMarkdown(body)
    checkFidelity(body)
    dirty.value = sourceDraft.value !== rawDoc.value
    if (dirty.value) queueAutosave()
  }

  // 「恢复我的改动」: put the stashed edits back into whichever editor is showing.
  // A swap — what was on screen goes back onto the stash, so restoring can't be
  // the step that drops content either.
  function applyPendingEdits() {
    const { restored, stack } = popStash(pendingEdits.value, currentFullMarkdown(), rawDoc.value)
    if (restored === null) return
    pendingEdits.value = stack
    if (sourceMode.value) {
      sourceDraft.value = restored
    } else {
      const { prefix, body } = splitDuplicateTitle(restored)
      titlePrefix.value = prefix
      installMarkdown(body)
    }
    dirty.value = restored !== rawDoc.value
    if (dirty.value) {
      savedAt.value = null
      queueAutosave()
    }
  }

  function discardPendingEdits() {
    pendingEdits.value = dropStash(pendingEdits.value)
  }

  // 冲突条「查看磁盘版本」: open the server's version in source mode and stash the
  // local edits so they stay recoverable — the same never-drop mechanism.
  function viewExternalDoc() {
    const incoming = externalDoc.value
    if (incoming === null) return
    pendingEdits.value = pushStash(pendingEdits.value, currentFullMarkdown())
    externalDoc.value = null
    // The server version becomes the new base; the local edits sit in the stash,
    // one click away, instead of being clobbered by the incoming content.
    installDoc(incoming)
    dirty.value = false
    savedAt.value = null
    sourceMode.value = true
  }

  // 冲突条「用我的版本覆盖」: an explicit overwrite, never an implicit one.
  function overwriteWithMine() {
    externalDoc.value = null
    void save(true)
  }

  function toggleSourceMode() {
    if (sourceMode.value) exitSourceMode()
    else enterSourceMode()
  }

  // Typing in the source editor: same dirty + autosave contract as the visual
  // editor (source autosave is never paused — raw text can't be lossy).
  function onSourceInput(v: string) {
    sourceDraft.value = v
    if (installing.value) return
    dirty.value = v !== rawDoc.value
    if (dirty.value) {
      savedAt.value = null
      queueAutosave()
    }
  }

  /** 文档的节点树：正文里那些段落在服务端那一侧的身份（id、出自哪一次活）。 */
  function fetchDocNodes(): Promise<Block[]> {
    const tid = props.topic?.id
    if (!tid) return Promise.resolve([])
    return getDocNodes(tid).then((r) => r.data)
  }

  // Display-time image src resolution: workspace-relative paths (uploads/x.png)
  // render through the raw-file API; absolute http(s)/data URLs pass through.
  // The node attr keeps the ORIGINAL path — serialization writes it back
  // verbatim, so a localhost URL never leaks into the markdown file.
  function imageSrc(src: string): string {
    if (/^(https?:|data:|blob:|\/)/i.test(src)) return src
    const pid = projectId.value
    if (!pid) return src
    return workspaceFileRawUrl(pid, src.replace(/^\.\//, ''), props.topic?.id)
  }

  watch(
    () => props.topic?.id ?? null,
    (id, previousId) => {
      // A surviving panel can change topics without unmounting. Each topic's
      // draft stack remains recoverable only in that topic, never in its neighbor.
      if (previousId) {
        const stack = dirty.value ? pushStash(pendingEdits.value, currentFullMarkdown()) : pendingEdits.value
        topicDrafts.set(previousId, stack)
      }
      pendingEdits.value = id ? topicDrafts.get(id) ?? [] : []
      requests.select(id)
      pendingReload = false
      if (autosaveTimer) clearTimeout(autosaveTimer)
      saving.value = false
      loading.value = false
      dirty.value = false
      savedAt.value = null
      errorMsg.value = null
      externalDoc.value = null
      rawDoc.value = ''
      titlePrefix.value = ''
      lastSavedMarkdown.value = ''
      sourceDraft.value = ''
      sourceMode.value = false
      lossy.value = false
      lossyConfirmOpen.value = false
      docVersion.value = 0
      comments.value = []
      anchorNodes.value = []
      installMarkdown('')
      if (id) void loadDoc(id)
    },
    { immediate: true }
  )

  // AI activity: soft reload (respects unsaved edits).
  watch(
    () => props.activityTick,
    () => {
      const id = props.topic?.id
      if (id) void reloadFromActivity(id)
    }
  )

  // A2: when the sidebar's topics change (a subtopic's status moved, or a new one
  // was spawned), refetch the doc nodes so titles / status stay live. (No resize
  // listener needed anymore — the widgets are in flow.)
  //
  // 盯的是支线身上徽章真正用到的那几个字段，而不是数组本身。侧栏每 30 秒把整个话题
  // 数组换成一批新对象（stores/workspace.ts 的 refreshTopics），`deep: true` 因此每
  // 半分钟触发一次、白拉两条请求——而徽章上一个字都没变。新开一条支线仍然要重拉：
  // 那时候某个文档节点刚变成「已升级」，哪个段落该长徽章只有服务端知道。
  watch(liveRefFingerprint, () => {
    const tid = props.topic?.id
    if (tid) void loadComments(tid).catch(() => {})
  })

  onBeforeUnmount(() => {
    disposed = true
    if (autosaveTimer) clearTimeout(autosaveTimer)
  })

  return {
    // 这一篇现在是什么状态
    editable,
    editingBlocked,
    mdAndUp,
    loading,
    saving,
    errorMsg,
    dirty,
    lossy,
    lossyConfirmOpen,
    sourceMode,
    sourceDraft,
    rawDoc,
    titlePrefix,
    docVersion,
    reloadFromActivity,
    pendingEdits,
    hasPendingEdits,
    externalDoc,
    saveStatus,
    paused,
    pausedHint,
    // 评论 / 节点（装饰的原料）
    comments,
    anchorNodes,
    liveRefIndex,
    commentMarkIndex,
    // 动作
    refreshComments,
    commentAuthor: AUTHOR,
    sendComment: (topicId: string, content: string, anchor?: string, quote?: string) =>
      addComment(topicId, content, anchor, quote),
    save,
    confirmLossySave,
    onBlur,
    toggleEditable,
    toggleSourceMode,
    enterSourceMode,
    exitSourceMode,
    applyPendingEdits,
    discardPendingEdits,
    viewExternalDoc,
    overwriteWithMine,
    onSourceInput,
    onDocKeydown,
    markEdited,
    setError,
    fetchDocNodes,
    imageSrc,
  }
}
