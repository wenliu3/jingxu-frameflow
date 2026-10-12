const cache = new Map();

export function audioPeaks(buffer, count = 96) {
  const peaks = Array(count).fill(0);
  for (let channel = 0; channel < buffer.numberOfChannels; channel++) {
    const samples = buffer.getChannelData(channel);
    for (let i = 0; i < count; i++) {
      const start = Math.floor((i * samples.length) / count),
        end = Math.floor(((i + 1) * samples.length) / count);
      const step = Math.max(1, Math.floor((end - start) / 1200));
      for (let j = start; j < end; j += step) peaks[i] = Math.max(peaks[i], Math.abs(samples[j]));
    }
  }
  const maximum = Math.max(...peaks);
  return maximum ? peaks.map((peak) => peak / maximum) : peaks;
}
export function audioTime(value) {
  const seconds = Math.max(0, Math.floor(Number(value) || 0));
  return `${String(Math.floor(seconds / 60)).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`;
}
export function loadAudioWaveform(url) {
  if (cache.has(url)) return cache.get(url);
  const promise = (async () => {
    const response = await fetch(url);
    if (!response.ok) throw new Error('Audio unavailable');
    const bytes = await response.arrayBuffer();
    const decoder = new OfflineAudioContext(1, 1, 8000);
    const buffer = await decoder.decodeAudioData(bytes);
    return { peaks: audioPeaks(buffer), duration: buffer.duration };
  })();
  cache.set(url, promise);
  if (cache.size > 12) cache.delete(cache.keys().next().value);
  promise.catch(() => {
    if (cache.get(url) === promise) cache.delete(url);
  });
  return promise;
}
