export interface StreamEvent {
  id?: number
  event: string
  data: Record<string, unknown>
}

interface StreamGenerationOptions {
  baseURL: string
  appId: number
  message: string
  requestId: string
  signal?: AbortSignal
  onEvent: (event: StreamEvent) => void
}

const parseEventBlock = (block: string): StreamEvent | null => {
  const lines = block.split(/\r?\n/)
  let event = 'message'
  let id: number | undefined
  const dataLines: string[] = []
  for (const line of lines) {
    if (line.startsWith('id:')) {
      const parsedId = Number(line.slice(3).trim())
      if (Number.isFinite(parsedId)) id = parsedId
    } else if (line.startsWith('event:')) {
      event = line.slice(6).trim()
    } else if (line.startsWith('data:')) {
      dataLines.push(line.slice(5).trimStart())
    }
  }
  if (dataLines.length === 0) return null
  return { id, event, data: JSON.parse(dataLines.join('\n')) as Record<string, unknown> }
}

export const createRequestId = () => {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  return `turn-${Date.now()}-${Math.random().toString(16).slice(2)}`
}

export const streamGeneration = async ({
  baseURL,
  appId,
  message,
  requestId,
  signal,
  onEvent,
}: StreamGenerationOptions) => {
  let streamCompleted = false
  let lastEventId: number | undefined
  let retryCount = 0

  while (!streamCompleted && retryCount < 2) {
    try {
      const headers: Record<string, string> = { 'Content-Type': 'application/json' }
      if (lastEventId !== undefined) headers['Last-Event-ID'] = String(lastEventId)
      const response = await fetch(`${baseURL}/app/chat/gen/code`, {
        method: 'POST',
        credentials: 'include',
        headers,
        signal,
        body: JSON.stringify({ appId, message, requestId }),
      })

      const contentType = response.headers.get('content-type') || ''
      if (!contentType.includes('text/event-stream')) {
        const payload = await response.json()
        throw new Error(payload.message || '生成请求失败')
      }
      if (!response.body) throw new Error('浏览器不支持流式响应')

      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      while (true) {
        const { done, value } = await reader.read()
        buffer += decoder.decode(value || new Uint8Array(), { stream: !done })
        const blocks = buffer.split(/\r?\n\r?\n/)
        buffer = blocks.pop() || ''
        for (const block of blocks) {
          if (!block.trim()) continue
          const streamEvent = parseEventBlock(block)
          if (!streamEvent) continue
          if (streamEvent.id !== undefined) lastEventId = streamEvent.id
          onEvent(streamEvent)
          if (streamEvent.event === 'turn_finished') streamCompleted = true
        }
        if (done) break
      }
      if (buffer.trim()) {
        const streamEvent = parseEventBlock(buffer)
        if (streamEvent) {
          if (streamEvent.id !== undefined) lastEventId = streamEvent.id
          onEvent(streamEvent)
          if (streamEvent.event === 'turn_finished') streamCompleted = true
        }
      }
      if (!streamCompleted) throw new Error('SSE 连接提前结束')
    } catch (error) {
      if (signal?.aborted || streamCompleted) throw error
      retryCount += 1
      if (retryCount >= 2) throw error
      await new Promise((resolve) => setTimeout(resolve, 300))
    }
  }
}
export type TurnControlAction = 'steer' | 'interrupt' | 'redirect'

export interface TurnControlAck {
  status: 'accepted' | 'duplicate' | 'not_active' | 'stale' | 'degraded_to_followup'
  action: TurnControlAction
  appId: number
  turnId: string
  commandId: string
  message?: string | null
}

interface SendTurnControlOptions {
  baseURL: string
  appId: number
  turnId: string
  action: TurnControlAction
  commandId: string
  message?: string
}

export const sendTurnControl = async ({
  baseURL,
  appId,
  turnId,
  action,
  commandId,
  message,
}: SendTurnControlOptions): Promise<TurnControlAck> => {
  const response = await fetch(`${baseURL}/app/chat/turn/control`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ appId, turnId, action, commandId, message }),
  })
  const payload = await response.json()
  if (!response.ok || payload.code !== 0 || !payload.data) {
    throw new Error(payload.message || '控制命令发送失败')
  }
  return payload.data as TurnControlAck
}

export type QueueAction = 'enqueue' | 'steer' | 'delete' | 'resume' | 'clear_paused'

export interface QueuedItem {
  commandId: string
  message: string
  status: 'queued' | 'paused'
}

export interface QueueSnapshot {
  appId: number
  version: number
  paused: boolean
  activeTurnId?: string | null
  queuedItems: QueuedItem[]
  pausedItems: QueuedItem[]
}

export interface QueueControlAck {
  status: string
  action: QueueAction
  commandId: string
  queue: QueueSnapshot
}

interface SendQueueControlOptions {
  baseURL: string
  appId: number
  action: QueueAction
  commandId: string
  itemId?: string
  message?: string
  clearPaused?: boolean
}

export const sendQueueControl = async ({
  baseURL,
  appId,
  action,
  commandId,
  itemId,
  message,
  clearPaused,
}: SendQueueControlOptions): Promise<QueueControlAck> => {
  const response = await fetch(`${baseURL}/app/chat/queue/control`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ appId, action, commandId, itemId, message, clearPaused }),
  })
  const payload = await response.json()
  if (!response.ok || payload.code !== 0 || !payload.data) {
    throw new Error(payload.message || '队列命令发送失败')
  }
  return payload.data as QueueControlAck
}

interface QueueStreamHandlers {
  onSnapshot: (snapshot: QueueSnapshot) => void
  onChanged: (snapshot: QueueSnapshot) => void
  onConsumed: (event: UserMessageConsumedEvent) => void
  onError?: () => void
}

export interface UserMessageConsumedEvent {
  appId: number
  commandId: string
  turnId: string
  text: string
  mode: 'followup' | 'steer'
  queue: QueueSnapshot
}

export const connectGenerationQueue = ({
  baseURL,
  appId,
  handlers,
}: {
  baseURL: string
  appId: number
  handlers: QueueStreamHandlers
}) => {
  const source = new EventSource(
    `${baseURL}/app/chat/queue/events?appId=${encodeURIComponent(String(appId))}`,
    { withCredentials: true }
  )
  const parse = <T>(event: MessageEvent<string>): T => JSON.parse(event.data) as T
  source.addEventListener('queue_snapshot', (event) => {
    handlers.onSnapshot(parse<QueueSnapshot>(event as MessageEvent<string>))
  })
  source.addEventListener('queue_changed', (event) => {
    handlers.onChanged(parse<QueueSnapshot>(event as MessageEvent<string>))
  })
  source.addEventListener('user_message_consumed', (event) => {
    handlers.onConsumed(parse<UserMessageConsumedEvent>(event as MessageEvent<string>))
  })
  source.onerror = () => {
    handlers.onError?.()
  }
  return source
}
