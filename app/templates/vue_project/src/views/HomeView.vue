<template>
  <main class="home">
    <h1>AI Vue App</h1>
    <p>等待生成内容。</p>
  </main>
</template>

<style scoped>
.home {
  display: grid;
  min-height: 100vh;
  place-content: center;
  padding: 48px 24px;
  text-align: center;
}

.home h1 {
  margin: 0 0 12px;
  font-size: clamp(2rem, 5vw, 4rem);
}

.home p {
  margin: 0;
  color: #64748b;
}
</style>
