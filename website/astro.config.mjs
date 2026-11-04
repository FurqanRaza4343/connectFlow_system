import { defineConfig } from 'astro/config';

export default defineConfig({
  site: process.env.PUBLIC_SITE_URL || 'http://connectflow.localhost:8080',
  output: 'static',
  compressHTML: true,
  build: { inlineStylesheets: 'auto' },
  vite: { build: { chunkSizeWarningLimit: 700 } },
});
