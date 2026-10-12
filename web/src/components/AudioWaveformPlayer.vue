<script setup>
import { computed, onBeforeUnmount, ref, useId, watch } from 'vue';
import { audioTime, loadAudioWaveform } from '../audioWaveform.js';
import WorkflowIcon from './WorkflowIcon.vue';

const props = defineProps({
  src: String,
  file: String,
  name: { type: String, default: '音频' },
  compact: Boolean,
});
const player = ref(null),
  peaks = ref([]),
  duration = ref(0),
  time = ref(0),
  playing = ref(false),
  loading = ref(true),
  waveError = ref(false),
  playbackError = ref(''),
  rate = ref(1);
const clipId = useId();
const progress = computed(() => (duration.value ? Math.max(0, Math.min(1, time.value / duration.value)) : 0));
let sequence = 0,
  frame;
function tick() {
  time.value = player.value?.currentTime || 0;
  if (playing.value) frame = requestAnimationFrame(tick);
}
function stopTick() {
  cancelAnimationFrame(frame);
}
function playState(value) {
  playing.value = value;
  stopTick();
  if (value) tick();
}
async function toggle() {
  if (playing.value) {
    player.value.pause();
    return;
  }
  playbackError.value = '';
  try {
    await player.value.play();
  } catch {
    playbackError.value = '音频暂时无法播放，请稍后重试。';
  }
}
function seek(value) {
  if (!duration.value || !player.value) return;
  player.value.currentTime = Math.max(0, Math.min(duration.value, Number(value)));
  time.value = player.value.currentTime;
}
function speed() {
  const rates = [0.75, 1, 1.25, 1.5, 2];
  rate.value = rates[(rates.indexOf(rate.value) + 1) % rates.length];
  player.value.playbackRate = rate.value;
}
watch(
  () => props.src,
  async (src) => {
    const serial = ++sequence;
    stopTick();
    playing.value = false;
    time.value = 0;
    duration.value = 0;
    peaks.value = [];
    waveError.value = false;
    playbackError.value = '';
    loading.value = true;
    if (!src) {
      loading.value = false;
      return;
    }
    try {
      const result = await loadAudioWaveform(src);
      if (serial !== sequence) return;
      peaks.value = result.peaks;
      duration.value = result.duration;
    } catch {
      if (serial === sequence) waveError.value = true;
    } finally {
      if (serial === sequence) loading.value = false;
    }
  },
  { immediate: true }
);
onBeforeUnmount(() => {
  sequence++;
  stopTick();
  player.value?.pause();
});
defineExpose({ play: () => player.value?.play(), pause: () => player.value?.pause() });
</script>

<template>
  <section
    class="audio-waveform-player"
    :class="{ compact }"
    :aria-label="name + '播放器'"
    @pointerdown.stop
    @dblclick.stop
    @keydown.stop
  >
    <audio
      ref="player"
      :src="src"
      preload="auto"
      :aria-label="name"
      @loadedmetadata="
        if (Number.isFinite($event.target.duration)) duration = $event.target.duration;
        $event.target.playbackRate = rate;
      "
      @timeupdate="time = $event.target.currentTime"
      @play="playState(true)"
      @pause="playState(false)"
      @ended="playState(false)"
      @error="playbackError = '音频暂时无法播放，请稍后重试。'"
    />
    <div class="waveform-track" :class="{ loading }">
      <svg
        v-if="peaks.length"
        class="waveform-svg"
        viewBox="0 0 1000 100"
        preserveAspectRatio="none"
        aria-label="真实音频波形"
      >
        <defs>
          <clipPath :id="clipId"><rect x="0" y="0" :width="progress * 1000" height="100" /></clipPath>
        </defs>
        <g class="waveform-bars">
          <rect
            v-for="(peak, i) in peaks"
            :key="i"
            :x="((i + 0.5) * 1000) / peaks.length"
            :y="50 - Math.max(2, peak * 78) / 2"
            width="5"
            :height="Math.max(2, peak * 78)"
            rx="2"
          />
        </g>
        <g class="waveform-played" :clip-path="`url(#${clipId})`">
          <rect
            v-for="(peak, i) in peaks"
            :key="i"
            :x="((i + 0.5) * 1000) / peaks.length"
            :y="50 - Math.max(2, peak * 78) / 2"
            width="5"
            :height="Math.max(2, peak * 78)"
            rx="2"
          />
        </g>
      </svg>
      <div v-else class="waveform-placeholder">
        <span>{{ loading ? '正在读取波形…' : waveError ? '波形暂不可用，仍可播放' : '' }}</span>
      </div>
      <i class="waveform-playhead" :style="{ left: `${progress * 100}%` }" aria-hidden="true" />
      <input
        type="range"
        min="0"
        :max="duration || 1"
        step="0.01"
        :value="time"
        :disabled="!duration"
        aria-label="音频播放进度"
        :aria-valuetext="`${audioTime(time)} / ${audioTime(duration)}`"
        @input="seek($event.target.value)"
      />
    </div>
    <div class="audio-playback-controls">
      <span class="audio-time"
        >{{ audioTime(time) }}<span> / {{ audioTime(duration) }}</span></span
      >
      <button
        type="button"
        class="audio-play-toggle"
        :aria-label="playing ? '暂停音频' : '播放音频'"
        @click="toggle"
      >
        <WorkflowIcon :name="playing ? 'pause' : 'play'" />
      </button>
      <div class="audio-secondary-controls">
        <button type="button" aria-label="重播音频" title="回到开头" @click="seek(0)">
          <WorkflowIcon name="undo" />
        </button>
        <button
          type="button"
          class="audio-speed"
          aria-label="切换播放速度"
          title="切换播放速度"
          @click="speed"
        >
          {{ rate }}×
        </button>
        <a v-if="file" :href="src" :download="file" aria-label="下载音频" title="下载音频"
          ><WorkflowIcon name="download"
        /></a>
      </div>
    </div>
    <p v-if="playbackError" class="audio-error" role="status">{{ playbackError }}</p>
  </section>
</template>

<style scoped>
.audio-waveform-player {
  width: 100%;
  min-width: 0;
  padding: 12px 14px 9px;
  border: 1px solid var(--day-line-soft, #ffffff0c);
  border-radius: 12px;
  background: var(--day-inset, #ffffff03);
  color: var(--day-text, #dce0ed);
  cursor: default;
  touch-action: auto;
}
audio {
  display: none;
}
.waveform-track {
  position: relative;
  height: 72px;
  background: var(--day-hover, #ffffff03);
  border-radius: 7px;
  overflow: hidden;
}
.waveform-svg {
  width: 100%;
  height: 100%;
  padding: 6px 8px;
  box-sizing: border-box;
}
.waveform-bars {
  fill: var(--day-muted, #a8a1b7);
}
.waveform-played {
  fill: var(--day-accent, #c7adff);
}
.waveform-placeholder {
  display: grid;
  place-items: center;
  height: 100%;
  color: var(--day-faint, #9992a6);
  font-size: 11px;
}
.waveform-playhead {
  position: absolute;
  top: 8px;
  bottom: 8px;
  width: 1.5px;
  background: var(--day-accent, #c7adff);
  pointer-events: none;
}
.waveform-track input {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  margin: 0;
  padding: 0;
  opacity: 0;
  cursor: pointer;
}
.waveform-track:focus-within {
  outline: 2px solid var(--day-accent, #c7adff);
  outline-offset: 2px;
}
.audio-playback-controls {
  display: grid;
  grid-template-columns: 1fr auto 1fr;
  align-items: center;
  gap: 10px;
  margin-top: 8px;
}
.audio-time {
  font-size: 11px;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.audio-time > span {
  color: var(--day-faint, #9992a6);
}
.audio-secondary-controls {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 7px;
}
.audio-waveform-player button,
.audio-waveform-player a {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  padding: 0;
  border: 0;
  border-radius: 7px;
  color: var(--day-muted, #bdb7c9);
  background: transparent;
  cursor: pointer;
  font: inherit;
  font-size: 10px;
  text-decoration: none;
}
.audio-waveform-player button:hover,
.audio-waveform-player a:hover {
  background: var(--day-hover, #ffffff0b);
  color: var(--day-text, #ece5fa);
}
.audio-waveform-player button:focus-visible,
.audio-waveform-player a:focus-visible {
  outline: 2px solid var(--day-accent, #c7adff);
  outline-offset: 2px;
}
.audio-waveform-player .audio-play-toggle {
  width: 30px;
  height: 30px;
  border-radius: 50%;
  background: var(--day-accent-soft, #b9a0f51c);
  color: var(--day-accent, #c7adff);
}
.audio-waveform-player svg:not(.waveform-svg) {
  width: 14px;
  height: 14px;
}
.audio-waveform-player .audio-speed {
  width: 36px;
}
.audio-error {
  margin: 5px 0 0;
  font-size: 10px;
  color: var(--day-danger, #e4a29b);
}
.compact {
  padding: 14px 12px 10px;
  border: 0;
  border-radius: 0;
  background: transparent;
}
.compact .waveform-track {
  height: 96px;
}
.compact .audio-time {
  font-size: 10px;
}
.compact .audio-secondary-controls {
  gap: 2px;
}
.compact .audio-secondary-controls button,
.compact .audio-secondary-controls a {
  width: 23px;
}
.compact .audio-secondary-controls button:first-child {
  display: none;
}
</style>
