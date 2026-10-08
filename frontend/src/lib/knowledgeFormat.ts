/**
 * 知识库页上「这一条长什么样」的全部判断，一个纯函数一行。
 *
 * 拆之前这些是 `views/teams/detail/Knowledge.vue` 里的一串 `const xxx = (…) =>`：
 * 资料类型到图标 / 中文名的那两张表、三个日期格式、文件大小和时长、上传对话框
 * 里那两栏选项。它们没有一处读得到组件状态，所以原来只是「碰巧长在页里」。
 *
 * 拆出来是给三件东西共用的：网格卡、表格行、详情对话框 —— 同一个类型名不能
 * 在三种视图里各写一遍。这个文件不 import 任何 api、store 或路由，所以
 * `components/` 下的件可以放心引它（见 .claude/rules/architecture.md）。
 */
import type { Knowledge, KnowledgeType } from '@/types'
import type { MaterialType } from '@/types/materials'

import dayjs from 'dayjs'

import i18n, { t } from '@/i18n'

/** 工具栏「资料类型」那一栏的选项：类型码本身就是请求里要的值，显示名走 `knowledgeTypeLabel`。 */
export const RESOURCE_TYPE_OPTIONS: KnowledgeType[] = ['TEXT', 'MATERIAL', 'LINK', 'CODE']

/** 上传对话框里那四档（图标在 `uploadTypeIcon` 里，显示名走 `knowledgeTypeLabel`）。 */
export const KNOWLEDGE_TYPE_OPTIONS: KnowledgeType[] = ['MATERIAL', 'TEXT', 'LINK', 'CODE']

/** 四档类型在筛选和上传里的显示名。 */
export function knowledgeTypeLabel(type: KnowledgeType): string {
  switch (type) {
    case 'MATERIAL':
      return t('teams.knowledge.typeFile')
    case 'TEXT':
      return t('teams.knowledge.typeText')
    case 'LINK':
      return t('teams.knowledge.typeLink')
    case 'CODE':
      return t('teams.knowledge.typeCode')
    default:
      return t('teams.knowledge.unknownType')
  }
}

/** 代码片段那一档的编程语言表。 */
const LANGUAGE_OPTIONS = [
  { text: 'JavaScript', value: 'javascript' },
  { text: 'TypeScript', value: 'typescript' },
  { text: 'HTML', value: 'html' },
  { text: 'CSS', value: 'css' },
  { text: 'Python', value: 'python' },
  { text: 'Java', value: 'java' },
  { text: 'C/C++', value: 'cpp' },
  { text: 'Go', value: 'go' },
  { text: 'Ruby', value: 'ruby' },
  { text: 'PHP', value: 'php' },
  { text: 'Shell', value: 'shell' },
]

/** 编程语言表，连同末尾那一档「其他」（它的名字随界面语言变）。 */
export function languageOptions(): { text: string; value: string }[] {
  return [...LANGUAGE_OPTIONS, { text: t('teams.knowledge.languageOther'), value: 'plaintext' }]
}

/** 资料的类型图标。材料还要看它是哪一路（图 / 视频 / 音频 / 文档）。 */
export function resourceTypeIcon(type: KnowledgeType, materialType?: MaterialType): string {
  if (type === 'MATERIAL') {
    switch (materialType) {
      case 'file':
        return 'mdi-file-document'
      case 'image':
        return 'mdi-file-image'
      case 'video':
        return 'mdi-file-video'
      case 'audio':
        return 'mdi-file-music'
      default:
        return 'mdi-file'
    }
  }

  switch (type) {
    case 'TEXT':
      return 'mdi-text-box'
    case 'LINK':
      return 'mdi-link'
    case 'CODE':
      return 'mdi-code-braces'
    default:
      return 'mdi-file'
  }
}

/** 类型的显示名。和图标一样，材料看的是哪一路。 */
export function resourceTypeName(type: KnowledgeType, materialType?: MaterialType): string {
  if (type === 'MATERIAL') {
    switch (materialType) {
      case 'file':
        return t('teams.knowledge.typeDocument')
      case 'image':
        return t('teams.knowledge.typeImage')
      case 'video':
        return t('teams.knowledge.typeVideo')
      case 'audio':
        return t('teams.knowledge.typeAudio')
      default:
        return t('teams.knowledge.typeFile')
    }
  }
  return knowledgeTypeLabel(type)
}

/** 上传对话框里那四档的图标。和 `resourceTypeIcon` 不是同一张表：这里没有材料。 */
export function uploadTypeIcon(type: KnowledgeType): string {
  switch (type) {
    case 'MATERIAL':
      return 'mdi-file-document-outline'
    case 'TEXT':
      return 'mdi-text-box-outline'
    case 'LINK':
      return 'mdi-link'
    case 'CODE':
      return 'mdi-code-braces'
    default:
      return 'mdi-file'
  }
}

/** 卡片右下角那个「几月几号」。没有年份：卡上是「最近」的语境。 */
export function formatDay(timestamp: number): string {
  return dayjs(timestamp).format('MM/DD')
}

/** 详情里那一行「添加时间」：要年份，要分钟。 */
export function formatDetailDate(timestamp: number): string {
  return new Date(timestamp).toLocaleString(i18n.global.locale.value, {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

/** 原始讨论里那句话的时间。今天只给时刻，本周给星期，更久以前给日期。 */
export function formatMessageTime(timestamp: number): string {
  const now = dayjs()
  const messageTime = dayjs(timestamp)

  if (now.diff(messageTime, 'day') === 0) {
    // 今天
    return messageTime.format('HH:mm')
  } else if (now.diff(messageTime, 'day') === 1) {
    // 昨天
    return t('teams.knowledge.yesterday', { time: messageTime.format('HH:mm') })
  } else if (now.diff(messageTime, 'day') < 7) {
    // 本周
    return messageTime.format('ddd HH:mm')
  } else {
    // 更久以前
    return messageTime.format('MM-DD HH:mm')
  }
}

/** 材料的大小。B / KB / MB / GB 四档，两位小数。 */
export function formatFileSize(size: number): string {
  if (size < 1024) {
    return `${size} B`
  } else if (size < 1024 * 1024) {
    return `${(size / 1024).toFixed(2)} KB`
  } else if (size < 1024 * 1024 * 1024) {
    return `${(size / (1024 * 1024)).toFixed(2)} MB`
  } else {
    return `${(size / (1024 * 1024 * 1024)).toFixed(2)} GB`
  }
}

/** 音视频的时长，`分:秒`。 */
export function formatDuration(duration: number): string {
  const minutes = Math.floor(duration / 60)
  const seconds = Math.floor(duration % 60)
  return `${minutes}:${seconds.toString().padStart(2, '0')}`
}

/**
 * 这一条是不是「我」放上去的 —— 决定删除键出现与否。
 *
 * 判据只有创建者一条（小队管理员没有额外权力，这是现在这一页的口径）。
 * `ownerId` 是当前登录的人，由页从 composable 递进来：`components/` 下的件
 * 够不着账号服务，也不该够着。
 */
export function canEditKnowledge(resource: Knowledge, ownerId?: number): boolean {
  return resource.creator.id === ownerId
}

/** 上传时挑中的那个文件是不是图片（决定画预览还是画一行文件名）。 */
export function isImageFile(file: File): boolean {
  return file.type.startsWith('image/')
}

/** 上传时挑中的那个文件的图标，按 MIME 猜。 */
export function fileTypeIcon(file: File): string {
  if (file.type.startsWith('image/')) {
    return 'mdi-file-image-outline'
  } else if (file.type.startsWith('video/')) {
    return 'mdi-file-video-outline'
  } else if (file.type.startsWith('audio/')) {
    return 'mdi-file-music-outline'
  } else if (file.type.includes('pdf')) {
    // mdi-file-pdf-box：@mdi/font 7.4.47 里没有 -outline 那一个，写了也是一片空白。
    return 'mdi-file-pdf-box'
  } else if (file.type.includes('word') || file.type.includes('document')) {
    return 'mdi-file-word-outline'
  } else {
    return 'mdi-file-outline'
  }
}

/** 上传前那一眼预览。object URL，不回收到现在就够用（一次上传一次）。 */
export function filePreviewUrl(file: File): string {
  return URL.createObjectURL(file)
}
