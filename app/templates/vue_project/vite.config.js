import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { vueSourceLocator } from './vite-source-locator.js'

export default defineConfig({
  base: './',
  plugins: [vueSourceLocator(), vue()]
})
