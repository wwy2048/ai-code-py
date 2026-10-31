<template>
  <div v-if="activities.length" class="tool-activity-group">
    <button class="tool-activity-summary" :aria-expanded="expanded" @click="expanded = !expanded">
      <span class="caret">{{ expanded ? '▼' : '▶' }}</span>
      <span>{{ summary }}</span>
    </button>

    <div v-if="expanded" class="tool-activity-list">
      <div v-for="activity in activities" :key="activity.toolCallId" class="tool-activity-item">
        <button class="tool-activity-row" @click="toggleRow(activity.toolCallId || '')">
          <span class="tool-status" :class="statusClass(activity.status)">
            {{ statusIcon(activity.status) }}
          </span>
          <span class="tool-name">{{ activity.toolName || 'tool' }}</span>
          <span v-if="target(activity)" class="tool-target">{{ target(activity) }}</span>
          <span class="tool-meta">{{ duration(activity.durationMs) }}</span>
        </button>
        <div v-if="isRowExpanded(activity.toolCallId || '')" class="tool-details">
          <div v-if="activity.input && Object.keys(activity.input).length" class="tool-section">
            <div class="section-label">参数</div>
            <pre>{{ formatInput(activity.input) }}</pre>
          </div>
          <div v-if="activity.outputPreview" class="tool-section">
            <div class="section-label">输出</div>
            <pre>{{ activity.outputPreview }}</pre>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'

const props = defineProps<{
  activities: API.ToolActivity[]
  running?: boolean
  turnActive?: boolean
}>()

// 工具批次运行时展开；整个 Turn 结束前保持展开，结束后自动折叠。
const expanded = ref(Boolean(props.running || props.turnActive))
const expandedRows = ref<Set<string>>(new Set())

watch(
  [() => props.running, () => props.turnActive],
  ([running, turnActive], [, previousTurnActive]) => {
    if (running) {
      expanded.value = true
    } else if (previousTurnActive && !turnActive) {
      expanded.value = false
      expandedRows.value = new Set()
    }
  }
)

const failedCount = computed(
  () => props.activities.filter(activity => activity.isError || activity.status === 'failed').length
)
const totalDuration = computed(() =>
  props.activities.reduce((total, activity) => total + (activity.durationMs || 0), 0)
)
const summary = computed(() => {
  const count = props.activities.length
  if (props.running || props.activities.some(activity => activity.status === 'running')) {
    return `正在执行 ${count} 个工具`
  }
  const failed = failedCount.value ? ` · ${failedCount.value} 个失败` : ''
  return `已执行 ${count} 个工具${failed}${totalDuration.value ? ` · ${totalDuration.value}ms` : ''}`
})

const statusIcon = (status?: string) => {
  if (status === 'completed') return '✓'
  if (status === 'failed') return '✗'
  if (status === 'running') return '●'
  return '!'
}

const statusClass = (status?: string) => `tool-status--${status || 'interrupted'}`
const duration = (value?: number) => (value === undefined ? '' : `${value}ms`)
const formatInput = (input: Record<string, unknown>) => JSON.stringify(input, null, 2)

const target = (activity: API.ToolActivity) => {
  const path = activity.input?.path
  const pattern = activity.input?.pattern
  return typeof path === 'string' ? path : typeof pattern === 'string' ? pattern : ''
}

const toggleRow = (id: string) => {
  if (!id) return
  const next = new Set(expandedRows.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  expandedRows.value = next
}

const isRowExpanded = (id: string) => expandedRows.value.has(id)
</script>

<style scoped>
.tool-activity-group {
  margin-bottom: 12px;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  background: #f8fafc;
  overflow: hidden;
}

.tool-activity-summary,
.tool-activity-row {
  width: 100%;
  border: 0;
  background: transparent;
  text-align: left;
  cursor: pointer;
}

.tool-activity-summary {
  padding: 9px 12px;
  color: #475569;
  font-size: 13px;
}

.caret {
  display: inline-block;
  width: 16px;
  color: #64748b;
  font-size: 10px;
}

.tool-activity-list {
  border-top: 1px solid #e5e7eb;
  background: #fff;
}

.tool-activity-item + .tool-activity-item {
  border-top: 1px solid #f1f5f9;
}

.tool-activity-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 12px;
  color: #334155;
  font-size: 12px;
}

.tool-activity-row:hover,
.tool-activity-summary:hover {
  background: #f1f5f9;
}

.tool-status {
  width: 14px;
  font-weight: 700;
}

.tool-status--completed {
  color: #16a34a;
}

.tool-status--failed {
  color: #dc2626;
}

.tool-status--running {
  color: #2563eb;
}

.tool-status--interrupted {
  color: #d97706;
}

.tool-name {
  font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
  font-weight: 600;
}

.tool-target {
  color: #64748b;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tool-meta {
  margin-left: auto;
  color: #94a3b8;
}

.tool-details {
  padding: 0 12px 10px 34px;
}

.tool-section + .tool-section {
  margin-top: 8px;
}

.section-label {
  margin-bottom: 4px;
  color: #94a3b8;
  font-size: 10px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.tool-section pre {
  max-height: 220px;
  margin: 0;
  padding: 8px;
  overflow: auto;
  border-radius: 6px;
  background: #0f172a;
  color: #e2e8f0;
  font-size: 11px;
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
