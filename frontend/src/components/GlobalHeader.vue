<template>
  <a-layout-header class="header">
    <a-row :wrap="false">
      <!-- 左侧：Logo和标题 -->
      <a-col flex="200px" class="header-brand-col">
        <RouterLink to="/">
          <div class="header-left">
            <img class="logo" src="@/assets/logo.png" alt="Logo" />
            <h1 class="site-title">CluckAI</h1>
          </div>
        </RouterLink>
      </a-col>
      <!-- 中间：导航菜单 -->
      <a-col flex="auto" class="header-menu-col">
        <a-menu
          v-model:selectedKeys="selectedKeys"
          mode="horizontal"
          :items="menuItems"
          @click="handleMenuClick"
        />
      </a-col>
      <!-- 右侧：用户操作区域 -->
      <a-col class="header-user-col">
        <div class="user-login-status">
          <div v-if="loginUserStore.loginUser.id">
            <a-dropdown>
              <a-space>
                <a-avatar :src="loginUserStore.loginUser.userAvatar" />
                <span class="user-name">{{ loginUserStore.loginUser.userName ?? '无名' }}</span>
              </a-space>
              <template #overlay>
                <a-menu>
                  <a-menu-item @click="doLogout">
                    <LogoutOutlined />
                    退出登录
                  </a-menu-item>
                </a-menu>
              </template>
            </a-dropdown>
          </div>
          <div v-else>
            <a-button type="primary" href="/user/login">登录</a-button>
          </div>
        </div>
      </a-col>
    </a-row>
  </a-layout-header>
</template>

<script setup lang="ts">
import { computed, h, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { type MenuProps, message } from 'ant-design-vue'
import { useLoginUserStore } from '@/stores/loginUser.ts'
import { userLogout } from '@/api/userController.ts'
import { LogoutOutlined, HomeOutlined } from '@ant-design/icons-vue'

const loginUserStore = useLoginUserStore()
const router = useRouter()
const narrowMediaQuery = window.matchMedia('(max-width: 768px)')
const isNarrow = ref(narrowMediaQuery.matches)

const handleNarrowChange = (event: MediaQueryListEvent) => {
  isNarrow.value = event.matches
}

onMounted(() => narrowMediaQuery.addEventListener('change', handleNarrowChange))
onUnmounted(() => narrowMediaQuery.removeEventListener('change', handleNarrowChange))

// 当前选中菜单
const selectedKeys = ref<string[]>(['/'])
// 监听路由变化，更新当前选中菜单
router.afterEach((to) => {
  selectedKeys.value = [to.path]
})

// 菜单配置项
const originItems = [
  {
    key: '/',
    icon: () => h(HomeOutlined),
    label: '主页',
    title: '主页',
  },
  {
    key: '/admin/userManage',
    label: '用户管理',
    title: '用户管理',
  },
  {
    key: '/admin/appManage',
    label: '应用管理',
    title: '应用管理',
  },
  {
    key: '/admin/consumptionManage',
    label: '消费管理',
    title: '消费管理',
  },
  {
    key: '/admin/chatHistoryManage',
    label: '对话管理',
    title: '对话管理',
  },
]

// 过滤菜单项
const filterMenus = (menus = [] as MenuProps['items']) => {
  return menus?.filter((menu) => {
    // 窄屏只保留品牌和用户区域，不展示任何导航菜单。
    if (isNarrow.value) return false

    const menuKey = menu?.key as string
    if (menuKey?.startsWith('/admin')) {
      const loginUser = loginUserStore.loginUser
      if (!loginUser || loginUser.userRole !== 'admin') {
        return false
      }
    }
    return true
  })
}

// 展示在菜单的路由数组
const menuItems = computed<MenuProps['items']>(() => filterMenus(originItems))

// 处理菜单点击
const handleMenuClick: MenuProps['onClick'] = (e) => {
  const key = e.key as string
  selectedKeys.value = [key]
  // 跳转到对应页面
  if (key.startsWith('/')) {
    router.push(key)
  }
}

// 退出登录
const doLogout = async () => {
  const res = await userLogout()
  if (res.data.code === 0) {
    loginUserStore.setLoginUser({
      userName: '未登录',
    })
    message.success('退出登录成功')
    await router.push('/user/login')
  } else {
    message.error('退出登录失败，' + res.data.message)
  }
}
</script>

<style scoped>
.header {
  position: sticky;
  top: 0;
  z-index: 1000;
  width: 100%;
  height: var(--app-header-height, 64px);
  line-height: var(--app-header-height, 64px);
  background: rgba(255, 255, 255, 0.96);
  padding: 0 24px;
  box-shadow: 0 1px 0 rgba(28, 28, 25, 0.08);
  backdrop-filter: blur(10px);
}

.header-brand-col {
  flex: 0 0 200px;
  max-width: 200px;
}

.header-menu-col {
  min-width: 0;
}

.header-user-col {
  flex: 0 0 auto;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 12px;
}

.logo {
  height: 48px;
  width: 48px;
}

.site-title {
  margin: 0;
  font-size: 18px;
  color: #1890ff;
}

.user-login-status {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  white-space: nowrap;
}

.user-login-status :deep(.ant-space) {
  flex-wrap: nowrap;
  white-space: nowrap;
}

.ant-menu-horizontal {
  border-bottom: none !important;
}

@media (max-width: 768px) {
  .header {
    padding: 0 12px;
  }

  .header-brand-col {
    flex: 0 0 auto !important;
    max-width: none !important;
  }

  .header-menu-col {
    flex: 1 1 auto !important;
    min-width: 0;
  }

  .header-user-col {
    flex: 0 0 auto !important;
    max-width: none !important;
  }

  .header-left {
    gap: 8px;
  }

  .logo {
    width: 40px;
    height: 40px;
  }

  .site-title {
    font-size: 16px;
    white-space: nowrap;
  }

  .user-name {
    display: none;
  }

  .user-login-status {
    margin-left: 8px;
  }

  .header :deep(.ant-row) {
    align-items: center;
  }
}
</style>
