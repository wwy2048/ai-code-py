<template>
  <a-layout class="basic-layout" :class="{ 'basic-layout--fullscreen': isFullscreen }">
    <!-- 顶部导航栏 -->
    <GlobalHeader />
    <!-- 主要内容区域 -->
    <a-layout-content class="main-content">
      <router-view />
    </a-layout-content>
  </a-layout>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import GlobalHeader from '@/components/GlobalHeader.vue'

const route = useRoute()
const isFullscreen = computed(() => Boolean(route.meta.fullscreen))
</script>

<style scoped>
.basic-layout {
  background: none;
}

.main-content {
  width: 100%;
  min-height: 0;
  padding: 0;
  background: none;
  margin: 0;
}

.basic-layout--fullscreen {
  min-height: 100vh;
  min-height: 100dvh;
}

.basic-layout--fullscreen .main-content {
  flex: 1;
  overflow: hidden;
}
</style>
