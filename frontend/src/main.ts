import { createApp } from 'vue';
import App from './App.vue';
import { bootstrapSession } from './api';
import './styles.css';
import './deep-ui-theme.css';

async function main() {
  try {
    await bootstrapSession();
  } catch {
    // Subsequent protected requests surface the session error without ever
    // logging or persisting the one-time code.
  }
  createApp(App).mount('#app');
}

void main();
