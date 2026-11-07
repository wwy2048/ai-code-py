<script setup lang="ts">
import BasicLayout from '@/layouts/BasicLayout.vue'
</script>

<template>
  <BasicLayout />
</template>

<style>
/* 全局样式优化 */
:root {
  --app-header-height: 64px;
}

* {
  box-sizing: border-box;
}

body {
  margin: 0;
  padding: 0;
  font-family:
    -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Roboto', 'Oxygen', 'Ubuntu', 'Cantarell',
    'Fira Sans', 'Droid Sans', 'Helvetica Neue', sans-serif;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
  overflow-x: clip;
}

html {
  overflow-x: clip;
}

@media (max-width: 768px) {
  /* 聊天页移动端使用自带的“对话/预览”切换器，不再占用全局 Header 高度。 */
  .basic-layout--fullscreen > .header {
    display: none;
  }
}
</style>
