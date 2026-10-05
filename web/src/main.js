import { createApp } from 'vue';
import './style.css';
import './workspace-theme.css';
import './theme.css';
import { applyTheme, readTheme } from './theme.js';
import App from './App.vue';

applyTheme(readTheme());
createApp(App).mount('#app');
