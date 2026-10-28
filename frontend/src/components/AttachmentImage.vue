<script setup lang="ts">
// 一条图片消息里的那张图。
//
// 单独一个组件，是因为这张图要经过一次请求才拿得到：附件字节的端点从
// Authorization 头认人，`<img src>` 带不了头，直接挂 URL 拿到的是 401（读者看到
// 一张裂图）。所以先 fetch、转成 object URL 再交给 <img>。
//
// object URL 不会自己消失——它把字节钉在内存里直到被 revoke。一屏消息翻过去就是
// 几十张图，所以每换一次地址都要把上一张还回去，卸载时也一样。
//
// 点一下在这张图本身上放大看（ImageLightbox），消息里那张和待发条上的缩略图都是。
// 不开新标签页：object URL 只活在当前这份文档里，新标签页打开是一片空白——那正是
// 这条消息原来的样子。预览开着的时候不能把地址还回去，换图时先把预览收起来。
//
// 「去哪儿取字节」不在这里：那只函数连着后端，而这张图散在对话栏、工作面板和公共页
// 里——这一颗 import 了它，渲染它的每一个组件都得跟着装后端才能挂起来。所以从外壳
// 注入的 AttachmentSource 问（lib/attachmentSource.ts），没人注入时按「读不到」画。
import { computed, inject, onBeforeUnmount, ref, watch } from 'vue'

import { t } from '../i18n'

import ImageLightbox from './ImageLightbox.vue'

import { ATTACHMENT_SOURCE, NO_ATTACHMENT_SOURCE } from '@/lib/attachmentSource'

const props = defineProps<{
  topicId: string | null
  /** 附件在话题工作区里的路径（消息块里的 content）。 */
  path: string
  alt?: string
  /** 输入框里那张待发的缩略图：尺寸固定成一个小方块，点开看的是同一张图。 */
  thumb?: boolean
}>()

const source = inject(ATTACHMENT_SOURCE, NO_ATTACHMENT_SOURCE)

// 路径是工作区里的，`uploads/<id>/…`；说给读者听的是文件名那一段。
const altText = computed(() => props.alt || props.path.split('/').pop() || t('work.image.alt'))

const url = ref('')
const failed = ref(false)
/** 放大看的那一层开着没有。它摆的就是 `url` 这一个地址。 */
const viewing = ref(false)
let live = ''
let generation = 0

function release() {
  if (!live) return
  URL.revokeObjectURL(live)
  live = ''
}

async function load() {
  const mine = ++generation
  failed.value = false
  url.value = ''
  // 换成另一张了：预览里那张已经不是这条消息的图，先收起来，再把地址还回去。
  viewing.value = false
  release()
  if (!props.topicId || !props.path) return
  try {
    const next = await source.imageUrl(props.topicId, props.path)
    // 已经不是最新那一轮了：刚拿到的 URL 不会有人挂上去，只能在这里还回去。
    if (mine !== generation) {
      URL.revokeObjectURL(next)
      return
    }
    live = next
    url.value = next
  } catch {
    if (mine === generation) failed.value = true
  }
}

watch(() => [props.topicId, props.path], load, { immediate: true })
onBeforeUnmount(() => {
  generation += 1
  release()
})
</script>

<template>
  <!-- 缩略图：盒子先占住位置，成败都一样大——待发条不会因为一张图慢半拍而跳动。
       名字不挂在这儿：外面那张卡片已经写着它，并且负责弹出全名。字节到手之后
       点一下就能放大看，和消息里那张一样。 -->
  <button
    v-if="thumb && url"
    type="button"
    class="att-face att-face--open"
    :aria-label="t('work.image.open', { name: altText })"
    @click="viewing = true"
  >
    <img class="im-thumb__img" :src="url" :alt="altText" decoding="async" />
  </button>
  <span v-else-if="thumb" class="att-face">
    <v-icon v-if="failed" size="16" class="im-thumb__failed">mdi-image-broken-variant</v-icon>
  </span>
  <!-- 消息里那张图：点一下在本页放大看（ImageLightbox）。不在新标签页打开——这个
       object URL 只活在当前这份文档里，新标签页拿到的是空白。 -->
  <button
    v-else-if="url"
    type="button"
    class="im-image-open"
    :aria-label="t('work.image.open', { name: altText })"
    @click="viewing = true"
  >
    <img class="im-image" :src="url" :alt="altText" loading="lazy" decoding="async" />
  </button>
  <!-- 失败说一句，别留一块空白：空白和「这条消息本来就没图」长得一样。 -->
  <span v-else-if="failed" class="im-image-failed">{{ t('work.image.failed') }}</span>

  <ImageLightbox
    v-if="viewing && url"
    :src="url"
    :alt="altText"
    :topic-id="topicId"
    :path="path"
    @close="viewing = false"
  />
</template>

<style scoped>
/* 点开看大图的那一下：按钮只是盒子，长相还是那张图。 */
.im-image-open {
  display: inline-block;
  margin-top: 2px;
  padding: 0;
  border: 0;
  background: none;
  line-height: 0;
  cursor: zoom-in;
}
.im-image-open:hover .im-image {
  border-color: var(--line-2);
}
.im-image {
  max-width: min(360px, 100%);
  max-height: 260px;
  border-radius: 8px;
  border: 1px solid var(--line);
  background: var(--fill);
  object-fit: contain;
}
.im-image-failed {
  font-size: 13px;
  color: var(--muted);
}
/* 能点开的那个盒子：还是 .att-face（style.css，四种附件状态共用），只是换成了
   button，把浏览器给按钮的那圈边框和底色去掉。 */
.att-face--open {
  padding: 0;
  border: 0;
  cursor: zoom-in;
}
.att-face--open:hover {
  background: var(--fill-2);
}
/* 盒子本身是 .att-face（style.css），四种附件状态共用；这里只管盒子里的图。 */
.im-thumb__img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}
.im-thumb__failed {
  color: var(--muted);
}
</style>
