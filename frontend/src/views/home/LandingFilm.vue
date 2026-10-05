<script setup lang="ts">
import { ref } from 'vue'

import film from '@/assets/landing/zhongzhi-film.mp4'
import poster from '@/assets/landing/zhongzhi-film-poster.jpg'
import { t } from '@/i18n'

// The film tells one course project from first week to last, so it waits on its
// poster and, when asked, plays from the top with sound: a silent loop dropped
// visitors into the middle of the story with nothing to tell them where they were.
// It stays feathered into the page over a blurred glow of itself the whole time,
// so there are no player controls: clicking the picture pauses and resumes it, and
// at the end it goes back to its poster.
const video = ref<HTMLVideoElement>()
const playing = ref(false)

function play() {
  const el = video.value
  if (!el) return
  if (playing.value) {
    if (el.paused) el.play()?.catch(() => {})
    else el.pause()
    return
  }
  playing.value = true
  el.muted = false
  el.currentTime = 0
  el.play()?.catch(() => {})
}

function rewind() {
  playing.value = false
  video.value?.load()
}
</script>

<template>
  <figure class="film" :class="{ 'film-playing': playing }" :aria-label="t('publicSite.filmLabel')">
    <figcaption class="film-caption">
      <button v-if="!playing" type="button" class="film-caption-play" @click="play">
        {{ t('publicSite.filmCaption') }}<v-icon class="film-caption-arrow" icon="mdi-chevron-right" size="1.2em" />
      </button>
      <template v-else>{{ t('publicSite.filmCaption') }}</template>
    </figcaption>
    <div class="film-frame" :style="{ '--film-poster': `url(${poster})` }">
      <video
        ref="video"
        class="film-video"
        :src="film"
        :poster="poster"
        muted
        playsinline
        preload="metadata"
        @click="play"
        @ended="rewind"
      />
    </div>
  </figure>
</template>

<style scoped>
.film {
  display: flex;
  margin: 0;
  flex-direction: column;
  gap: 16px;
}

/* The caption is the film's way in: the line says what it shows, and the arrow
   says it plays. It sits under the picture's right-hand side, clear of the
   words. */
.film-caption {
  order: 1;
  align-self: flex-end;
  padding-right: var(--gutter, 48px);
  font-size: clamp(18px, 1.5vw, 22px);
  font-weight: 500;
  line-height: 1.4;
  color: var(--muted);
}

.film-caption-play {
  display: inline-flex;
  padding: 0;
  font: inherit;
  color: inherit;
  cursor: pointer;
  background: none;
  border: 0;
  align-items: center;
  gap: 2px;
}

.film-caption-play:hover {
  color: var(--ink);
}

.film-caption-arrow {
  color: var(--accent-press);
  transition: transform var(--dur-base) var(--ease-out);
}

.film-caption-play:hover .film-caption-arrow {
  transform: translateX(3px);
}

.film-frame {
  position: relative;
  isolation: isolate;
  aspect-ratio: 16 / 9;
}

/* The glow: the poster itself, blurred and spread past the frame. */
.film-frame::before {
  position: absolute;
  z-index: -1;
  background: var(--film-poster) center / cover;
  content: '';
  inset: -6%;
  opacity: var(--film-glow, 0.5);
  filter: blur(48px) saturate(1.2);
}

.film-video {
  display: block;
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  cursor: pointer;
  object-fit: cover;
  filter: var(--film-dim, none);

  /* A long, soft fade on the left, where it passes under the words, and soft
     edges top and foot, so no edge of the frame ever shows; the right runs off
     the screen. */
  mask-image: linear-gradient(to right, transparent, var(--ink) 35%),
    linear-gradient(to bottom, transparent, var(--ink) 16%, var(--ink) 84%, transparent);
  mask-composite: intersect;
  transition: filter var(--dur-slow) var(--ease-out);
}

/* The poster is a fixed warm white. On the dark page a bright glow turns to
   smoke around a lit slab, so the glow nearly goes and the picture is dimmed
   until it plays. */
:root[data-theme='dark'] .film {
  --film-glow: 0.14;
  --film-dim: brightness(0.62) saturate(0.9);
}

.film-playing .film-video {
  filter: none;
}

@media (width <= 900px) {
  .film-caption {
    order: 0;
    align-self: flex-start;
    padding-right: 0;
  }

  .film-video {
    mask-image: linear-gradient(to right, transparent, var(--ink) 16%, var(--ink) 84%, transparent),
      linear-gradient(to bottom, transparent, var(--ink) 20%, var(--ink) 80%, transparent);
  }
}
</style>
