<script setup lang="ts">
import { ref } from 'vue'

import film from '@/assets/landing/zhongzhi-film.mp4'
import poster from '@/assets/landing/zhongzhi-film-poster.jpg'
import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'

// The film tells one course project from first week to last, so it waits on its
// poster and, when asked, plays from the top with sound: a silent loop dropped
// visitors into the middle of the story with nothing to tell them where they were.
const video = ref<HTMLVideoElement>()
const playing = ref(false)

function play() {
  const el = video.value
  if (!el) return
  playing.value = true
  el.muted = false
  el.currentTime = 0
  el.play()?.catch(() => {})
}
</script>

<template>
  <section class="film" :aria-label="t('publicSite.filmLabel')">
    <p class="film-caption">{{ t('publicSite.filmCaption') }}</p>
    <div class="film-frame">
      <video
        ref="video"
        class="film-video"
        :src="film"
        :poster="poster"
        :controls="playing"
        muted
        playsinline
        preload="metadata"
      />
      <div v-if="!playing" class="film-play">
        <BaseButton kind="primary" size="lg" prepend-icon="mdi-play" @click="play">
          {{ t('publicSite.filmPlay') }} · {{ t('publicSite.filmLength') }}
        </BaseButton>
      </div>
    </div>
  </section>
</template>

<style scoped>
.film {
  max-width: calc(960px + 2 * var(--gutter));
  padding: 64px var(--gutter);
  margin: 0 auto;
}

.film-caption {
  margin: 0 0 16px;
  font-size: clamp(18px, 1.6vw, 24px);
  line-height: 1.5;
  color: var(--text);
}

.film-frame {
  position: relative;
  overflow: hidden;
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
  aspect-ratio: 16 / 9;
}

.film-video {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: cover;
}

/* The poster is a fixed warm white in both themes; a solid button is the one
   control that reads on it whichever theme the page is in.
   It sits in a corner, so it leaves the poster's subject in view. */
.film-play {
  position: absolute;
  left: 24px;
  bottom: 24px;
}

@media (max-width: 900px) {
  .film {
    padding: 32px var(--gutter);
  }

  .film-play {
    left: 12px;
    bottom: 12px;
  }
}
</style>
