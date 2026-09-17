<script setup lang="ts">
// 一条图片消息里的那张图。
//
// 单独一个组件，是因为这张图要经过一次请求才拿得到：附件字节的端点从
// Authorization 头认人，`<img src>` 带不了头，直接挂 URL 拿到的是 401（读者看到
// 一张裂图）。所以先 fetch、转成 object URL 再交给 <img>。
//
// object URL 不会自己消失——它把字节钉在内存里直到被 revoke。一屏消息翻过去就是
// 几十张图，所以每换一次地址都要把上一张还回去，卸载时也一样。
import { computed, onBeforeUnmount, ref, watch } from 'vue'

import { attachmentImageUrl } from '../api'

const props = defineProps<{
  topicId: string | null
  /** 附件在话题工作区里的路径（消息块里的 content）。 */
  path: string
  alt?: string
}>()

// 路径是工作区里的，`uploads/<id>/…`；说给读者听的是文件名那一段。
const altText = computed(() => props.alt || props.path.split('/').pop() || '图片')

const url = ref('')
const failed = ref(false)
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
  release()
  if (!props.topicId || !props.path) return
  try {
    const next = await attachmentImageUrl(props.topicId, props.path)
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
  <a v-if="url" class="im-image-link" :href="url" target="_blank" rel="noopener">
    <img class="im-image" :src="url" :alt="altText" loading="lazy" />
  </a>
  <!-- 失败说一句，别留一块空白：空白和「这条消息本来就没图」长得一样。 -->
  <span v-else-if="failed" class="im-image-failed">图片加载失败</span>
</template>

<style scoped>
.im-image-link {
  display: inline-block;
  margin-top: 2px;
  line-height: 0;
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
</style>
