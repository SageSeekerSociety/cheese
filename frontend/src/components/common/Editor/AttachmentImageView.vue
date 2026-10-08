<script setup lang="ts">
import { computed, getCurrentInstance, inject, provide, ref, watch } from 'vue'
import { NodeViewContent, nodeViewProps, NodeViewWrapper } from '@tiptap/vue-3'

import { ATTACHMENT_IMAGE_SOURCE } from './attachmentImageSource'

import { t } from '@/i18n'

const props = defineProps(nodeViewProps)
const images = inject(ATTACHMENT_IMAGE_SOURCE, null)

// tiptap 的 NodeViewWrapper 从包它的那一层 inject 这两样（拖动时的处理、装饰类），而它没有
// 默认值：在编辑器里那个包裹层 provide 过了，这一件单独挂起来（预览站、测试）时却没人给，
// 于是每个实例都吼一句 injection not found。给过就让给它（编辑器里那两样是真的），没给才补
// 一对空的，好让这一件真的只吃 props。`provides` 是 Vue 内部实例上的东西，类型里没有它。
const instance = getCurrentInstance() as unknown as { provides?: Record<string | symbol, unknown> } | null
const provided = instance?.provides
if (!provided || !Object.prototype.hasOwnProperty.call(provided, 'onDragStart')) provide('onDragStart', undefined)
if (!provided || !Object.prototype.hasOwnProperty.call(provided, 'decorationClasses'))
  provide('decorationClasses', undefined)

const src = ref<string | null>(null)
const failed = ref(false)

watch(
  () => props.node.attrs.attachmentId as number | null,
  async (id) => {
    src.value = null
    failed.value = false
    if (!id || !images) return
    try {
      src.value = await images.url(Number(id))
    } catch {
      failed.value = true
    }
  },
  { immediate: true }
)

// 宽高是上传时量的原图尺寸：留出同样比例的位置，图到之前正文不跳；宽不超过原图。
const frameStyle = computed(() => {
  const width = Number(props.node.attrs.width) || null
  const height = Number(props.node.attrs.height) || null
  return {
    aspectRatio: width && height ? `${width} / ${height}` : undefined,
    maxWidth: width ? `${width}px` : undefined,
  }
})

const captionEmpty = computed(() => props.node.content.size === 0)
const captionPlaceholder = computed(() => JSON.stringify(t('editor.image.caption')))
</script>

<template>
  <NodeViewWrapper class="rt-image" :class="{ 'is-selected': selected }">
    <div class="rt-image__frame" :style="frameStyle" contenteditable="false">
      <img v-if="src" :src="src" :alt="node.attrs.alt ?? ''" loading="lazy" decoding="async" />
      <span v-else-if="failed" class="rt-image__failed">{{ t('editor.image.loadFailed') }}</span>
    </div>
    <NodeViewContent
      class="rt-image__caption"
      :class="{ 'is-empty': captionEmpty && editor.isEditable }"
      :hidden="captionEmpty && !editor.isEditable"
    />
  </NodeViewWrapper>
</template>

<style scoped>
.rt-image {
  margin: 12px 0;
  text-align: center;
}
.rt-image__frame {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  margin: 0 auto;
  border-radius: var(--radius-md);
  background: var(--fill);
  overflow: hidden;
}
.rt-image__frame img {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: contain;
}
.rt-image.is-selected .rt-image__frame {
  outline: 2px solid rgb(var(--v-theme-primary));
  outline-offset: 2px;
}
.rt-image__failed {
  padding: 24px 12px;
  color: var(--muted);
  font-size: 13px;
}
.rt-image__caption {
  margin-top: 4px;
  color: var(--muted);
  font-size: 13px;
}
.rt-image__caption.is-empty::before {
  content: v-bind(captionPlaceholder);
  color: var(--faint);
  pointer-events: none;
}
</style>
