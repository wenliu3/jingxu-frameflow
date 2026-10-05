export const THEME_KEY = 'frameflow.theme';
export function readTheme(storage) {
  try {
    const theme = (storage || window.localStorage).getItem(THEME_KEY);
    return theme === 'light' ? 'light' : 'dark';
  } catch {
    return 'dark';
  }
}
export function applyTheme(theme) {
  const selected = theme === 'light' ? 'light' : 'dark';
  document.documentElement.dataset.theme = selected;
  document.documentElement.style.colorScheme = selected;
  document
    .querySelector('meta[name="theme-color"]')
    ?.setAttribute('content', selected === 'light' ? '#f6f5fa' : '#1b1b22');
  try {
    window.localStorage.setItem(THEME_KEY, selected);
  } catch {
    /* Switching still works when browser storage is unavailable. */
  }
  return selected;
}
