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
import { useDisplay } from 'vuetify'

import { copyIntoRoom, openRoomFileEditor, roomFileRevisions } from '../../../api'
import { t } from '../../../i18n'

import RoomFileHistory from './RoomFileHistory.vue'

import BaseButton from '@/components/base/BaseButton.vue'
import UserRef from '@/components/common/UserRefLink.vue'

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
// 手机上放不下编辑器旁边再并排一栏 320px 的历史：历史打开时盖满编辑器那一块，
// 再点一下「历史」收起。
const { mdAndUp } = useDisplay()
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
      reject(new Error(t('work.room.fileEditor.serviceUnreachable')))
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
    if (!api) throw new Error(t('work.room.fileEditor.notLoaded'))
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
          failure.value = e?.data?.errorDescription || t('work.room.fileEditor.editorError')
        },
      },
    })
  } catch (e) {
    failure.value = e instanceof Error ? e.message : t('work.room.fileEditor.openFailed')
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
  const target = copyName.value.trim() || `${t('work.room.outputs.defaultFolder')}/${leaf}`
  try {
    const made = await copyIntoRoom(props.topicId, props.path, target)
    emit('opened', made.path)
  } catch (e) {
    failure.value = e instanceof Error ? e.message : t('work.room.fileEditor.copyFailed')
  }
}

function onRestored() {
  void start()
}

onMounted(() => {
  const leaf = props.path.split('/').pop() ?? 'file'
  copyName.value = `${t('work.room.outputs.defaultFolder')}/${leaf}`
  void start()
  timer = setInterval(watchVersion, 8000)
})

onBeforeUnmount(() => {
  if (timer) clearInterval(timer)
  instance?.destroyEditor()
})
</script>

<template>
  <div class="rfe" :class="{ 'rfe--phone': !mdAndUp }" data-testid="room-file-editor">
    <div class="rfe__bar">
      <v-icon size="18">mdi-file-edit-outline</v-icon>
      <span class="rfe__name">{{ path }}</span>
      <span v-if="session?.enabled && !session.editable" class="t-meta">{{ t('work.room.fileEditor.readOnly') }}</span>
      <span v-else-if="unsaved" class="t-meta">{{ t('work.room.fileEditor.unsaved') }}</span>
      <span v-else-if="savedSeq" class="t-meta">{{ t('work.room.fileEditor.savedAs', { seq: savedSeq }) }}</span>
      <v-spacer />
      <BaseButton kind="ghost" size="sm" prepend-icon="mdi-history" @click="showHistory = !showHistory">
        {{ t('work.room.fileEditor.history') }}
      </BaseButton>
      <BaseButton
        kind="ghost"
        size="sm"
        icon="mdi-close"
        :title="t('work.room.fileEditor.close')"
        @click="emit('close')"
      />
    </div>

    <v-alert v-if="changedBy" type="info" density="compact" class="ma-2" data-testid="changed-by">
      <div>
        <i18n-t scope="global" keypath="work.room.fileEditor.changedBy" tag="span">
          <template #who>
            <UserRef v-if="changedBy.author" :handle="changedBy.author" />
            <template v-else>{{
              changedBy.author_kind === 'agent' ? t('work.room.defaultAgentName') : t('work.room.fileEditor.someone')
            }}</template>
          </template>
          <template #seq>{{ changedBy.seq }}</template>
        </i18n-t>
        <template v-if="changedBy.note">{{ t('work.room.fileEditor.changedNote', { note: changedBy.note }) }}</template>
      </div>
      <div class="t-meta">
        {{ t('work.room.fileEditor.staleNote') }}
      </div>
      <BaseButton kind="secondary" size="sm" class="mt-1" @click="start">{{
        t('work.room.fileEditor.loadNew')
      }}</BaseButton>
    </v-alert>

    <v-alert v-if="failure" type="warning" density="compact" class="ma-2">{{ failure }}</v-alert>

    <div class="rfe__main">
      <div class="rfe__canvas">
        <div v-if="loading" class="rfe__state"><v-progress-circular indeterminate color="primary" /></div>
        <div v-else-if="session && !session.enabled" class="rfe__state">
          <div>{{ session.reason ? t(`work.room.fileEditor.unavailable.${session.reason}`) : '' }}</div>
          <div v-if="session.copyable" class="mt-3 rfe__copy">
            <v-text-field
              v-model="copyName"
              density="compact"
              :label="t('work.room.fileEditor.copyName')"
              autocomplete="off"
              hide-details
            />
            <BaseButton kind="secondary" class="mt-2" @click="makeCopy">{{
              t('work.room.fileEditor.makeCopy')
            }}</BaseButton>
            <div class="t-meta mt-1">{{ t('work.room.fileEditor.originalKept') }}</div>
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
.rfe :deep(.v-alert) {
  flex: none;
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
.rfe--phone .rfe__main {
  position: relative;
}
.rfe--phone .rfe__side {
  position: absolute;
  inset: 0;
  z-index: 1;
  width: auto;
  border-left: 0;
  background: var(--surface);
}
</style>
