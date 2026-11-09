<template>
  <div id="appChatPage">
    <!-- 移动端视图切换器 -->
    <div class="mobile-view-switcher">
      <button
        type="button"
        class="mobile-view-tab"
        :class="{ active: mobileView === 'chat' }"
        :aria-pressed="mobileView === 'chat'"
        @click="mobileView = 'chat'"
      >
        <MessageOutlined />
        <span>对话</span>
      </button>
      <button
        type="button"
        class="mobile-view-tab"
        :class="{ active: mobileView === 'preview' }"
        :aria-pressed="mobileView === 'preview'"
        @click="mobileView = 'preview'"
      >
        <PlayCircleOutlined />
        <span>预览</span>
      </button>
    </div>

    <!-- 顶部栏 -->
    <div class="header-bar">
      <div class="header-left">
        <h1 class="app-name">{{ appInfo?.appName || '网站生成器' }}</h1>
        <a-tag v-if="appInfo?.codeGenType" color="blue" class="code-gen-type-tag">
          {{ formatCodeGenType(appInfo.codeGenType) }}
        </a-tag>
      </div>

      <div class="header-right">
        <a-button type="default" @click="showAppDetail">
          <template #icon>
            <InfoCircleOutlined />
          </template>
          应用详情
        </a-button>
        <a-button
          type="primary"
          ghost
          @click="downloadCode"
          :loading="downloading"
          :disabled="!isOwner"
        >
          <template #icon>
            <DownloadOutlined />
          </template>
          下载代码
        </a-button>

        <a-button
          type="primary"
          @click="deployApp"
          :loading="deploying"
          :disabled="!isOwner"
        >
          <template #icon>
            <CloudUploadOutlined />
          </template>
          部署按钮
        </a-button>
      </div>
    </div>

    <!-- 主要内容区域 -->
    <div class="main-content" :class="`mobile-view-${mobileView}`">
      <!-- 左侧对话区域 -->
      <div class="chat-section">
        <!-- 消息区域 -->
        <div
          class="messages-container"
          :class="{ 'messages-container--initializing': !historyReady }"
          ref="messagesContainer"
          @scroll="handleHistoryScroll"
        >
          <!-- 加载更多按钮 -->
          <div v-if="hasMoreHistory" class="load-more-container">
            <a-button
              type="default"
              size="small"
              @click="fetchChatHistory"
              :loading="loadingHistory"
            >
              加载更多
            </a-button>
          </div>

          <div v-for="(message, index) in messages" :key="message.commandId || message.id || index" class="message-item">
            <div v-if="message.type === 'user'" class="user-message">
              <div class="message-content">
                <div v-if="getUserMessageView(message).element" class="visual-prompt-content">
                  <div>请对以下选中元素进行修改：</div>
                  <div>修改需求：{{ getUserMessageView(message).request }}</div>
                  <div
                    class="visual-element-chip"
                    :title="getUserMessageView(message).element?.selector"
                  >
                    <AimOutlined />
                    <code>{{ formatVisualElementLabel(getUserMessageView(message).element) }}</code>
                  </div>
                </div>
                <template v-else>{{ message.content }}</template>
              </div>
              <div class="message-avatar">
                <a-avatar :src="loginUserStore.loginUser.userAvatar" />
              </div>
            </div>
            <div v-else class="ai-message">
              <div class="message-avatar">
                <a-avatar :src="aiAvatar" />
              </div>
              <div class="message-content">
                <div
                  v-for="(block, blockIndex) in message.blocks || []"
                  :key="`${message.id || index}-${block.type === 'tool_group' ? block.groupId : block.batchId || blockIndex}-${blockIndex}`"
                  class="assistant-block"
                >
                  <div
                    v-if="block.type === 'text' && block.content && block.streaming"
                    class="streaming-text"
                  >{{ block.content }}</div>
                  <MarkdownRenderer
                    v-else-if="block.type === 'text' && block.content"
                    :content="block.content"
                  />
                  <ToolActivityGroup
                    v-else-if="block.type === 'tool_group' && block.tools?.length"
                    :activities="block.tools"
                    :running="block.running"
                    :turn-active="message.turnId === activeTurnId && isGenerating"
                  />
                </div>
                <MarkdownRenderer
                  v-if="message.content && !message.blocks?.length"
                  :content="message.content"
                />
                <div v-if="message.loading" class="loading-indicator">
                  <a-spin size="small" />
                  <span>AI 正在思考...</span>
                </div>
              </div>
            </div>
          </div>
        </div>

        <!-- 用户消息输入框与 Followup 队列 -->
        <div class="input-container">
          <div v-if="hasVisibleQueue" class="queue-panel">
            <div
              v-for="item in queuedItems"
              :key="item.commandId"
              class="queue-row"
            >
              <div class="queue-row-content">
                <span class="queue-row-icon">↳</span>
                <a-tooltip :title="getQueueDisplayMessage(item.message)" placement="topLeft">
                  <span class="queue-row-text">{{ getQueueDisplayMessage(item.message) }}</span>
                </a-tooltip>
              </div>
              <div class="queue-row-actions">
                <a-button type="text" size="small" @click="steerQueuedItem(item)">
                  Steer
                </a-button>
                <a-tooltip title="删除">
                  <a-button type="text" size="small" @click="deleteQueuedItem(item)">
                    <template #icon><DeleteOutlined /></template>
                  </a-button>
                </a-tooltip>
              </div>
            </div>

            <div v-if="pausedItems.length" class="paused-queue">
              <div class="queue-paused-header">
                <div class="queue-paused-title">
                  <PauseOutlined />
                  <span>Queue paused because you interrupted</span>
                </div>
                <a-button type="text" size="small" @click="resumeQueue">
                  <template #icon><CaretRightOutlined /></template>
                  Resume
                </a-button>
              </div>
              <div
                v-for="(item, index) in pausedItems"
                :key="item.commandId"
                class="queue-row"
              >
                <div class="queue-row-content">
                  <span class="queue-row-icon">↳</span>
                  <a-tooltip :title="getQueueDisplayMessage(item.message)" placement="topLeft">
                    <span class="queue-row-text">{{ getQueueDisplayMessage(item.message) }}</span>
                  </a-tooltip>
                </div>
                <div class="queue-row-actions">
                  <a-button
                    v-if="index > 0"
                    type="text"
                    size="small"
                    @click="steerQueuedItem(item)"
                  >
                    Steer
                  </a-button>
                  <a-tooltip title="删除">
                    <a-button type="text" size="small" @click="deleteQueuedItem(item)">
                      <template #icon><DeleteOutlined /></template>
                    </a-button>
                  </a-tooltip>
                </div>
              </div>
            </div>
          </div>

          <a-alert
            v-if="selectedElement"
            class="visual-selection-alert"
            type="info"
            show-icon
            closable
            :message="`已选中 ${formatVisualElementLabel(selectedElement)}`"
            :description="getSelectedElementDescription(selectedElement)"
            @close="clearSelectedElement"
          />

          <div class="input-wrapper" :class="{ 'has-visual-edit': isOwner }">
            <a-tooltip v-if="!isOwner" title="无法在别人的作品下对话哦~" placement="top">
              <a-textarea
                v-model:value="userInput"
                placeholder="请描述你想生成的网站，越详细效果越好哦"
                :auto-size="{ minRows: 2, maxRows: 6 }"
                :maxlength="1000"
                @keydown.enter.exact.prevent="handleComposerSend"
                :disabled="true"
              />
            </a-tooltip>
            <a-textarea
              v-else
              v-model:value="userInput"
              placeholder="请描述你想生成的网站，越详细效果越好哦"
              :auto-size="{ minRows: 2, maxRows: 6 }"
              :maxlength="1000"
              @keydown.enter.exact.prevent="handleComposerSend"
            />
            <div class="input-actions">
              <a-tooltip v-if="isOwner" :title="isEditing ? '退出可视化编辑' : '在预览中选择元素'">
                <a-button
                  class="visual-edit-action"
                  :class="{ active: isEditing }"
                  :disabled="!canUseVisualEditor"
                  @click="toggleVisualEditing"
                >
                  <template #icon><AimOutlined /></template>
                  {{ isEditing ? '退出编辑' : '编辑' }}
                </a-button>
              </a-tooltip>
              <a-button
                v-if="isGenerating && !userInput.trim() && isOwner"
                class="composer-action stop-action"
                :loading="stopping"
                @click="stopGeneration"
              >
                <span class="stop-square"></span>
              </a-button>
              <a-button
                v-else
                type="primary"
                class="composer-action send-action"
                :loading="queueSending || sendingMessage"
                :disabled="!isOwner || !userInput.trim()"
                @click="handleComposerSend"
              >
                <template #icon><SendOutlined /></template>
              </a-button>
            </div>
          </div>
        </div>
      </div>

      <!-- 右侧网页展示区域 -->
      <div class="preview-section">
        <div class="preview-header">
          <h3>生成后的网页展示</h3>
          <div class="preview-actions">
            <a-button v-if="previewUrl" type="link" @click="openInNewTab">
              <template #icon>
                <ExportOutlined />
              </template>
              新窗口打开
            </a-button>
          </div>
        </div>
        <div class="preview-content">
          <div v-if="!previewUrl && !isGenerating" class="preview-placeholder">
            <div class="placeholder-icon">🌐</div>
            <p>网站文件生成完成后将在这里展示</p>
          </div>
          <div v-else-if="isGenerating" class="preview-loading">
            <a-spin size="large" />
            <p>正在生成网站...</p>
          </div>
          <iframe
            v-else
            ref="previewIframe"
            :src="previewUrl"
            class="preview-iframe"
            title="生成网站预览"
            frameborder="0"
            @load="onIframeLoad"
          ></iframe>
        </div>

        <a-button
          class="mobile-preview-refresh"
          shape="circle"
          :loading="refreshingPreview"
          :disabled="!previewUrl || isGenerating"
          aria-label="刷新预览"
          @click="refreshPreview"
        >
          <template v-if="!refreshingPreview" #icon><ReloadOutlined /></template>
        </a-button>

        <div class="mobile-preview-footer">
          <a-button
            class="mobile-deploy-action"
            :loading="deploying"
            :disabled="!isOwner"
            @click="deployApp"
          >
            <template #icon><CloudUploadOutlined /></template>
            部署
          </a-button>
        </div>
      </div>
    </div>

    <!-- 存在暂停队列时的新消息确认 -->
    <a-modal
      v-model:open="queueConflictVisible"
      title="存在暂停的 Followup"
      :footer="null"
      :closable="true"
      @cancel="cancelQueueConflict"
    >
      <p class="queue-conflict-description">
        当前有 {{ pausedItems.length }} 条因中断暂停的 Followup。要清空队列，还是保留队列并发送？
      </p>
      <div class="queue-conflict-actions">
        <a-button danger @click="resolveQueueConflict(true)">Clear</a-button>
        <a-button type="primary" @click="resolveQueueConflict(false)">发送</a-button>
      </div>
    </a-modal>

    <!-- 应用详情弹窗 -->
    <AppDetailModal
      v-model:open="appDetailVisible"
      :app="appInfo"
      :show-actions="isOwner || isAdmin"
      @edit="editApp"
      @delete="deleteApp"
    />

    <!-- 部署成功弹窗 -->
    <DeploySuccessModal
      v-model:open="deployModalVisible"
      :deploy-url="deployUrl"
      @open-site="openDeployedSite"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, nextTick, onUnmounted, computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import { useLoginUserStore } from '@/stores/loginUser'
import {
  getAppVoById,
  deployApp as deployAppApi,
  deleteApp as deleteAppApi,
} from '@/api/appController'
import { listAppChatHistory } from '@/api/chatHistoryController'
import {
  connectGenerationQueue,
  createRequestId,
  sendQueueControl,
  sendTurnControl,
  streamGeneration,
  type ActiveTurnSnapshot,
  type QueuedItem,
  type QueueSnapshot,
  type StreamEvent,
  type UserMessageConsumedEvent,
} from '@/api/generationStream'
import { CodeGenTypeEnum, formatCodeGenType } from '@/utils/codeGenTypes'
import {
  useVisualEditor,
  type VisualElementInfo,
} from '@/composables/useVisualEditor'
import request from '@/request'

import MarkdownRenderer from '@/components/MarkdownRenderer.vue'
import ToolActivityGroup from '@/components/ToolActivityGroup.vue'
import AppDetailModal from '@/components/AppDetailModal.vue'
import DeploySuccessModal from '@/components/DeploySuccessModal.vue'
import aiAvatar from '@/assets/aiAvatar.png'
import { API_BASE_URL, getDeployUrl, getStaticPreviewUrl } from '@/config/env'
import { openExternalUrl } from '@/utils/openExternal'

import {
  AimOutlined,
  CaretRightOutlined,
  CloudUploadOutlined,
  DeleteOutlined,
  DownloadOutlined,
  ExportOutlined,
  InfoCircleOutlined,
  MessageOutlined,
  PauseOutlined,
  PlayCircleOutlined,
  ReloadOutlined,
  SendOutlined,
} from '@ant-design/icons-vue'

const route = useRoute()
const router = useRouter()
const loginUserStore = useLoginUserStore()

// 应用信息
const appInfo = ref<API.AppVO>()
const appId = ref<string>()

// 对话相关
interface TextChatBlock {
  type: 'text'
  batchId?: string
  content: string
  streaming?: boolean
}

interface ToolGroupChatBlock {
  type: 'tool_group'
  groupId: string
  tools: API.ToolActivity[]
  running?: boolean
}

type ChatBlock = TextChatBlock | ToolGroupChatBlock

interface Message {
  type: 'user' | 'ai'
  content: string
  blocks?: ChatBlock[]
  loading?: boolean
  id?: number
  createTime?: string
  turnId?: string
  commandId?: string
  originalContent?: string
  visualElement?: VisualElementInfo
}

const messages = ref<Message[]>([])
const userInput = ref('')
const isGenerating = ref(false)
const activeTurnId = ref<string>()
const stopping = ref(false)
const sendingMessage = ref(false)
const queueSending = ref(false)
const queuedItems = ref<QueuedItem[]>([])
const pausedItems = ref<QueuedItem[]>([])
const queueConflictVisible = ref(false)
const pendingQueueMessage = ref('')
const consumedCommandIds = new Set<string>()
const queueVersion = ref(0)
const activeTurnSnapshot = ref<ActiveTurnSnapshot | null>(null)
const resumingTurnId = ref<string>()
const streamGapTurnIds = new Set<string>()
let queueEventSource: EventSource | null = null
const messagesContainer = ref<HTMLElement>()
const historyReady = ref(false)
const hasInitialConversation = ref(false) // 标记是否已经进行过初始对话
const hasVisibleQueue = computed(() => queuedItems.value.length > 0 || pausedItems.value.length > 0)

// 对话历史相关
const chatHistory = ref<API.ChatHistory[]>([])
const hasMoreHistory = ref(true)
const lastCreateTime = ref<string | undefined>()
const lastId = ref<number | undefined>()
const loadingHistory = ref(false)

// 预览相关
const previewUrl = ref('')
const previewReady = ref(false)
const previewIframe = ref<HTMLIFrameElement>()
const mobileView = ref<'chat' | 'preview'>('chat')
const refreshingPreview = ref(false)
let previewRevision = 0

const {
  isEditing,
  selectedElement,
  toggleEditing,
  exitEditing,
  handleIframeLoad: handleVisualEditorIframeLoad,
  clearSelectedElement,
  buildVisualPrompt,
  parseVisualPrompt,
  formatVisualElementLabel,
} = useVisualEditor({
  iframeRef: previewIframe,
  previewUrl,
})

// 部署相关
const deploying = ref(false)
const deployModalVisible = ref(false)
const deployUrl = ref('')

// 权限相关
const isOwner = computed(() => {
  return appInfo.value?.userId === loginUserStore.loginUser.id
})

const canUseVisualEditor = computed(() => {
  return isOwner.value && Boolean(previewUrl.value) && !isGenerating.value
})

const getUserMessageView = (item: Message) => {
  const parsed = parseVisualPrompt(item.content)
  return {
    request: item.originalContent ?? parsed.request,
    element: item.visualElement ?? parsed.element,
  }
}

const getQueueDisplayMessage = (content: string) => {
  return parseVisualPrompt(content).request
}

const getSelectedElementDescription = (element?: VisualElementInfo) => {
  if (!element) return ''
  const details = [element.selector]
  if (element.text) details.push(`文本：${element.text}`)
  return details.join(' · ')
}

const toggleVisualEditing = () => {
  if (!canUseVisualEditor.value) return
  toggleEditing()
}

const isAdmin = computed(() => {
  return loginUserStore.loginUser.userRole === 'admin'
})

// 应用详情相关
const appDetailVisible = ref(false)

// 显示应用详情
const showAppDetail = () => {
  appDetailVisible.value = true
}

// 将后端有序 parts 转换为前端渲染块
const toChatBlocks = (parts: API.ChatTurnPart[] | undefined): ChatBlock[] => {
  return (parts || []).flatMap((part): ChatBlock[] => {
    if (part.type === 'assistant_text') {
      const content = String(part.content || '')
      return content ? [{ type: 'text', batchId: part.batchId, content }] : []
    }
    const tools = part.tools || []
    return tools.length
      ? [{ type: 'tool_group', groupId: part.groupId, tools, running: false }]
      : []
  })
}

const waitAnimationFrame = () =>
  new Promise<void>((resolve) => window.requestAnimationFrame(() => resolve()))

// 首次加载时等待内容高度稳定，再无动画定位到底部。
const scrollHistoryToBottom = async () => {
  const container = messagesContainer.value
  if (!container) return

  const previousBehavior = container.style.scrollBehavior
  container.style.scrollBehavior = 'auto'
  let previousHeight = -1
  let stableFrames = 0

  for (let frame = 0; frame < 12; frame += 1) {
    container.scrollTop = container.scrollHeight
    await waitAnimationFrame()
    const currentHeight = container.scrollHeight
    if (currentHeight === previousHeight) {
      stableFrames += 1
      if (stableFrames >= 2) break
    } else {
      previousHeight = currentHeight
      stableFrames = 0
    }
  }

  container.scrollTop = container.scrollHeight
  container.style.scrollBehavior = previousBehavior
}

// 获取对话历史
const fetchChatHistory = async () => {
  if (!appId.value) return

  const container = messagesContainer.value
  const preserveScroll = lastCreateTime.value !== undefined && container !== undefined
  const previousScrollHeight = container?.scrollHeight ?? 0
  const previousScrollTop = container?.scrollTop ?? 0
  loadingHistory.value = true
  try {
    const res = await listAppChatHistory({
      appId: parseInt(appId.value),
      lastCreateTime: lastCreateTime.value,
      lastId: lastId.value,
    })

    if (res.data.code === 0 && res.data.data) {
      let records = res.data.data.records || []

      if (records.length === 0) {
        hasMoreHistory.value = false
        return
      }

      // 反转records顺序，使其按时间正序排列（最早的消息在前面，最新的消息在后面）
      records = records.reverse()

      // 更新对话历史
      chatHistory.value = [...records, ...chatHistory.value]

      // 更新最后一条消息的创建时间，用于下一次分页查询
      // 由于records已经反转，records[0]是最早的消息，records[records.length - 1]是最新的消息
      lastCreateTime.value = records[0].createTime
      lastId.value = records[0].id

      // 将对话历史按 Turn 重建为一个 AI 气泡，内部保持文本/工具批次顺序。
      const partsByTurn = res.data.data.partsByTurn || {}
      const visibleAssistantTurns = new Set(
        records
          .filter(record => record.messageType !== 'user' && record.turnId)
          .map(record => record.turnId as string)
      )
      const renderedTurnIds = new Set(
        messages.value
          .filter(message => message.type === 'ai' && message.turnId)
          .map(message => message.turnId as string)
      )
      const historyMessages: Message[] = []
      const existingUserKeys = new Set(
        messages.value
          .filter((item) => item.type === 'user')
          .map((item) => `${item.turnId || ''}::${item.content}`)
      )

      for (const record of records) {
        const turnId = record.turnId || ''
        if (record.messageType === 'user') {
          const userKey = `${turnId}::${record.message || ''}`
          if (!existingUserKeys.has(userKey)) {
            historyMessages.push({
              type: 'user',
              content: record.message || '',
              id: record.id,
              createTime: record.createTime,
              turnId: record.turnId,
            })
            existingUserKeys.add(userKey)
          }
          if (
            turnId &&
            !visibleAssistantTurns.has(turnId) &&
            !renderedTurnIds.has(turnId) &&
            partsByTurn[turnId]?.length
          ) {
            historyMessages.push({
              type: 'ai',
              content: '',
              createTime: record.createTime,
              turnId,
              blocks: toChatBlocks(partsByTurn[turnId]),
            })
            renderedTurnIds.add(turnId)
          }
          continue
        }

        if (!turnId || renderedTurnIds.has(turnId)) continue
        const blocks = toChatBlocks(partsByTurn[turnId])
        if (!blocks.length && record.message) {
          blocks.push({ type: 'text', batchId: 'legacy', content: record.message })
        }
        if (!blocks.length) continue
        historyMessages.push({
          type: 'ai',
          content: record.message || '',
          id: record.id,
          createTime: record.createTime,
          turnId,
          blocks,
        })
        renderedTurnIds.add(turnId)
      }

      // 将历史消息添加到消息列表的开头
      messages.value = [...historyMessages, ...messages.value]

      if (preserveScroll && container) {
        await nextTick()
        container.scrollTop = container.scrollHeight - previousScrollHeight + previousScrollTop
      } else if (container) {
        // 首次加载历史消息时默认定位到最新消息。
        await nextTick()
        await scrollHistoryToBottom()
      }

      // 如果没有更多历史消息，更新标志
      if (records.length < 10) {
        hasMoreHistory.value = false
      }
    }
  } catch (error) {
    console.error('获取对话历史失败：', error)
    message.error('获取对话历史失败')
  } finally {
    loadingHistory.value = false
    historyReady.value = true
  }
}

// 滚动到顶部时自动加载更早的对话历史
const handleHistoryScroll = () => {
  const container = messagesContainer.value
  if (!container || !hasMoreHistory.value || loadingHistory.value) return
  if (container.scrollTop <= 40) {
    void fetchChatHistory()
  }
}

// 获取应用信息
const fetchAppInfo = async () => {
  const id = route.params.id as string
  if (!id) {
    message.error('应用ID不存在')
    router.push('/')
    return
  }

  appId.value = id

  try {
    const res = await getAppVoById({ id: id as unknown as number })
    if (res.data.code === 0 && res.data.data) {
      appInfo.value = res.data.data
      if (appInfo.value.deployKey) {
        deployUrl.value = getDeployUrl(appInfo.value.deployKey)
      }
      if (isOwner.value) {
        connectQueueEvents()
      }

      // 获取对话历史
      await fetchChatHistory()
      if (isOwner.value) {
        await maybeResumeActiveTurn()
      }

      // 检查是否有对话历史
      const hasHistory = chatHistory.value.length > 0

      // 更新预览URL
      if (hasHistory) {
        await updatePreviewIfAvailable()
      }

      // 自动发送初始提示词（如果是自己的app，并且没有对话历史）
      if (appInfo.value.initPrompt && isOwner.value && !hasHistory) {
        hasInitialConversation.value = true
        await sendInitialMessage(appInfo.value.initPrompt)
      }
    } else {
      message.error('获取应用信息失败')
      router.push('/')
    }
  } catch (error) {
    console.error('获取应用信息失败：', error)
    message.error('获取应用信息失败')
    router.push('/')
  }
}

// 发送初始消息
const sendInitialMessage = async (prompt: string) => {
  await startDirectMessage(prompt)
}

const startDirectMessage = async (content: string, clearPaused = false): Promise<boolean> => {
  if (!appId.value || sendingMessage.value || !isOwner.value) return false
  sendingMessage.value = true
  try {
    const baseURL = request.defaults.baseURL || API_BASE_URL
    if (clearPaused) {
      await sendQueueControl({
        baseURL,
        appId: Number(appId.value),
        action: 'clear_paused',
        commandId: createRequestId(),
      })
    }
    const parsed = parseVisualPrompt(content)
    userInput.value = ''
    messages.value.push({
      type: 'user',
      content,
      originalContent: parsed.request,
      visualElement: parsed.element,
    })
    const aiMessageIndex = messages.value.length
    messages.value.push({
      type: 'ai',
      content: '',
      blocks: [],
      loading: true,
    })
    await nextTick()
    scrollToBottom()
    isGenerating.value = true
    void generateCode(content, aiMessageIndex)
    sendingMessage.value = false
    return true
  } catch (error) {
    console.error('发送消息失败：', error)
    message.error(error instanceof Error ? error.message : '发送消息失败')
    sendingMessage.value = false
    return false
  }
}

const enqueueFollowup = async (content: string, clearPaused = false): Promise<boolean> => {
  if (!appId.value || queueSending.value || !isOwner.value) return false
  queueSending.value = true
  try {
    const baseURL = request.defaults.baseURL || API_BASE_URL
    const ack = await sendQueueControl({
      baseURL,
      appId: Number(appId.value),
      action: 'enqueue',
      commandId: createRequestId(),
      message: content,
      clearPaused,
    })
    applyQueueSnapshot(ack.queue)
    userInput.value = ''
    return true
  } catch (error) {
    console.error('排队消息发送失败：', error)
    message.error(error instanceof Error ? error.message : '排队消息发送失败')
    return false
  } finally {
    queueSending.value = false
  }
}

const handleComposerSend = async () => {
  const content = userInput.value.trim()
  if (!content || !isOwner.value || queueSending.value || sendingMessage.value) return

  const prompt = selectedElement.value
    ? buildVisualPrompt(content, selectedElement.value)
    : content

  if (pausedItems.value.length > 0) {
    pendingQueueMessage.value = prompt
    queueConflictVisible.value = true
    return
  }

  const sent = isGenerating.value
    ? await enqueueFollowup(prompt)
    : await startDirectMessage(prompt)

  if (sent) exitEditing()
}

const cancelQueueConflict = () => {
  queueConflictVisible.value = false
  pendingQueueMessage.value = ''
}

const resolveQueueConflict = async (clearPaused: boolean) => {
  const content = pendingQueueMessage.value
  queueConflictVisible.value = false
  pendingQueueMessage.value = ''
  if (!content) return

  const sent = isGenerating.value
    ? await enqueueFollowup(content, clearPaused)
    : await startDirectMessage(content, clearPaused)

  if (sent) exitEditing()
}

const applyActiveTurn = (snapshot: ActiveTurnSnapshot | null) => {
  activeTurnSnapshot.value = snapshot
  if (!snapshot) return

  if (snapshot.active && snapshot.status === 'running') {
    void maybeResumeActiveTurn()
    return
  }

  if (activeTurnId.value === snapshot.turnId) {
    isGenerating.value = false
    activeTurnId.value = undefined
  }
  streamGapTurnIds.delete(snapshot.turnId)
  if (snapshot.status === 'completed') {
    void updatePreviewIfAvailable()
  }
}

const maybeResumeActiveTurn = async () => {
  const snapshot = activeTurnSnapshot.value
  if (
    !isOwner.value ||
    !snapshot?.active ||
    snapshot.status !== 'running' ||
    !historyReady.value ||
    !appId.value
  ) {
    return
  }
  if (!snapshot.replayableFromZero) {
    message.warning('本轮事件过多，无法从头重放，已切换到历史快照')
    return
  }
  if (
    streamGapTurnIds.has(snapshot.turnId) ||
    resumingTurnId.value === snapshot.turnId
  ) {
    return
  }

  const userIndex = messages.value.findIndex(
    item => item.type === 'user' && item.turnId === snapshot.turnId,
  )
  if (userIndex < 0) return
  const userMessage = messages.value[userIndex]
  let aiIndex = messages.value.findIndex(
    item => item.type === 'ai' && item.turnId === snapshot.turnId,
  )
  if (aiIndex < 0) {
    aiIndex = userIndex + 1
    messages.value.splice(aiIndex, 0, {
      type: 'ai',
      content: '',
      blocks: [],
      loading: true,
      turnId: snapshot.turnId,
    })
  } else {
    messages.value[aiIndex] = {
      ...messages.value[aiIndex],
      content: '',
      blocks: [],
      loading: true,
      turnId: snapshot.turnId,
    }
  }

  resumingTurnId.value = snapshot.turnId
  isGenerating.value = true
  activeTurnId.value = snapshot.turnId
  await nextTick()
  scrollToBottom()
  void generateCode(userMessage.content, aiIndex, snapshot.turnId).finally(() => {
    if (resumingTurnId.value === snapshot.turnId) resumingTurnId.value = undefined
  })
}
const applyQueueSnapshot = (snapshot: QueueSnapshot) => {
  const version = Number(snapshot.version || 0)
  if (version < queueVersion.value) return
  queueVersion.value = version
  applyActiveTurn(snapshot.activeTurn ?? null)
  queuedItems.value = snapshot.queuedItems || []
  pausedItems.value = snapshot.pausedItems || []
}

const appendConsumedUserMessage = (event: UserMessageConsumedEvent) => {
  if (consumedCommandIds.has(event.commandId)) {
    applyQueueSnapshot(event.queue)
    return false
  }
  consumedCommandIds.add(event.commandId)
  const parsed = parseVisualPrompt(event.text)
  messages.value.push({
    type: 'user',
    content: event.text,
    originalContent: parsed.request,
    visualElement: parsed.element,
    turnId: event.turnId,
    commandId: event.commandId,
  })
  applyQueueSnapshot(event.queue)
  return true
}

const handleUserMessageConsumed = (event: UserMessageConsumedEvent) => {
  if (!isOwner.value) return
  const isNew = appendConsumedUserMessage(event)
  if (!isNew) return
  scrollToBottom()
  if (event.mode !== 'followup') return
  const alreadyAttached = messages.value.some(
    (item) => item.type === 'ai' && item.turnId === event.turnId
  )
  if (alreadyAttached) return
  const aiMessageIndex = messages.value.length
  messages.value.push({
    type: 'ai',
    content: '',
    blocks: [],
    loading: true,
    turnId: event.turnId,
  })
  isGenerating.value = true
  activeTurnId.value = event.turnId
  void nextTick().then(() => scrollToBottom())
  void generateCode(event.text, aiMessageIndex, event.turnId)
}

const connectQueueEvents = () => {
  queueEventSource?.close()
  if (!appId.value || !isOwner.value) return
  const baseURL = request.defaults.baseURL || API_BASE_URL
  queueEventSource = connectGenerationQueue({
    baseURL,
    appId: Number(appId.value),
    handlers: {
      onSnapshot: applyQueueSnapshot,
      onChanged: applyQueueSnapshot,
      onConsumed: handleUserMessageConsumed,
      onActiveTurnChanged: applyActiveTurn,
      onError: () => {
        console.warn('生成队列事件流连接中断，浏览器将自动重连')
      },
    },
  })
}

const steerQueuedItem = async (item: QueuedItem) => {
  if (!appId.value || queueSending.value) return
  queueSending.value = true
  try {
    const baseURL = request.defaults.baseURL || API_BASE_URL
    const ack = await sendQueueControl({
      baseURL,
      appId: Number(appId.value),
      action: 'steer',
      commandId: createRequestId(),
      itemId: item.commandId,
    })
    applyQueueSnapshot(ack.queue)
  } catch (error) {
    console.error('Steer 失败：', error)
    message.error(error instanceof Error ? error.message : 'Steer 失败')
  } finally {
    queueSending.value = false
  }
}

const deleteQueuedItem = async (item: QueuedItem) => {
  if (!appId.value || queueSending.value) return
  queueSending.value = true
  try {
    const baseURL = request.defaults.baseURL || API_BASE_URL
    const ack = await sendQueueControl({
      baseURL,
      appId: Number(appId.value),
      action: 'delete',
      commandId: createRequestId(),
      itemId: item.commandId,
    })
    applyQueueSnapshot(ack.queue)
  } catch (error) {
    console.error('删除排队消息失败：', error)
    message.error(error instanceof Error ? error.message : '删除排队消息失败')
  } finally {
    queueSending.value = false
  }
}

const resumeQueue = async () => {
  if (!appId.value || queueSending.value) return
  queueSending.value = true
  try {
    const baseURL = request.defaults.baseURL || API_BASE_URL
    const ack = await sendQueueControl({
      baseURL,
      appId: Number(appId.value),
      action: 'resume',
      commandId: createRequestId(),
    })
    applyQueueSnapshot(ack.queue)
  } catch (error) {
    console.error('恢复队列失败：', error)
    message.error(error instanceof Error ? error.message : '恢复队列失败')
  } finally {
    queueSending.value = false
  }
}

// 生成代码 - SSE 协议由 API 客户端处理
let activeAbortController: AbortController | null = null

const generateCode = async (
  userMessage: string,
  aiMessageIndex: number,
  existingRequestId?: string
) => {
  const baseURL = request.defaults.baseURL || API_BASE_URL
  activeAbortController = new AbortController()
  const requestId = existingRequestId || createRequestId()
  activeTurnId.value = requestId
  messages.value[aiMessageIndex].turnId = requestId

  const getBlocks = () => {
    const messageItem = messages.value[aiMessageIndex]
    if (!messageItem.blocks) messageItem.blocks = []
    return messageItem.blocks
  }

  const refreshContent = () => {
    const messageItem = messages.value[aiMessageIndex]
    messageItem.content = getBlocks()
      .filter((block): block is TextChatBlock => block.type === 'text')
      .map(block => block.content)
      .join('\n\n')
  }

  let liveToolGroupSeq = 0
  let pendingRenderText = ''
  let activeTextBlock: TextChatBlock | null = null
  let typewriterFrame: number | null = null

  const scheduleTypewriter = () => {
    if (typewriterFrame !== null) return
    typewriterFrame = window.requestAnimationFrame(renderTypewriterFrame)
  }

  const renderTypewriterFrame = () => {
    typewriterFrame = null
    if (!activeTextBlock || !pendingRenderText) return
    const backlog = pendingRenderText.length
    const take = backlog > 48 ? 6 : backlog > 16 ? 3 : 1
    activeTextBlock.content += pendingRenderText.slice(0, take)
    pendingRenderText = pendingRenderText.slice(take)
    refreshContent()
    scrollToBottom()
    if (pendingRenderText) scheduleTypewriter()
  }

  const flushTypewriter = (finalize = false) => {
    if (typewriterFrame !== null) {
      window.cancelAnimationFrame(typewriterFrame)
      typewriterFrame = null
    }
    if (activeTextBlock && pendingRenderText) {
      activeTextBlock.content += pendingRenderText
      pendingRenderText = ''
      refreshContent()
      scrollToBottom()
    }
    if (finalize && activeTextBlock) {
      activeTextBlock.streaming = false
      activeTextBlock = null
    }
  }

  const cancelTypewriter = () => {
    if (typewriterFrame !== null) {
      window.cancelAnimationFrame(typewriterFrame)
      typewriterFrame = null
    }
    pendingRenderText = ''
    activeTextBlock = null
  }

  // 后端 chunk 先进入显示缓冲，再由 rAF 按帧吐出，避免合并后的整块文本一次上屏。
  const appendText = (content: string, batchId = '') => {
    if (!content) return
    const blocks = getBlocks()
    const last = blocks[blocks.length - 1]
    let block: TextChatBlock
    if (last?.type === 'text' && (last.batchId || '') === batchId) {
      block = last
    } else {
      flushTypewriter(true)
      block = { type: 'text', batchId: batchId || undefined, content: '', streaming: true }
      blocks.push(block)
    }
    if (activeTextBlock && activeTextBlock !== block) flushTypewriter(true)
    activeTextBlock = block
    block.streaming = true
    pendingRenderText += content
    messages.value[aiMessageIndex].loading = false
    scheduleTypewriter()
  }

  // 连续工具调用无论来自几个 batch，都归入文本边界内的同一个折叠组。
  const ensureToolGroup = (): ToolGroupChatBlock => {
    flushTypewriter(true)
    const blocks = getBlocks()
    const last = blocks[blocks.length - 1]
    if (last?.type === 'tool_group') return last
    const group: ToolGroupChatBlock = {
      type: 'tool_group',
      groupId: `live:${requestId}:tools:${++liveToolGroupSeq}`,
      tools: [],
      running: true,
    }
    blocks.push(group)
    return group
  }

  const publicInput = (data: Record<string, unknown>) => ({
    ...(data.path ? { path: String(data.path) } : {}),
    ...(data.pattern ? { pattern: String(data.pattern) } : {}),
  })

  const handleEvent = (streamEvent: StreamEvent) => {
    const data = streamEvent.data
    switch (streamEvent.event) {
      case 'text_delta':
        appendText(String(data.text || ''), String(data.batchId || ''))
        break
      case 'tool_started': {
        const toolCallId = String(data.toolCallId || '')
        const group = ensureToolGroup()
        group.running = true
        group.tools = [
          ...group.tools,
          {
            toolCallId,
            toolName: String(data.name || 'tool'),
            batchId: String(data.batchId || ''),
            status: 'running',
            input: publicInput(data),
          },
        ]
        break
      }
      case 'tool_finished': {
        const toolCallId = String(data.toolCallId || '')
        const blocks = getBlocks()
        let group = blocks.find(
          (block): block is ToolGroupChatBlock =>
            block.type === 'tool_group' &&
            block.tools.some(activity => activity.toolCallId === toolCallId)
        )
        if (!group) group = ensureToolGroup()
        const existing = group.tools.find(item => item.toolCallId === toolCallId)
        const status = data.status === 'completed' ? 'completed' : 'failed'
        if (existing) {
          existing.status = status
          existing.batchId = String(data.batchId || existing.batchId || '')
          existing.outputPreview = String(data.preview || '')
          existing.durationMs =
            typeof data.durationMs === 'number' ? data.durationMs : existing.durationMs
          existing.sizeBytes =
            typeof data.sizeBytes === 'number' ? data.sizeBytes : existing.sizeBytes
          existing.isError = status === 'failed'
        } else {
          group.tools = [
            ...group.tools,
            {
              toolCallId,
              toolName: String(data.name || 'tool'),
              batchId: String(data.batchId || ''),
              status,
              input: publicInput(data),
              outputPreview: String(data.preview || ''),
              durationMs: typeof data.durationMs === 'number' ? data.durationMs : undefined,
              sizeBytes: typeof data.sizeBytes === 'number' ? data.sizeBytes : undefined,
              isError: status === 'failed',
            },
          ]
        }
        group.running = group.tools.some(activity => activity.status === 'running')
        break
      }
      case 'compaction': {
        if (data.status === 'started') {
          appendText('> 正在压缩较早的会话上下文...', 'status:compaction')
        } else if (data.status === 'completed') {
          appendText(
            `> 上下文压缩完成，已整理 ${String(data.compactedMessages || 0)} 条历史消息。`,
            'status:compaction'
          )
        } else if (data.status === 'failed') {
          appendText('> 上下文压缩失败，本轮继续使用原上下文。', 'status:compaction')
        }
        break
      }
      case 'subagent_started':
        appendText('> 子代理开始处理局部任务...', 'status:subagent')
        break
      case 'subagent_finished':
        appendText('> 子代理已完成局部任务。', 'status:subagent')
        break
      case 'subagent_failed':
        appendText('> 子代理执行失败，主流程将继续处理。', 'status:subagent')
        break
      case 'steering_consumed':
        // 用户消息由 App 队列流统一落 UI，避免 Turn SSE 与队列 SSE 重复插入。
        break
      case 'step_cancelled':
        flushTypewriter(false)
        appendControlNote('> 当前模型步已取消，正在安全收口。')
        break
      case 'redirect_applied':
        flushTypewriter(false)
        appendControlNote(`> 已转向：${String(data.texts || '')}`)
        break
      case 'turn_interrupted':
        flushTypewriter(false)
        appendControlNote('> 本轮已中断。')
        break
      case 'stream_gap': {
        const gapTurnId = String(data.turnId || requestId)
        streamGapTurnIds.add(gapTurnId)
        isGenerating.value = false
        if (activeTurnId.value === gapTurnId) activeTurnId.value = undefined
        message.warning('本轮实时事件过多，已切换到历史快照')
        void fetchChatHistory()
        break
      }

      case 'turn_finished': {
        flushTypewriter(true)
        const finishedTurnId = String(data.turnId || requestId)
        const messageItem = messages.value[aiMessageIndex]
        if (messageItem) {
          messageItem.loading = false
          messageItem.blocks?.forEach(block => {
            if (block.type !== 'tool_group') return
            block.running = false
            block.tools.forEach(activity => {
              if (activity.status === 'running') activity.status = 'interrupted'
            })
          })
        }
        if (activeTurnId.value === finishedTurnId) {
          isGenerating.value = false
          activeTurnId.value = undefined
        }
        if (data.status === 'completed') {
          updatePreview()
        } else if (data.status === 'interrupted') {
          message.warning('生成已中断')
        } else {
          message.error('生成失败，请重试')
        }
        break
      }
      case 'error':
        appendText(`> 生成失败：${String(data.message || '请稍后重试')}`, 'status:error')
        break
    }
  }

  try {
    await streamGeneration({
      baseURL,
      appId: Number(appId.value),
      message: userMessage,
      requestId,
      signal: activeAbortController.signal,
      onEvent: handleEvent,
    })
  } catch (error) {
    cancelTypewriter()
    if (!activeAbortController.signal.aborted) handleError(error, aiMessageIndex)
  }
}

const appendControlNote = (content: string) => {
  const turnId = activeTurnId.value
  if (!turnId) return
  const messageItem = messages.value.find(item => item.type === 'ai' && item.turnId === turnId)
  if (!messageItem) return
  if (!messageItem.blocks) messageItem.blocks = []
  messageItem.blocks.push({
    type: 'text',
    batchId: `status:control:${Date.now()}`,
    content,
  })
  messageItem.loading = false
  scrollToBottom()
}

const stopGeneration = async () => {
  const turnId = activeTurnId.value
  if (!turnId || !appId.value || !isOwner.value || stopping.value) return
  stopping.value = true
  try {
    const baseURL = request.defaults.baseURL || API_BASE_URL
    const ack = await sendTurnControl({
      baseURL,
      appId: Number(appId.value),
      turnId,
      action: 'interrupt',
      commandId: createRequestId(),
    })
    if (ack.status !== 'accepted' && ack.status !== 'duplicate') {
      message.warning('当前回合已结束，停止命令未生效')
    }
  } catch (error) {
    console.error('停止生成失败：', error)
    message.error(error instanceof Error ? error.message : '停止生成失败')
  } finally {
    stopping.value = false
  }
}

// 错误处理函数
const handleError = (error: unknown, aiMessageIndex: number) => {
  console.error('生成代码失败：', error)
  const content = '抱歉，生成过程中出现了错误，请重试。'
  const messageItem = messages.value[aiMessageIndex]
  const failedTurnId = messageItem?.turnId
  if (messageItem) {
    messageItem.content = content
    messageItem.blocks = [{ type: 'text', batchId: 'error', content }]
    messageItem.loading = false
  }
  message.error('生成失败，请重试')
  if (!failedTurnId || activeTurnId.value === failedTurnId) {
    isGenerating.value = false
    activeTurnId.value = undefined
  }
}

// 更新预览
const updatePreview = () => {
  if (appId.value) {
    const codeGenType = appInfo.value?.codeGenType || CodeGenTypeEnum.HTML
    previewRevision += 1
    const newPreviewUrl = getStaticPreviewUrl(codeGenType, appId.value, previewRevision)
    previewUrl.value = newPreviewUrl
    previewReady.value = true
  }
}

const updatePreviewIfAvailable = async () => {
  if (!appId.value) return
  const codeGenType = appInfo.value?.codeGenType || CodeGenTypeEnum.HTML
  const candidate = getStaticPreviewUrl(codeGenType, appId.value)
  try {
    const response = await fetch(candidate, {
      method: 'HEAD',
      credentials: 'include',
      cache: 'no-store',
    })
    if (response.ok) {
      updatePreview()
      return
    }
  } catch (error) {
    console.warn('检查预览产物失败：', error)
  }
  previewUrl.value = ''
  previewReady.value = false
}
const refreshPreview = () => {
  if (!previewUrl.value || refreshingPreview.value || isGenerating.value) return
  refreshingPreview.value = true
  updatePreview()
}

// 滚动到底部
const scrollToBottom = () => {
  if (messagesContainer.value) {
    messagesContainer.value.scrollTop = messagesContainer.value.scrollHeight
  }
}

// 部署应用
const deployApp = async () => {
  if (!appId.value) {
    message.error('应用ID不存在')
    return
  }

  deploying.value = true
  try {
    const res = await deployAppApi({
      appId: appId.value as unknown as number,
    })

    if (res.data.code === 0 && res.data.data) {
      deployUrl.value = res.data.data
      deployModalVisible.value = true
      message.success('部署成功')
    } else {
      message.error('部署失败：' + res.data.message)
    }
  } catch (error) {
    console.error('部署失败：', error)
    message.error('部署失败，请重试')
  } finally {
    deploying.value = false
  }
}

// 在新窗口打开预览
const openInNewTab = () => {
  if (previewUrl.value) {
    openExternalUrl(previewUrl.value)
  }
}

// 打开部署的网站
const openDeployedSite = () => {
  if (deployUrl.value) {
    openExternalUrl(deployUrl.value)
  }
}


// 下载相关
const downloading = ref(false)

// 下载代码
const downloadCode = async () => {
  if (!appId.value) {
    message.error('应用ID不存在')
    return
  }
  downloading.value = true
  try {
    const API_BASE_URL = request.defaults.baseURL || ''
    const url = `${API_BASE_URL}/app/download/${appId.value}`
    const response = await fetch(url, {
      method: 'GET',
      credentials: 'include',
    })
    if (!response.ok) {
      throw new Error(`下载失败: ${response.status}`)
    }
    if (response.headers.get('Content-Type')?.includes('application/json')) {
      const errorEnvelope = await response.json()
      throw new Error(String(errorEnvelope.message || '下载失败'))
    }
    // 获取文件名
    const contentDisposition = response.headers.get('Content-Disposition')
    const fileName = contentDisposition?.match(/filename="(.+)"/)?.[1] || `app-${appId.value}.zip`
    // 下载文件
    const blob = await response.blob()
    const downloadUrl = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = downloadUrl
    link.download = fileName
    link.click()
    // 清理
    URL.revokeObjectURL(downloadUrl)
    message.success('代码下载成功')
  } catch (error) {
    console.error('下载失败：', error)
    message.error(error instanceof Error ? error.message : '下载失败，请重试')
  } finally {
    downloading.value = false
  }
}


// iframe加载完成
const onIframeLoad = () => {
  previewReady.value = true
  refreshingPreview.value = false
  handleVisualEditorIframeLoad()
}

// 编辑应用
const editApp = () => {
  if (appInfo.value?.id) {
    router.push(`/app/edit/${appInfo.value.id}`)
  }
}

// 删除应用
const deleteApp = async () => {
  if (!appInfo.value?.id) return

  try {
    const res = await deleteAppApi({ id: appInfo.value.id })
    if (res.data.code === 0) {
      message.success('删除成功')
      appDetailVisible.value = false
      router.push('/')
    } else {
      message.error('删除失败：' + res.data.message)
    }
  } catch (error) {
    console.error('删除失败：', error)
    message.error('删除失败')
  }
}

// 页面加载时获取应用信息
onMounted(() => {
  fetchAppInfo()
})

// 清理资源
onUnmounted(() => {
  activeAbortController?.abort()
  queueEventSource?.close()
})
</script>

<style scoped>
#appChatPage {
  height: calc(100vh - var(--app-header-height, 64px));
  height: calc(100dvh - var(--app-header-height, 64px));
  display: flex;
  flex-direction: column;
  padding: 12px 16px 16px;
  background: #f6f6f4;
  color: #242422;
}

.mobile-view-switcher,
.mobile-preview-refresh,
.mobile-preview-footer {
  display: none;
}

/* 顶部栏 */
.header-bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 8px 14px;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 12px;
}

.app-name {
  margin: 0;
  font-size: 18px;
  font-weight: 600;
  color: #1f1f1d;
}

.header-right {
  display: flex;
  gap: 10px;
}

/* 主要内容区域 */
.main-content {
  flex: 1;
  display: flex;
  gap: 12px;
  overflow: hidden;
}

/* 左侧对话区域 */
.chat-section {
  flex: 2;
  display: flex;
  flex-direction: column;
  min-width: 0;
  background: #fbfbfa;
  border: 1px solid #e8e8e5;
  border-radius: 24px;
  box-shadow: 0 10px 30px rgba(28, 28, 25, 0.04);
  overflow: hidden;
}

.messages-container {
  flex: 1;
  padding: 24px 20px;
  overflow-y: auto;
  scroll-behavior: smooth;
}

.messages-container--initializing {
  visibility: hidden;
}

.message-item {
  margin-bottom: 16px;
}

.user-message {
  display: flex;
  justify-content: flex-end;
  align-items: flex-start;
  gap: 8px;
}

.ai-message {
  display: flex;
  justify-content: flex-start;
  align-items: flex-start;
  gap: 10px;
}

.message-content {
  max-width: 76%;
  padding: 11px 15px;
  border-radius: 18px;
  line-height: 1.65;
  word-wrap: break-word;
}

.user-message .message-content {
  background: #f0f0ed;
  color: #242422;
  border: 1px solid #e7e7e3;
}

.visual-prompt-content {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.visual-element-chip {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  width: fit-content;
  max-width: 100%;
  min-width: 0;
  padding: 8px 13px;
  color: #62626b;
  background: #fff;
  border: 1px solid #dedee3;
  border-radius: 12px;
  box-shadow: 0 2px 7px rgba(28, 28, 25, 0.06);
}

.visual-element-chip code {
  min-width: 0;
  overflow: hidden;
  color: #55555d;
  font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', monospace;
  font-size: 13px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ai-message .message-content {
  max-width: 84%;
  background: transparent;
  color: #242422;
  padding: 4px 8px;
}

.message-avatar {
  flex-shrink: 0;
}

.assistant-block + .assistant-block {
  margin-top: 10px;
}

.streaming-text {
  white-space: pre-wrap;
  line-height: 1.65;
  word-break: break-word;
}

.load-more-container {
  display: flex;
  justify-content: center;
  padding: 12px 0;
}

.loading-indicator {
  display: flex;
  align-items: center;
  gap: 8px;
  color: #777772;
}

/* Followup 队列 */
.queue-panel {
  margin-bottom: 10px;
  overflow: hidden;
  background: #fff;
  border: 1px solid #e8e8e5;
  border-radius: 22px;
  box-shadow: 0 6px 18px rgba(28, 28, 25, 0.035);
}

.queue-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  min-height: 44px;
  padding: 7px 12px;
  border-bottom: 1px solid #f0f0ed;
}

.queue-row:last-child {
  border-bottom: none;
}

.queue-row-content {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
  flex: 1;
}

.queue-row-icon {
  flex-shrink: 0;
  color: #93938e;
  font-size: 17px;
  line-height: 1;
}

.queue-row-text {
  display: block;
  overflow: hidden;
  color: #3d3d39;
  font-size: 14px;
  line-height: 1.5;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.queue-row-actions {
  display: flex;
  flex-shrink: 0;
  align-items: center;
  gap: 2px;
}

.queue-row-actions :deep(.ant-btn) {
  color: #6f6f6a;
  font-size: 13px;
}

.queue-row-actions :deep(.ant-btn:hover) {
  color: #242422;
  background: #f5f5f2;
}

.queue-paused-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 12px;
  color: #858580;
  font-size: 14px;
  border-bottom: 1px solid #f0f0ed;
}

.queue-paused-title {
  display: flex;
  align-items: center;
  gap: 9px;
  min-width: 0;
}

.queue-paused-title span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.queue-paused-header :deep(.ant-btn) {
  flex-shrink: 0;
  color: #6f6f6a;
  font-size: 14px;
}

.queue-conflict-description {
  margin: 8px 0 0;
  color: #656560;
  line-height: 1.7;
}

.queue-conflict-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 24px;
}

/* 输入区域 */
.input-container {
  padding: 12px 16px 16px;
  background: linear-gradient(180deg, rgba(251, 251, 250, 0) 0%, #fbfbfa 28%);
}

.input-wrapper {
  position: relative;
  padding: 10px 56px 10px 14px;
  background: #fff;
  border: 1px solid #e4e4e0;
  border-radius: 22px;
  box-shadow: 0 4px 16px rgba(28, 28, 25, 0.04);
}

.input-wrapper.has-visual-edit {
  padding-right: 164px;
}

.visual-selection-alert {
  margin-bottom: 10px;
  border-radius: 14px;
}

.input-wrapper :deep(.ant-input) {
  min-height: 42px;
  padding: 0;
  color: #242422;
  font-size: 14px;
  line-height: 1.6;
  background: transparent;
  border: none;
  box-shadow: none;
  resize: none;
}

.input-wrapper :deep(.ant-input::placeholder) {
  color: #aaa9a4;
}

.input-actions {
  position: absolute;
  right: 10px;
  bottom: 10px;
  display: flex;
  align-items: center;
  gap: 8px;
}

.visual-edit-action {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  height: 36px;
  padding: 0 13px;
  color: #30303a;
  font-weight: 500;
  background: #fff;
  border-color: #e3e3df;
  border-radius: 18px;
  box-shadow: 0 3px 10px rgba(28, 28, 25, 0.06);
}

.visual-edit-action:hover {
  color: #1f1f23;
  background: #fafaf8;
  border-color: #d7d7d1;
}

.visual-edit-action.active {
  color: #1746b5;
  background: #eef4ff;
  border-color: #8fb0ff;
  box-shadow: 0 0 0 2px rgba(47, 107, 255, 0.1);
}

.composer-action {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  min-width: 36px;
  padding: 0;
  border-radius: 12px;
}

.send-action {
  background: #2f6bff;
  border-color: #2f6bff;
  box-shadow: 0 3px 10px rgba(47, 107, 255, 0.24);
}

.send-action:hover {
  background: #245eea;
  border-color: #245eea;
}

.stop-action {
  color: #242422;
  background: #fff;
  border-color: #e2e2de;
  box-shadow: none;
}

.stop-action:hover {
  color: #000;
  background: #f5f5f2;
  border-color: #d8d8d3;
}

.stop-square {
  width: 12px;
  height: 12px;
  background: #20201e;
  border-radius: 2px;
}

/* 右侧预览区域 */
.preview-section {
  position: relative;
  flex: 3;
  display: flex;
  flex-direction: column;
  background: white;
  border-radius: 8px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
  overflow: hidden;
}

.preview-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 16px;
  border-bottom: 1px solid #e8e8e8;
}

.preview-header h3 {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
}

.preview-actions {
  display: flex;
  gap: 8px;
}

.preview-content {
  flex: 1;
  position: relative;
  overflow: hidden;
}

.preview-placeholder {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: #666;
}

.placeholder-icon {
  font-size: 48px;
  margin-bottom: 16px;
}

.preview-loading {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: #666;
}

.preview-loading p {
  margin-top: 16px;
}

.preview-iframe {
  width: 100%;
  height: 100%;
  border: none;
}

/* 响应式设计 */
@media (max-width: 1024px) and (min-width: 769px) {
  .main-content {
    flex-direction: column;
  }

  .chat-section,
  .preview-section {
    flex: 1 1 0;
    height: auto;
    min-height: 0;
  }
}

@media (max-width: 768px) {
  #appChatPage {
    height: 100vh;
    height: 100dvh;
    padding: 0;
  }

  .header-bar {
    display: none;
  }

  .mobile-view-switcher {
    display: flex;
    flex-shrink: 0;
    gap: 4px;
    padding: calc(8px + env(safe-area-inset-top)) 12px 8px;
    background: #f6f6f4;
  }

  .mobile-view-tab {
    display: inline-flex;
    flex: 1;
    align-items: center;
    justify-content: center;
    gap: 8px;
    height: 42px;
    padding: 0 14px;
    color: #8a8985;
    font-size: 16px;
    font-weight: 500;
    background: transparent;
    border: none;
    border-radius: 21px;
    transition:
      color 0.2s ease,
      background 0.2s ease,
      box-shadow 0.2s ease;
  }

  .mobile-view-tab.active {
    color: #242422;
    background: #fff;
    box-shadow: 0 2px 10px rgba(28, 28, 25, 0.08);
  }

  .main-content {
    flex: 1;
    flex-direction: column;
    gap: 0;
    min-height: 0;
    padding: 0;
  }

  .chat-section,
  .preview-section {
    flex: 1 1 0;
    width: 100%;
    height: auto;
    min-height: 0;
    border: none;
    border-radius: 0;
    box-shadow: none;
  }

  .main-content.mobile-view-chat .preview-section,
  .main-content.mobile-view-preview .chat-section {
    display: none;
  }

  .preview-header {
    display: none;
  }

  .preview-content {
    min-height: 0;
  }

  .mobile-preview-refresh.ant-btn {
    position: absolute;
    right: 16px;
    bottom: calc(82px + env(safe-area-inset-bottom));
    z-index: 3;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 46px;
    min-width: 46px;
    height: 46px;
    padding: 0;
    color: #242422;
    background: #fff;
    border-color: #ecece8;
    box-shadow: 0 5px 18px rgba(28, 28, 25, 0.14);
  }

  .mobile-preview-footer {
    display: block;
    flex-shrink: 0;
    padding: 10px 12px calc(10px + env(safe-area-inset-bottom));
    background: #fff;
    border-top: 1px solid #efefec;
  }

  .mobile-deploy-action {
    width: 100%;
    height: 48px;
    color: #fff;
    font-size: 16px;
    font-weight: 600;
    background: #172235;
    border-color: #172235;
    border-radius: 24px;
  }

  .mobile-deploy-action:hover {
    color: #fff;
    background: #223047;
    border-color: #223047;
  }

  .visual-edit-action {
    display: none !important;
  }

  .input-wrapper.has-visual-edit {
    padding-right: 56px;
  }

  .input-container {
    padding-bottom: calc(16px + env(safe-area-inset-bottom));
  }

  .message-content {
    max-width: 85%;
  }

  .code-gen-type-tag {
    font-size: 12px;
  }
}
</style>
