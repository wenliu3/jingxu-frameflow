<script setup>
defineProps({ name: { type: String, default: 'grid' } });
const paths = {
  sun: 'M16 12a4 4 0 1 1-8 0a4 4 0 0 1 8 0 M12 2v2 M12 20v2 M2 12h2 M20 12h2 M5 5l1.5 1.5 M17.5 17.5L19 19 M5 19l1.5-1.5 M17.5 6.5L19 5',
  moon: 'M20.8 13A9 9 0 1 1 11 3.2a7 7 0 0 0 9.8 9.8z',
  queue: 'M8 6h13 M8 12h13 M8 18h13 M3 6h.01 M3 12h.01 M3 18h.01',
  upload: 'M12 16V3 M7 8l5-5 5 5 M4 15v6h16v-6',
  shield: 'M12 3l8 3v6c0 5-8 9-8 9s-8-4-8-9V6z M8 12l3 3 5-6',
  grid: 'M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z',
  plus: 'M12 5v14 M5 12h14',
  shot: 'M3 7h18v14H3z M3 3h18v4H3z M7 3l3 4 M14 3l3 4 M9 11l6 3-6 3z',
  image: 'M3 4h18v16H3z M3 16l5-5 4 4 3-3 6 6 M8 8h.01',
  video: 'M3 5h18v14H3z M10 9l5 3-5 3z',
  note: 'M5 3h14v18H5z M8 8h8 M8 12h8 M8 16h5',
  link: 'M10 14l4-4 M8 16l-1 1a4 4 0 0 1-6-6l4-4a4 4 0 0 1 6 0 M16 8l1-1a4 4 0 0 1 6 6l-4 4a4 4 0 0 1-6 0',
  fit: 'M8 3H3v5 M16 3h5v5 M3 16v5h5 M21 16v5h-5 M8 8h8v8H8z',
  layout: 'M2 4h6v6H2z M16 2h6v6h-6z M16 16h6v6h-6z M8 7h4v12h4 M12 5h4',
  undo: 'M9 5L3 11l6 6 M3 11h11a7 7 0 0 1 7 7',
  redo: 'M15 5l6 6-6 6 M21 11H10a7 7 0 0 0-7 7',
  close: 'M6 6l12 12 M18 6L6 18',
  trash: 'M3 6h18 M9 6V3h6v3 M6 6l1 15h10l1-15 M10 10v7 M14 10v7',
  copy: 'M8 8h13v13H8z M16 8V3H3v13h5',
  download: 'M12 3v12 M7 10l5 5 5-5 M4 17v4h16v-4',
  arrow: 'M5 12h14 M14 7l5 5-5 5',
  search: 'M10 3a7 7 0 1 0 0 14a7 7 0 1 0 0-14 M15 15l6 6',
  play: 'M7 3l14 9-14 9z',
  settings: 'M3 6h18 M3 18h18 M8 3v6 M16 15v6',
  sidebar: 'M3 4h18v16H3z M9 4v16 M5 8h2 M5 12h2',
  more: 'M5 12h.01 M12 12h.01 M19 12h.01',
  audio: 'M9 5v14 M5 9v6 M13 2v20 M17 7v10 M21 10v4',
  help: 'M9 8a3 3 0 1 1 5 2c-1 1-2 1-2 3 M12 17h.01 M22 12a10 10 0 1 1-20 0a10 10 0 0 1 20 0',
  check: 'M5 12l4 4L19 6',
  hand: 'M8 12V5a2 2 0 0 1 4 0v7 M12 7a2 2 0 0 1 4 0v6 M16 9a2 2 0 0 1 4 0v8l-4 5H9l-6-9a2 2 0 0 1 3-2z',
  spark: 'M12 2l3 7 7 3-7 3-3 7-3-7-7-3 7-3z',
};
</script>
<template>
  <svg
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width="1.5"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
  >
    <path :d="paths[name] || paths.grid" />
  </svg>
</template>
<style scoped>
svg {
  width: 18px;
  height: 18px;
  flex: 0 0 auto;
}
</style>
