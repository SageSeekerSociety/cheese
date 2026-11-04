/**
 * 反馈表单的那一栏附件：选中即上传，名单在这里，字节留在服务端。
 *
 * **粘贴是主要入口。** 报一个界面问题的人手上通常已经有一张截图（截图工具默认把它
 * 放进剪贴板），而点「选择文件」再进文件夹找那张图的代价，是他宁可不贴。所以这一栏
 * 同时听 `paste`：焦点在表单里任何地方按 Ctrl+V，剪贴板里的图片和文件都走同一条
 * `add`。文本粘贴不受影响 —— 只取 `clipboardData.files`，那是浏览器从剪贴板里解出来
 * 的真文件；纯文本的粘贴里它是空的，正文照旧。
 *
 * **选中即上传，和发题目那条路一样**（`useAttachmentUploads` 的文件头）：文件在选完
 * 的那一刻就开始传，失败在用户还在填表的时候就说出来，而不是写完几百字按提交才发现。
 * 代价相同：中途放弃会留下一个没人引用的对象，服务端那两行说明写的就是它。
 *
 * **上传失败的那一份留在名单里**，画成一行带原因的错误，可以重试 —— 悄悄收回一行，
 * 人只会以为自己没选上，然后重选一遍同一个文件。正在传的那一份也占一行，带进度。
 *
 * **传成功的那些写回 `store.draft.attachments`**：提交时只发 id，而草稿落盘也跟着
 * 它走（`lib/feedbackDraft.ts`），所以选了图之后刷新页面，名单还在。进度和失败这两
 * 件事**不进 store**：它们是这一次会话里的动作，捡不回来，存进去只会让恢复出来的
 * 表单显示一个永远停在 40% 的行。
 */

import type { FeedbackDraft } from '@/stores/feedback/types'

import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import { uploadFeedbackAttachment } from '@/api/feedback'
import { toast } from '@/lib/toast'
import { fmtFileSize } from '@/lib/usageFormat'
import { useFeedbackStore } from '@/stores/feedback'

/** 名单里的一行。三种状态，由这一行自己说清此刻在做什么。 */
export interface PendingAttachment {
  /** `v-for` 的 key，也是「去掉这一行」的判据。传成功之后它就是服务端给的 id；
   *  正在传或传失败的那一份只有一个本地号（它还没有 id）。 */
  key: string
  name: string
  bytes: number
  /** 0–1，只在 `uploading` 时有意义（重试要用它，所以原文件挂着一起）。 */
  progress: number
  state: 'uploading' | 'done' | 'failed'
  /** 服务端给的 id，`done` 时才有。 */
  id?: string
  /** 服务端说的那句为什么没成。 */
  error?: string
  /** 手上这份文件。传成功之后就放下 —— 字节已经在服务端了，留着它就是把一份 8MB
   *  的图钉在内存里，而这个表单可能开着很久。 */
  file?: File
}

/** 上限的兜底值：和 `GET /feedback/meta` 报的那两个数一致。
 *
 *  **它是兜底，不是权威**：权威在服务端（`MAX_ATTACHMENTS` 与
 *  `library.MAX_FILE_BYTES`），表单挂载后 `setLimits` 用 meta 报来的数覆盖它。留一份
 *  默认值是为了 meta 还在飞的那一帧也能拦住明显超限的文件，而不是让前几个先传上去、
 *  下一帧才被拒。 */
const DEFAULT_MAX_FILES = 10
const DEFAULT_MAX_BYTES = 10 * 1024 * 1024

let localSeq = 0

/** 草稿里那几行还原成界面上的一行行。传成功的那些（`done`）。 */
function seedRows(draft: FeedbackDraft): PendingAttachment[] {
  return draft.attachments.map((file) => ({
    key: file.id,
    name: file.name,
    bytes: file.bytes,
    progress: 1,
    state: 'done' as const,
    id: file.id,
  }))
}

export function useFeedbackAttachments() {
  const store = useFeedbackStore()
  const { t } = useI18n()

  /** 已经传上去的（也可以是从盘上捞回来的那几行），从草稿起手。 */
  const rows = ref<PendingAttachment[]>(seedRows(store.draft))
  const maxFiles = ref(DEFAULT_MAX_FILES)
  const maxBytes = ref(DEFAULT_MAX_BYTES)

  // **换了一份草稿就重新起手。** 表单有活过一份草稿的时候：会话里那张提案卡用的
  // 对话框提交完不会卸载，下一张卡再打开还是同一个组件实例 —— 而 `openSubmit` 是
  // 整份换掉 `store.draft`。不重新起手的话，上一张卡选的截图会留在屏幕上；而人再
  // 选一个文件时 `sync()` 会把这两行一起写进新草稿，于是上一张卡的截图跟着发出去了。
  //
  // 判的是**对象身份**（`openSubmit` 赋的是新对象），不是内容：`sync()` 是就地改
  // `attachments` 数组，身份不变，所以这里不会被自己写回去的值触发。
  let seeded = store.draft
  watch(
    () => store.draft,
    (draft) => {
      if (draft === seeded) return
      seeded = draft
      rows.value = seedRows(draft)
    }
  )

  const uploading = computed(() => rows.value.some((row) => row.state === 'uploading'))

  /** 把传成功的那几行写回草稿。**每一次名单变化都写一遍**，不在提交前才收：
   *  提交那条路读的就是 `store.draft`，而「名单变了、草稿没变」的表现是「屏幕上
   *  看得见、发出去没有」。 */
  function sync(): void {
    store.draft.attachments = rows.value.flatMap((row) =>
      row.id ? [{ id: row.id, name: row.name, bytes: row.bytes }] : []
    )
    store.touchDraft()
  }

  function setLimits(files: number, bytes: number): void {
    if (files > 0) maxFiles.value = files
    if (bytes > 0) maxBytes.value = bytes
  }

  function remove(key: string): void {
    rows.value = rows.value.filter((row) => row.key !== key)
    sync()
  }

  async function send(row: PendingAttachment, file: File): Promise<void> {
    try {
      const uploaded = await uploadFeedbackAttachment(file, (fraction) => {
        row.progress = fraction
      })
      row.state = 'done'
      row.progress = 1
      row.id = uploaded.id
      // 服务端返回的名字才是存下来的那个（清洗过、可能补了后缀），以它为准。
      row.name = uploaded.name
      row.bytes = uploaded.bytes
      // 字节在服务端了，手上的这一份放掉（见 `file` 的注释）。
      row.file = undefined
      sync()
    } catch (error) {
      row.state = 'failed'
      row.error = error instanceof Error ? error.message : t('feedback.submit.attachments.failed')
    }
  }

  /** 重试一行传失败了的。失败时把它留下来，正是为了这一步。 */
  async function retry(key: string): Promise<void> {
    const row = rows.value.find((item) => item.key === key)
    if (!row || row.state !== 'failed' || !row.file) return
    const file = row.file
    row.state = 'uploading'
    row.error = undefined
    row.progress = 0
    await send(row, file)
  }

  /** 把几个文件加进来。**一个一个按顺序传**：一串照片同时上传会互相抢带宽，而几条
   *  进度条同时在动反而看不出哪一个卡住了。 */
  async function add(files: File[]): Promise<void> {
    const room = maxFiles.value - rows.value.length
    if (files.length > room) {
      toast.error(t('feedback.submit.attachments.tooMany', { max: maxFiles.value }))
    }
    for (const file of files.slice(0, Math.max(0, room))) {
      if (file.size > maxBytes.value) {
        toast.error(
          t('feedback.submit.attachments.tooLarge', {
            name: file.name || t('feedback.submit.attachments.unnamed'),
            size: fmtFileSize(maxBytes.value),
          })
        )
        continue
      }
      const row: PendingAttachment = {
        key: `local-${++localSeq}`,
        name: file.name || t('feedback.submit.attachments.unnamed'),
        bytes: file.size,
        progress: 0,
        state: 'uploading',
        file,
      }
      rows.value = [...rows.value, row]
      await send(row, file)
    }
  }

  /** 剪贴板里那几个文件；没有就什么都不做，让这一次粘贴照常走它原来的路（正文）。
   *
   *  **只看 `files`，不碰 `items`**：纯文本粘贴里 `files` 是空的；从 `items` 自己拼
   *  文件名和扩展名是猜的，猜错时存下来的是一份名字不对的文件。
   *
   *  有文件时**拦下这一次粘贴**：不拦的话，那一张图除了进附件，还会被浏览器当成富
   *  内容塞进页面（`contenteditable` 里留下一段看不见的 base64）。 */
  function fromClipboard(event: ClipboardEvent): File[] {
    const files = Array.from(event.clipboardData?.files ?? [])
    if (!files.length) return []
    event.preventDefault()
    return files
  }

  return { rows, uploading, maxFiles, maxBytes, setLimits, add, remove, retry, fromClipboard }
}
