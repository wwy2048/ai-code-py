<template>
  <div id="chatHistoryManagePage">
    <!-- 搜索表单 -->
    <a-form layout="inline" :model="searchParams" @finish="doSearch">
      <a-form-item label="应用ID">
        <a-input v-model:value="searchParams.appId" placeholder="输入应用ID" />
      </a-form-item>
      <a-form-item label="用户ID">
        <a-input v-model:value="searchParams.userId" placeholder="输入用户ID" />
      </a-form-item>
      <a-form-item label="消息类型">
        <a-select
          v-model:value="searchParams.messageType"
          placeholder="选择消息类型"
          style="width: 150px"
        >
          <a-select-option value="">全部</a-select-option>
          <a-select-option value="user">用户消息</a-select-option>
          <a-select-option value="ai">AI消息</a-select-option>
          <a-select-option value="tool">工具消息</a-select-option>
        </a-select>
      </a-form-item>
      <a-form-item label="开始时间">
        <a-date-picker v-model:value="startTime" show-time />
      </a-form-item>
      <a-form-item label="结束时间">
        <a-date-picker v-model:value="endTime" show-time />
      </a-form-item>
      <a-form-item>
        <a-button type="primary" html-type="submit">搜索</a-button>
      </a-form-item>
    </a-form>
    <a-divider />

    <!-- 表格 -->
    <a-table
      :columns="columns"
      :data-source="data"
      :pagination="pagination"
      @change="doTableChange"
      :scroll="{ x: 1200 }"
    >
      <template #bodyCell="{ column, record }">
        <template v-if="column.dataIndex === 'message'">
          <a-tooltip :title="record.preview">
            <div class="message-text">{{ record.preview }}</div>
          </a-tooltip>
        </template>
        <template v-else-if="column.dataIndex === 'messageType'">
          <a-tag v-if="record.messageType === 'user'" color="blue">用户</a-tag>
          <a-tag v-else-if="record.messageType === 'ai'" color="green">AI</a-tag>
          <a-tag v-else color="orange">工具</a-tag>
        </template>
        <template v-else-if="column.dataIndex === 'visible'">
          <a-tag :color="record.visible ? 'blue' : 'orange'">
            {{ record.visible ? '可见' : '隐藏' }}
          </a-tag>
        </template>
        <template v-else-if="column.dataIndex === 'createTime'">
          {{ formatTime(record.createTime) }}
        </template>
        <template v-else-if="column.key === 'action'">
          <a-button type="primary" size="small" @click="openTurn(record)">Turn 详情</a-button>
        </template>
      </template>
    </a-table>

    <a-modal
      v-model:open="detailOpen"
      title="Turn 运行详情"
      :footer="null"
      width="1000px"
      :loading="detailLoading"
    >
      <a-descriptions v-if="detail" :column="2" bordered size="small">
        <a-descriptions-item label="App ID">{{ detail.appId }}</a-descriptions-item>
        <a-descriptions-item label="Turn ID">{{ detail.turnId }}</a-descriptions-item>
      </a-descriptions>
      <a-divider>消息</a-divider>
      <a-table
        :columns="messageColumns"
        :data-source="detail?.messages || []"
        :pagination="false"
        size="small"
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.dataIndex === 'visible'">
            <a-tag :color="record.visible ? 'blue' : 'orange'">
              {{ record.visible ? '可见' : '隐藏' }}
            </a-tag>
          </template>
          <template v-else-if="column.dataIndex === 'isError'">
            <a-tag :color="record.isError ? 'red' : 'green'">
              {{ record.isError ? '失败' : '正常' }}
            </a-tag>
          </template>
        </template>
      </a-table>
      <a-divider>模型调用</a-divider>
      <a-table
        :columns="modelColumns"
        :data-source="detail?.modelCalls || []"
        :pagination="false"
        size="small"
      />
    </a-modal>
  </div>
</template>

<script lang="ts" setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { message } from 'ant-design-vue'
import { getChatTurnDebug, listAllChatHistoryByPageForAdmin } from '@/api/chatHistoryController'
import { formatTime } from '@/utils/time'
import type { Dayjs } from 'dayjs'

const columns = [
  {
    title: 'ID',
    dataIndex: 'id',
    width: 80,
    fixed: 'left',
  },
  {
    title: '应用ID',
    dataIndex: 'appId',
    width: 100,
  },
  {
    title: '用户ID',
    dataIndex: 'userId',
    width: 100,
  },
  {
    title: '消息类型',
    dataIndex: 'messageType',
    width: 100,
  },
  {
    title: '消息内容',
    dataIndex: 'message',
    width: 300,
  },
  {
    title: 'Turn',
    dataIndex: 'turnId',
    width: 160,
  },
  {
    title: '可见性',
    dataIndex: 'visible',
    width: 90,
  },
  {
    title: '创建时间',
    dataIndex: 'createTime',
    width: 160,
  },
  {
    title: '操作',
    key: 'action',
    width: 110,
    fixed: 'right',
  },
]

const messageColumns = [
  { title: '序号', dataIndex: 'sequence', width: 70 },
  { title: '类型', dataIndex: 'messageKind', width: 110 },
  { title: '工具', dataIndex: 'toolName', width: 120 },
  { title: '可见', dataIndex: 'visible', width: 80 },
  { title: '状态', dataIndex: 'isError', width: 80 },
  { title: '耗时(ms)', dataIndex: 'durationMs', width: 90 },
  { title: '预览', dataIndex: 'preview' },
]

const modelColumns = [
  { title: '类型', dataIndex: 'callKind', width: 100 },
  { title: '模型', dataIndex: 'modelName', width: 130 },
  { title: '状态', dataIndex: 'status', width: 80 },
  { title: '耗时(ms)', dataIndex: 'durationMs', width: 90 },
  { title: '输入', dataIndex: 'inputTokens', width: 80 },
  { title: '输出', dataIndex: 'outputTokens', width: 80 },
  { title: '错误', dataIndex: 'errorSummary' },
]

// 数据
const data = ref<API.ChatHistory[]>([])
const total = ref(0)
const startTime = ref<Dayjs>()
const endTime = ref<Dayjs>()
const detailOpen = ref(false)
const detailLoading = ref(false)
const detail = ref<API.ChatTurnDebug>()

// 搜索条件
const searchParams = reactive<API.ChatHistoryQueryRequest>({
  pageNum: 1,
  pageSize: 10,
})

// 获取数据
const fetchData = async () => {
  try {
    const res = await listAllChatHistoryByPageForAdmin({
      ...searchParams,
    })
    if (res.data.code === 0 && res.data.data) {
      data.value = res.data.data.records ?? []
      total.value = res.data.data.totalRow ?? 0
    } else {
      message.error('获取数据失败，' + res.data.message)
    }
  } catch (error) {
    console.error('获取数据失败：', error)
    message.error('获取数据失败')
  }
}

// 页面加载时请求一次
onMounted(() => {
  fetchData()
})

// 分页参数
const pagination = computed(() => {
  return {
    current: searchParams.pageNum ?? 1,
    pageSize: searchParams.pageSize ?? 10,
    total: total.value,
    showSizeChanger: true,
    pageSizeOptions: ['10', '20'],
    showTotal: (total: number) => `共 ${total} 条`,
  }
})

// 表格变化处理
const doTableChange = (page: { current: number; pageSize: number }) => {
  searchParams.pageNum = page.current
  searchParams.pageSize = page.pageSize
  fetchData()
}

// 搜索
const doSearch = () => {
  // 重置页码
  searchParams.pageNum = 1
  searchParams.startTime = startTime.value?.format('YYYY-MM-DDTHH:mm:ss')
  searchParams.endTime = endTime.value?.format('YYYY-MM-DDTHH:mm:ss')
  fetchData()
}

const openTurn = async (record: API.ChatHistory) => {
  if (!record.appId || !record.turnId) {
    message.warning('该消息没有可用的 Turn 信息')
    return
  }
  detailOpen.value = true
  detailLoading.value = true
  try {
    const res = await getChatTurnDebug(record.appId, record.turnId)
    if (res.data.code === 0 && res.data.data) {
      detail.value = res.data.data
    } else {
      message.error('获取 Turn 详情失败：' + res.data.message)
    }
  } catch (error) {
    console.error('获取 Turn 详情失败：', error)
    message.error('获取 Turn 详情失败')
  } finally {
    detailLoading.value = false
  }
}
</script>

<style scoped>
#chatHistoryManagePage {
  padding: 24px;
  background: white;
  margin-top: 16px;
}

.message-text {
  max-width: 300px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.text-gray {
  color: #999;
}

:deep(.ant-table-tbody > tr > td) {
  vertical-align: middle;
}
</style>
