<template>
  <div id="consumptionManagePage">
    <a-form layout="inline" :model="searchParams" @finish="doSearch">
      <a-form-item label="用户ID">
        <a-input-number
          v-model:value="searchParams.userId"
          :min="1"
          placeholder="输入用户ID"
          style="width: 160px"
          allow-clear
        />
      </a-form-item>
      <a-form-item label="应用ID">
        <a-input-number
          v-model:value="searchParams.appId"
          :min="1"
          placeholder="输入应用ID"
          style="width: 160px"
          allow-clear
        />
      </a-form-item>
      <a-form-item>
        <a-space>
          <a-button type="primary" html-type="submit">搜索</a-button>
          <a-button @click="doReset">重置</a-button>
        </a-space>
      </a-form-item>
    </a-form>

    <a-divider />

    <a-row :gutter="[16, 16]" class="summary-row">
      <a-col :xs="24" :sm="12" :xl="6">
        <a-card size="small" class="summary-card">
          <a-statistic
            title="消费总额"
            :value="summary.totalCostCny || 0"
            :precision="8"
            prefix="¥"
          />
        </a-card>
      </a-col>
      <a-col :xs="24" :sm="12" :xl="6">
        <a-card size="small" class="summary-card">
          <a-statistic title="模型调用次数" :value="summary.totalCalls || 0" />
        </a-card>
      </a-col>
      <a-col :xs="24" :sm="12" :xl="6">
        <a-card size="small" class="summary-card">
          <a-statistic title="输入 Token" :value="summary.totalInputTokens || 0" />
        </a-card>
      </a-col>
      <a-col :xs="24" :sm="12" :xl="6">
        <a-card size="small" class="summary-card">
          <a-statistic title="输出 Token" :value="summary.totalOutputTokens || 0" />
        </a-card>
      </a-col>
    </a-row>

    <a-table
      class="consumption-table"
      :columns="columns"
      :data-source="data"
      :loading="loading"
      :pagination="pagination"
      :scroll="{ x: 1300 }"
      :row-key="(record: API.ModelCallConsumptionVO) => `${record.userId}-${record.appId}`"
      @change="doTableChange"
    >
      <template #bodyCell="{ column, record }">
        <template v-if="column.dataIndex === 'userAccount'">
          <div>{{ record.userAccount || '-' }}</div>
          <div v-if="record.userName" class="secondary-text">{{ record.userName }}</div>
        </template>
        <template v-else-if="column.dataIndex === 'appName'">
          {{ record.appName || '-' }}
        </template>
        <template v-else-if="column.dataIndex === 'callCount'">
          {{ formatNumber(record.callCount) }}
        </template>
        <template v-else-if="column.dataIndex === 'inputTokens'">
          {{ formatNumber(record.inputTokens) }}
        </template>
        <template v-else-if="column.dataIndex === 'cachedInputTokens'">
          {{ formatNumber(record.cachedInputTokens) }}
        </template>
        <template v-else-if="column.dataIndex === 'outputTokens'">
          {{ formatNumber(record.outputTokens) }}
        </template>
        <template v-else-if="column.dataIndex === 'costCny'">
          <span class="cost-text">{{ formatCost(record.costCny) }}</span>
        </template>
        <template v-else-if="column.dataIndex === 'lastCallTime'">
          {{ formatTime(record.lastCallTime) || '-' }}
        </template>
      </template>
    </a-table>
  </div>
</template>

<script lang="ts" setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { message } from 'ant-design-vue'
import { listModelCallConsumptionByPageForAdmin } from '@/api/modelCallController'
import { formatTime } from '@/utils/time'

const columns = [
  { title: '用户ID', dataIndex: 'userId', width: 90 },
  { title: '用户账号', dataIndex: 'userAccount', width: 160 },
  { title: '应用ID', dataIndex: 'appId', width: 90 },
  { title: '应用名称', dataIndex: 'appName', width: 180 },
  { title: '调用次数', dataIndex: 'callCount', width: 100 },
  { title: '输入 Token', dataIndex: 'inputTokens', width: 120 },
  { title: '缓存输入 Token', dataIndex: 'cachedInputTokens', width: 140 },
  { title: '输出 Token', dataIndex: 'outputTokens', width: 120 },
  { title: '消费总额', dataIndex: 'costCny', width: 140 },
  { title: '最近调用', dataIndex: 'lastCallTime', width: 180 },
]

const data = ref<API.ModelCallConsumptionVO[]>([])
const total = ref(0)
const loading = ref(false)
const summary = ref<API.ModelCallConsumptionSummary>({})

const searchParams = reactive<API.ModelCallConsumptionQueryRequest>({
  pageNum: 1,
  pageSize: 10,
})

const fetchData = async () => {
  loading.value = true
  try {
    const res = await listModelCallConsumptionByPageForAdmin({ ...searchParams })
    if (res.data.code === 0 && res.data.data) {
      data.value = res.data.data.records ?? []
      total.value = res.data.data.totalRow ?? 0
      summary.value = res.data.data.summary ?? {}
    } else {
      message.error('获取消费数据失败：' + res.data.message)
    }
  } catch (error) {
    console.error('获取消费数据失败：', error)
    message.error('获取消费数据失败')
  } finally {
    loading.value = false
  }
}

const pagination = computed(() => ({
  current: searchParams.pageNum ?? 1,
  pageSize: searchParams.pageSize ?? 10,
  total: total.value,
  showSizeChanger: true,
  pageSizeOptions: ['10', '20'],
  showTotal: (value: number) => `共 ${value} 个应用`,
}))

const doTableChange = (page: { current: number; pageSize: number }) => {
  searchParams.pageNum = page.current
  searchParams.pageSize = page.pageSize
  fetchData()
}

const doSearch = () => {
  searchParams.pageNum = 1
  fetchData()
}

const doReset = () => {
  searchParams.userId = undefined
  searchParams.appId = undefined
  searchParams.pageNum = 1
  fetchData()
}

const formatNumber = (value?: number) => Number(value || 0).toLocaleString('zh-CN')

const formatCost = (value?: number) => {
  const amount = Number(value || 0)
  if (!Number.isFinite(amount) || amount === 0) return '¥0'
  if (amount < 0.000001) return `¥${amount.toFixed(8)}`
  return `¥${amount.toFixed(6)}`
}

onMounted(() => {
  fetchData()
})
</script>

<style scoped>
#consumptionManagePage {
  padding: 24px;
  background: white;
  margin-top: 16px;
}

.summary-row {
  margin-bottom: 20px;
}

.summary-card {
  height: 100%;
}

.consumption-table {
  margin-top: 8px;
}

.secondary-text {
  margin-top: 2px;
  color: #999;
  font-size: 12px;
}

.cost-text {
  color: #cf1322;
  font-weight: 600;
}
</style>
