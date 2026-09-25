<script setup lang="ts">
// 在房间里直接改一份 Word、表格或幻灯片。
//
// 编辑器（OnlyOffice）改的就是这份 Office 文件本身：保存回来的是 .docx / .xlsx /
// .pptx，芝士下一次读的就是它，「导出」就是下载它。没有中间格式，也就没有「编辑器
// 里的样子」和「文件里的样子」对不上这回事。
//
// 这个组件自己要管的只有一件事：文件在编辑器开着的时候被别人改了。芝士改完会带一句
// 说明，这里把那句话和「载入新版本」摆出来——不自动重载，因为读者手上可能正有没存的
// 修改；他没存的那部分在他存的时候由后端另存一份，谁的都不丢。

import type { RoomFileEditorSession, RoomFileRevision } from '../../../api'

import { nextTick, onBeforeUnmount, onMounted, ref } from 'vue'

import { copyIntoRoom, openRoomFileEditor, roomFileRevisions } from '../../../api'

import RoomFileHistory from './RoomFileHistory.vue'

const props = defineProps<{ topicId: string; path: string }>()
const emit = defineEmits<{ (e: 'close'): void; (e: 'opened', path: string): void }>()

type DocEditor = { destroyEditor: () => void }
type DocsApi = { DocEditor: new (id: string, config: Record<string, unknown>) => DocEditor }

const session = ref<RoomFileEditorSession | null>(null)
const failure = ref('')
const loading = ref(true)
const unsaved = ref(false)
const showHistory = ref(false)
const changedBy = ref<RoomFileRevision | null>(null)
const savedSeq = ref<number | null>(null)
const copyName = ref('')
const hostId = `room-file-editor-${Math.random().toString(36).slice(2)}`

let instance: DocEditor | null = null
let loadedVersion = ''
let editorKey = ''
let timer: ReturnType<typeof setInterval> | null = null

const scripts = new Map<string, Promise<void>>()

function loadScript(src: string): Promise<void> {
  const existing = scripts.get(src)
  if (existing) return existing
  const made = new Promise<void>((resolve, reject) => {
    const el = document.createElement('script')
    el.src = src
    el.async = true
    el.onload = () => resolve()
    el.onerror = () => {
      scripts.delete(src)
      reject(new Error('编辑器服务连不上，稍后再试'))
    }
    document.head.appendChild(el)
  })
  scripts.set(src, made)
  return made
}

async function start() {
  loading.value = true
  failure.value = ''
  changedBy.value = null
  instance?.destroyEditor()
  instance = null
  try {
    const got = await openRoomFileEditor(props.topicId, props.path)
    session.value = got
    if (!got.enabled || !got.config || !got.api_url) return
    await loadScript(got.api_url)
    const api = (window as unknown as { DocsAPI?: DocsApi }).DocsAPI
    if (!api) throw new Error('编辑器没有加载出来')
    loadedVersion = got.version ?? ''
    editorKey = String((got.config.document as { key?: string })?.key ?? '')
    await nextTick()
    instance = new api.DocEditor(hostId, {
      ...got.config,
      width: '100%',
      height: '100%',
      events: {
        onDocumentStateChange: (e: { data: boolean }) => {
          unsaved.value = e.data
        },
        onError: (e: { data?: { errorDescription?: string } }) => {
          failure.value = e?.data?.errorDescription || '编辑器出错了'
        },
      },
    })
  } catch (e) {
    failure.value = e instanceof Error ? e.message : '打不开编辑器'
  } finally {
    loading.value = false
  }
}

/** 文件变了没有、是谁改的。自己这次编辑存下的不算「被别人改了」。 */
async function watchVersion() {
  if (!session.value?.enabled) return
  try {
    const got = await roomFileRevisions(props.topicId, props.path)
    const top = got.data[0]
    if (!top || !got.version || got.version === loadedVersion) return
    if (top.editor_key && top.editor_key === editorKey) {
      loadedVersion = got.version
      savedSeq.value = top.seq
      return
    }
    changedBy.value = top
  } catch {
    // 下一轮再看；读不到历史不妨碍继续编辑。
  }
}

async function makeCopy() {
  const leaf = props.path.split('/').pop() ?? 'file'
  const target = copyName.value.trim() || `文档/${leaf}`
  try {
    const made = await copyIntoRoom(props.topicId, props.path, target)
    emit('opened', made.path)
  } catch (e) {
    failure.value = e instanceof Error ? e.message : '复制失败'
  }
}

function onRestored() {
  void start()
}

onMounted(() => {
  const leaf = props.path.split('/').pop() ?? 'file'
  copyName.value = `文档/${leaf}`
  void start()
  timer = setInterval(watchVersion, 8000)
})

onBeforeUnmount(() => {
  if (timer) clearInterval(timer)
  instance?.destroyEditor()
})
</script>

<template>
  <div class="rfe" data-testid="room-file-editor">
    <div class="rfe__bar">
      <v-icon size="18">mdi-file-edit-outline</v-icon>
      <span class="rfe__name">{{ path }}</span>
      <span v-if="session?.enabled && !session.editable" class="t-meta">只读（这种格式只能查看）</span>
      <span v-else-if="unsaved" class="t-meta">有未保存的修改</span>
      <span v-else-if="savedSeq" class="t-meta">已保存为第 {{ savedSeq }} 版</span>
      <v-spacer />
      <v-btn size="small" variant="text" prepend-icon="mdi-history" @click="showHistory = !showHistory"> 历史 </v-btn>
      <v-btn size="small" variant="text" icon="mdi-close" title="关闭" @click="emit('close')" />
    </div>

    <v-alert v-if="changedBy" type="info" density="compact" class="ma-2" data-testid="changed-by">
      <div>
        {{ changedBy.author_kind === 'agent' ? '芝士' : changedBy.author || '有人' }}
        刚刚保存了这份文件（第 {{ changedBy.seq }} 版）<template v-if="changedBy.note">：{{ changedBy.note }}</template>
      </div>
      <div class="t-meta">
        你现在看到的是改动之前的内容。载入新版本会丢掉这里还没保存的修改；先保存的话，你的修改会另存一份，不会覆盖。
      </div>
      <v-btn size="small" color="primary" variant="flat" class="mt-1" @click="start">载入新版本</v-btn>
    </v-alert>

    <v-alert v-if="failure" type="warning" density="compact" class="ma-2">{{ failure }}</v-alert>

    <div class="rfe__main">
      <div class="rfe__canvas">
        <div v-if="loading" class="rfe__state"><v-progress-circular indeterminate color="primary" /></div>
        <div v-else-if="session && !session.enabled" class="rfe__state">
          <div>{{ session.reason }}</div>
          <div v-if="session.copyable" class="mt-3 rfe__copy">
            <v-text-field v-model="copyName" density="compact" label="在房间里存成" autocomplete="off" hide-details />
            <v-btn color="primary" variant="flat" class="mt-2" @click="makeCopy">复制一份来编辑</v-btn>
            <div class="t-meta mt-1">原件留在资料库里不动。</div>
          </div>
        </div>
        <!-- 编辑器把占位的那个 div 换成自己的 iframe，所以定位留在外面这一层。 -->
        <div v-show="session?.enabled && !loading" class="rfe__host"><div :id="hostId" /></div>
      </div>
      <aside v-if="showHistory" class="rfe__side">
        <RoomFileHistory
          :topic-id="topicId"
          :path="path"
          :version="savedSeq ? String(savedSeq) : null"
          @restored="onRestored"
        />
      </aside>
    </div>
  </div>
</template>

<style scoped>
.rfe {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: var(--surface);
}
.rfe__bar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 12px;
  border-bottom: 1px solid var(--line);
}
.rfe__name {
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.rfe__main {
  flex: 1;
  min-height: 0;
  display: flex;
}
.rfe__canvas {
  flex: 1;
  min-width: 0;
  position: relative;
}
.rfe__host {
  position: absolute;
  inset: 0;
}
.rfe__state {
  padding: 48px 16px;
  text-align: center;
  color: var(--muted);
}
.rfe__copy {
  max-width: 360px;
  margin: 0 auto;
}
.rfe__side {
  width: 320px;
  border-left: 1px solid var(--line);
  overflow: auto;
}
</style>
