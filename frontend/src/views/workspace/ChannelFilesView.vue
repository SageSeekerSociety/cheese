<script setup lang="ts">
// 一个频道的文件：这个频道自己的东西都在这一页上——芝士交付的文档、定时任务交回的
// 结果、有人放进来的文件。以前只有干活的那台机器和记得住名字的人看得见它们。
//
// 一层一层看，像文件夹：先列这一层的文件夹和文件，点文件夹进去，顶上一条面包屑回到
// 上一层。路径里的层次是实的——定时任务往「runs/2026-09-14/」这样的地方写结果，平铺
// 出来只会把日期那一层当成名字的一部分。
//
// 一行一份，写着它的名字，和是谁什么时候放下的；点一份在右边一栏看内容，手机上它是
// 整一页。数据和动作都从外面来（`ChannelFiles.vue`）。
import type { RoomFile } from '@/api/roomFiles'

import { computed, ref, watch } from 'vue'
import { useDisplay } from 'vuetify'

import BaseButton from '@/components/base/BaseButton.vue'
import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import BaseLoadError from '@/components/base/BaseLoadError.vue'
import AppPage from '@/components/common/AppPage.vue'
import FileBytesPreview from '@/components/common/FileBytesPreview.vue'
import { t } from '@/i18n'
import { fmtBytes } from '@/lib/changesTree'
import { KIND_ICONS, kindOf } from '@/lib/libraryKinds'
import { relTime } from '@/lib/relTime'

const props = defineProps<{
  files: RoomFile[]
  /** 这个房间名册上每个 handle 叫什么，署名用（`ChannelFiles.vue` 拉一次交下来）。 */
  memberNames: Record<string, string>
  /** 清单不是全部：文件多到超过上限，只列了最近改的那些。 */
  truncated: boolean
  loading: boolean
  error: string | null
  read: (file: RoomFile, asPdf: boolean) => Promise<ArrayBuffer>
}>()

const emit = defineEmits<{
  (e: 'retry'): void
  (e: 'download', file: RoomFile): void
}>()

const { mdAndUp } = useDisplay()

/** 这一层里的一个文件夹：它的路径、名字，和它底下有几份、最近改的是其中哪一份。 */
interface FolderRow {
  path: string
  name: string
  count: number
  modified_at: string
}

// 看哪一层、看哪一份。地址上都不留：这一页不是别人会转发的去处（要发给别人是「复制
// 链接」拿到的那个频道地址），刷新回到最上层。
const here = ref('')
const selected = ref<RoomFile | null>(null)
watch(
  () => props.files,
  (rows) => {
    if (selected.value && !rows.some((row) => row.path === selected.value?.path)) selected.value = null
    // 清单一换（重新读回来，或者别人挪走了东西），脚下这一层可能已经不在了。
    if (here.value && !rows.some((row) => row.path.startsWith(`${here.value}/`))) here.value = ''
  }
)

/** 这一层有什么：文件夹在前，文件在后，各自按最近改的排。
 *
 *  文件夹是数出来的，不是问出来的：清单给的是每一份文件的路径，一个空文件夹不在里面
 *  （也不会真的有——房间文件区里的文件夹都是因为有东西在里面才存在）。 */
const listing = computed(() => {
  const prefix = here.value ? `${here.value}/` : ''
  const folders = new Map<string, FolderRow>()
  const files: RoomFile[] = []
  for (const file of props.files) {
    if (prefix && !file.path.startsWith(prefix)) continue
    const rest = file.path.slice(prefix.length)
    const cut = rest.indexOf('/')
    if (cut < 0) {
      files.push(file)
      continue
    }
    const path = `${prefix}${rest.slice(0, cut)}`
    const found = folders.get(path)
    if (!found) folders.set(path, { path, name: rest.slice(0, cut), count: 1, modified_at: file.modified_at })
    else {
      found.count += 1
      if (file.modified_at > found.modified_at) found.modified_at = file.modified_at
    }
  }
  return {
    folders: [...folders.values()].sort((a, b) => b.modified_at.localeCompare(a.modified_at)),
    files,
  }
})

/** 从最上层到脚下这一层，一层一个名字，最后一个是现在这一层。 */
const crumbs = computed(() => {
  const parts = here.value ? here.value.split('/') : []
  return parts.map((part, index) => ({ name: part, path: parts.slice(0, index + 1).join('/') }))
})

/** 名字和它所在的文件夹分开写：一个频道里文件可能重名，路径才不会。 */
function name(file: RoomFile): string {
  return file.path.split('/').pop() ?? file.path
}

function folder(file: RoomFile): string {
  const cut = file.path.lastIndexOf('/')
  return cut < 0 ? '' : file.path.slice(0, cut)
}

/**
 * 谁放下的。作者只存着 handle，名字从名册里换出来（`memberNames`）——队友写的是另一
 * 位队友（小苔、无言），不是一句「AI 队友」；名册上认不出的照「芝士」写，`cheese-<hex>`
 * 那种座位编号是内部的，屏幕上没人该看见它；没有保存记录的照实说「来源未知」，不编一
 * 个人名。一份文件是怎么放下的（传上来的、定时任务交回的）不写在这一行：这一页只认
 * 得下「谁、什么时候、多大」，那几项在它自己的保存历史里（`RoomFileHistory`）。
 */
function who(file: RoomFile): string {
  if (!file.author) return t('work.library.unknownSource')
  const known = props.memberNames[file.author]
  if (known) return known
  return file.author_kind === 'agent' ? t('work.room.defaultAgentName') : file.author
}

/** 什么时候 · 多大 —— 和资料库那一行同一种写法（`ProjectLibraryView` 的 `rowMeta`）。 */
function meta(file: RoomFile): string {
  return [relTime(file.modified_at), fmtBytes(file.bytes)].filter(Boolean).join(' · ')
}

const title = computed(() => (selected.value && !mdAndUp ? name(selected.value) : t('navigation.project.channelFiles')))

function readSelected(asPdf: boolean): Promise<ArrayBuffer> {
  return selected.value ? props.read(selected.value, asPdf) : Promise.resolve(new ArrayBuffer(0))
}
</script>

<template>
  <AppPage :title="title" width="full">
    <template #meta>{{ t('files.room.readOnly') }}</template>

    <div class="room-files">
      <!-- 清单：手机上看着一份文件时让出整页。 -->
      <section v-if="mdAndUp || !selected" class="room-files__list">
        <BaseLoadError v-if="error" :title="t('files.room.loadError')" :error="error" @retry="emit('retry')" />

        <div
          v-else-if="loading && !files.length"
          class="py-8 text-center"
          role="status"
          :aria-label="t('files.room.loading')"
        >
          <v-progress-circular indeterminate size="28" color="primary" />
        </div>

        <template v-else>
          <!-- 在哪一层。最上层不用写（没有上一层可回）。 -->
          <nav v-if="here" class="room-files__crumbs" :aria-label="t('files.room.crumbsLabel')">
            <button type="button" class="room-files__crumb" @click="here = ''">{{ t('files.room.crumbsRoot') }}</button>
            <template v-for="crumb in crumbs" :key="crumb.path">
              <span class="room-files__crumb-sep" aria-hidden="true">/</span>
              <button
                type="button"
                class="room-files__crumb"
                :aria-current="crumb.path === here ? 'location' : undefined"
                @click="here = crumb.path"
              >
                {{ crumb.name }}
              </button>
            </template>
          </nav>

          <ul v-if="listing.folders.length || listing.files.length" class="room-files__rows">
            <li v-for="row in listing.folders" :key="row.path" class="room-files__row">
              <button type="button" class="room-files__open" @click="here = row.path">
                <v-icon icon="mdi-folder-outline" size="20" class="room-files__icon" />
                <span class="room-files__id">
                  <span class="room-files__name t-body">{{ row.name }}</span>
                  <span class="t-meta c-faint">{{
                    t('work.library.folderMeta', { n: row.count, when: relTime(row.modified_at) })
                  }}</span>
                </span>
              </button>
            </li>
            <li
              v-for="file in listing.files"
              :key="file.path"
              class="room-files__row"
              :class="{ 'room-files__row--on': selected?.path === file.path }"
            >
              <button type="button" class="room-files__open" @click="selected = file">
                <v-icon :icon="KIND_ICONS[kindOf(file.path)]" size="20" class="room-files__icon" />
                <span class="room-files__id">
                  <span class="room-files__name t-body">{{ name(file) }}</span>
                  <!-- 一行一句平话，不给人名做 chip：这一行整行已经是一个按钮（点开
                       看内容），按钮里再放一个能点的东西，键盘和读屏都要打架。 -->
                  <span class="t-meta c-faint">{{ who(file) }} · {{ meta(file) }}</span>
                </span>
              </button>
            </li>
          </ul>

          <BaseEmptyState
            v-else
            size="inline"
            align="center"
            class="room-files__empty"
            :title="t('files.room.empty')"
          />
        </template>

        <p v-if="truncated" class="t-meta c-faint room-files__truncated">
          {{ t('files.room.truncated', { n: files.length }) }}
        </p>
      </section>

      <!-- 这一份：桌面上在右边一栏，手机上是整一页。 -->
      <section v-if="selected" class="room-files__detail" :class="{ 'room-files__detail--phone': !mdAndUp }">
        <header class="room-files__head">
          <h2 v-if="mdAndUp" class="t-title room-files__detail-name">{{ name(selected) }}</h2>
          <p class="t-meta c-faint room-files__detail-meta">
            {{ who(selected) }} · {{ meta(selected) }}
            <template v-if="folder(selected)"> · {{ folder(selected) }}</template>
          </p>
          <div v-if="mdAndUp" class="room-files__actions">
            <BaseButton
              kind="primary"
              size="sm"
              prepend-icon="mdi-download-outline"
              @click="emit('download', selected)"
            >
              {{ t('work.library.download') }}
            </BaseButton>
            <BaseButton
              size="sm"
              prepend-icon="mdi-close"
              :aria-label="t('work.library.close')"
              @click="selected = null"
            >
              {{ t('work.library.close') }}
            </BaseButton>
          </div>
        </header>
        <div class="room-files__preview">
          <FileBytesPreview :filename="selected.path" :source="selected.path" :read="readSelected" />
        </div>
        <div v-if="!mdAndUp" class="room-files__bar">
          <BaseButton kind="primary" block @click="emit('download', selected)">{{
            t('work.library.download')
          }}</BaseButton>
        </div>
      </section>
    </div>
  </AppPage>
</template>

<style scoped>
.room-files {
  display: flex;
  flex: 1 1 auto;
  min-height: 0;
}

.room-files__list {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  gap: 12px;
  min-width: 0;
  padding: 20px 24px 32px;
  overflow-y: auto;
}

.room-files__crumbs {
  display: flex;
  flex-wrap: wrap;
  gap: 2px;
  align-items: center;
}

.room-files__crumb {
  padding: 2px 6px;
  font: inherit;
  color: var(--muted);
  cursor: pointer;
  background: none;
  border: 0;
  border-radius: 6px;
}

.room-files__crumb:hover {
  color: var(--ink);
}

/* 现在这一层：还能点（回到它自己），但不必再动，写清是哪一层。 */
.room-files__crumb[aria-current='location'] {
  font-weight: 600;
  color: var(--ink);
}

.room-files__crumb-sep {
  color: var(--faint);
}

.room-files__rows {
  display: flex;
  flex-direction: column;
  padding: 0;
  margin: 0;
  list-style: none;
  border-top: 1px solid var(--line);
}

.room-files__row {
  display: flex;
  align-items: center;
  border-bottom: 1px solid var(--line);
}

.room-files__row--on {
  background: var(--fill);
}

.room-files__open {
  display: flex;
  flex: 1 1 auto;
  gap: 10px;
  align-items: center;
  min-width: 0;
  padding: 10px 12px;
  font: inherit;
  text-align: left;
  cursor: pointer;
  background: none;
  border: 0;
}

.room-files__open:hover {
  background: var(--fill);
}

.room-files__icon {
  flex: none;
  color: var(--muted);
}

.room-files__id {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.room-files__name {
  overflow-wrap: anywhere;
}

.room-files__empty {
  margin-top: 24px;
}

.room-files__detail {
  display: flex;
  flex: none;
  flex-direction: column;
  width: 480px;
  min-width: 0;
  min-height: 0;
  border-left: 1px solid var(--line);
}

.room-files__detail--phone {
  flex: 1 1 auto;
  width: auto;
  border-left: 0;
}

.room-files__head {
  display: flex;
  flex: none;
  flex-direction: column;
  gap: 4px;
  padding: 16px 20px 12px;
  border-bottom: 1px solid var(--line);
}

.room-files__detail--phone .room-files__head {
  padding: 10px 16px;
}

.room-files__detail-name {
  margin: 0;
  overflow-wrap: anywhere;
}

.room-files__detail-meta {
  margin: 0;
  overflow-wrap: anywhere;
}

.room-files__actions {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin: 4px 0 0 -8px;
}

.room-files__preview {
  flex: 1 1 auto;
  min-height: 0;
  background: var(--canvas);
}

.room-files__bar {
  flex: none;
  padding: 10px 16px calc(10px + env(safe-area-inset-bottom));
  border-top: 1px solid var(--line);
  background: var(--surface);
}
</style>
