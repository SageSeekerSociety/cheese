<script setup lang="ts">
// The mark of a file's kind beside its name (`fileMark` in `lib/fileKind`): a
// few letters in the kind's colour, or a file icon for a kind without letters.
// Decoration — the name beside it says the same thing to a screen reader.
import { computed } from 'vue'

import { FILE_MARK_LETTERS, fileMark } from '../../lib/fileKind'

const props = defineProps<{ name: string }>()

const kind = computed(() => fileMark(props.name))
const letters = computed(() => FILE_MARK_LETTERS[kind.value])
</script>

<template>
  <span class="file-kind" :class="`file-kind--${kind}`" aria-hidden="true">
    <template v-if="letters">{{ letters }}</template>
    <v-icon v-else size="15">{{ kind === 'text' ? 'mdi-file-document-outline' : 'mdi-file-outline' }}</v-icon>
  </span>
</template>

<style scoped>
/* The letters are a glyph in a 16px box, like an icon, not text to read: three
   of them ("git") have to fit the box, hence the small size. */
.file-kind {
  display: inline-grid;
  flex: none;
  place-items: center;
  width: 16px;
  height: 16px;
  font-family: var(--font-mono);
  font-size: 9px;
  font-weight: 700;
  line-height: 16px;
  letter-spacing: -0.02em;
  color: var(--file-kind-file);
}
.file-kind--c,
.file-kind--cpp {
  color: var(--file-kind-c);
}
.file-kind--header {
  color: var(--file-kind-header);
}
.file-kind--python {
  color: var(--file-kind-python);
}
.file-kind--javascript {
  color: var(--file-kind-javascript);
}
.file-kind--typescript {
  color: var(--file-kind-typescript);
}
.file-kind--java {
  color: var(--file-kind-java);
}
.file-kind--go {
  color: var(--file-kind-go);
}
.file-kind--rust {
  color: var(--file-kind-rust);
}
.file-kind--markdown {
  color: var(--file-kind-markdown);
}
.file-kind--config {
  color: var(--file-kind-config);
}
.file-kind--makefile {
  color: var(--file-kind-makefile);
}
.file-kind--shell {
  color: var(--file-kind-shell);
}
.file-kind--git {
  color: var(--file-kind-git);
}
</style>
