import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'vite';
import vue from '@vitejs/plugin-vue';
import { createSSRApp } from 'vue';
import { renderToString } from 'vue/server-renderer';

test('Vue dashboard renders its primary views', async () => {
  const vite = await createServer({ configFile: false, server: { middlewareMode: true }, plugins: [vue()] });
  try {
    const { default: App } = await vite.ssrLoadModule('/src/App.vue');
    const html = await renderToString(createSSRApp(App));
    for (const label of ['Good to have you here', 'Explore markets', 'Unattended trading', 'Copy trading', 'Limit monitor', 'Place paper buy order']) {
      assert.ok(html.includes(label), `Missing ${label}`);
    }
  } finally {
    await vite.close();
  }
});
