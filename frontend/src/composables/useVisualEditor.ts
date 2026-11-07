import { onUnmounted, ref, type Ref } from 'vue'
import { message } from 'ant-design-vue'

const CHANNEL = 'ai-code-visual-editor'
const SELECTED_ELEMENT_BEGIN = '[[AI_CODE_SELECTED_ELEMENT_BEGIN]]'
const SELECTED_ELEMENT_END = '[[AI_CODE_SELECTED_ELEMENT_END]]'

export interface VisualElementInfo {
  tagName: string
  selector: string
  source?: string
  id?: string
  className?: string
  text?: string
  attributes?: Record<string, string>
  outerHTML?: string
  pageUrl?: string
}

export interface ParsedVisualPrompt {
  request: string
  element?: VisualElementInfo
}

interface UseVisualEditorOptions {
  iframeRef: Ref<HTMLIFrameElement | undefined>
  previewUrl: Ref<string>
}

/**
 * 注入到同源 iframe 中的运行时。
 *
 * 它只负责悬浮框、选中框和元素信息采集，不直接修改生成站点源码。
 */
const IFRAME_RUNTIME = String.raw`
(() => {
  'use strict'

  const CHANNEL = 'ai-code-visual-editor'
  const runtimeKey = '__AI_CODE_VISUAL_EDITOR_RUNTIME__'
  const currentScript = document.currentScript
  const parentOrigin = currentScript?.dataset?.parentOrigin || window.location.origin
  const host = document.body || document.documentElement

  if (window[runtimeKey]) {
    window[runtimeKey].setMode(currentScript?.dataset?.initialMode === 'true')
    return
  }

  const hoverOverlay = document.createElement('div')
  const selectedOverlay = document.createElement('div')
  hoverOverlay.setAttribute('data-ai-visual-editor-overlay', 'hover')
  selectedOverlay.setAttribute('data-ai-visual-editor-overlay', 'selected')
  host.appendChild(hoverOverlay)
  host.appendChild(selectedOverlay)

  const overlayStyle = {
    position: 'fixed',
    display: 'none',
    pointerEvents: 'none',
    boxSizing: 'border-box',
    borderRadius: '3px',
    zIndex: '2147483647',
    background: 'transparent'
  }

  const setStyles = (element, styles) => {
    Object.keys(styles).forEach((key) => {
      element.style.setProperty(
        key.replace(/[A-Z]/g, (letter) => '-' + letter.toLowerCase()),
        styles[key],
        'important'
      )
    })
  }

  setStyles(hoverOverlay, {
    ...overlayStyle,
    border: '2px solid #2f6bff',
    boxShadow: '0 0 0 1px rgba(47, 107, 255, 0.18)'
  })
  setStyles(selectedOverlay, {
    ...overlayStyle,
    border: '2px solid #1746b5',
    background: 'rgba(47, 107, 255, 0.06)',
    boxShadow: '0 0 0 1px rgba(23, 70, 181, 0.28)'
  })

  let enabled = false
  let hoverTarget = null
  let selectedTarget = null
  let pointerFrame = null

  const blacklistedTags = new Set([
    'HTML', 'BODY', 'HEAD', 'SCRIPT', 'STYLE', 'LINK', 'META', 'TITLE', 'BASE', 'NOSCRIPT'
  ])

  const escapeCss = (value) => {
    if (window.CSS && typeof window.CSS.escape === 'function') {
      return window.CSS.escape(value)
    }
    return String(value).replace(/[^a-zA-Z0-9_-]/g, '\\$&')
  }

  const isSelectable = (target) => {
    if (!(target instanceof Element)) return false
    if (target.hasAttribute('data-ai-visual-editor-overlay')) return false
    if (blacklistedTags.has(target.tagName)) return false
    return !target.closest('[data-ai-visual-editor-overlay]')
  }

  const uniqueIdSelector = (element) => {
    if (!element.id) return ''
    const selector = '#' + escapeCss(element.id)
    return document.querySelector(selector) === element ? selector : ''
  }

  const selectorFor = (element) => {
    const idSelector = uniqueIdSelector(element)
    if (idSelector) return idSelector

    const parts = []
    let current = element
    while (current && current instanceof Element) {
      if (current === document.documentElement) break

      const tag = current.tagName.toLowerCase()
      const stableClasses = Array.from(current.classList || [])
        .filter((name) => !name.startsWith('data-v-'))
        .slice(0, 2)
        .map((name) => '.' + escapeCss(name))
        .join('')

      if (tag === 'body' || tag === 'html') break

      let part = tag + stableClasses
      if (!stableClasses && current.parentElement) {
        const siblings = Array.from(current.parentElement.children).filter(
          (sibling) => sibling.tagName === current.tagName
        )
        if (siblings.length > 1) {
          part += ':nth-of-type(' + (siblings.indexOf(current) + 1) + ')'
        }
      }

      parts.unshift(part)
      const candidate = parts.join(' > ')
      try {
        if (document.querySelector(candidate) === element) return candidate
      } catch (error) {
        // 忽略非法选择器，继续向上组合。
      }
      current = current.parentElement
    }

    return parts.join(' > ')
  }

  const compactText = (element) => {
    const value = element.innerText || element.textContent || ''
    return value.replace(/\s+/g, ' ').trim().slice(0, 200)
  }

  const collectAttributes = (element) => {
    const allowed = new Set([
      'alt', 'aria-label', 'class', 'data-ai-source', 'href', 'id', 'name',
      'placeholder', 'role', 'src', 'title', 'type', 'value'
    ])
    const attributes = {}
    Array.from(element.attributes || []).forEach((attribute) => {
      if (!allowed.has(attribute.name)) return
      attributes[attribute.name] = attribute.value.slice(0, 300)
    })
    return attributes
  }

  const elementInfo = (element) => ({
    tagName: element.tagName.toLowerCase(),
    selector: selectorFor(element),
    source: element.getAttribute('data-ai-source') || undefined,
    id: element.id || undefined,
    className: element.getAttribute('class') || undefined,
    text: compactText(element) || undefined,
    attributes: collectAttributes(element),
    outerHTML: (element.outerHTML || '').slice(0, 1200),
    pageUrl: window.location.href
  })

  const placeOverlay = (overlay, element) => {
    if (!element || !element.isConnected) {
      overlay.style.setProperty('display', 'none', 'important')
      return
    }
    const rect = element.getBoundingClientRect()
    if (!rect.width && !rect.height) {
      overlay.style.setProperty('display', 'none', 'important')
      return
    }
    overlay.style.setProperty('display', 'block', 'important')
    overlay.style.setProperty('left', rect.left + 'px', 'important')
    overlay.style.setProperty('top', rect.top + 'px', 'important')
    overlay.style.setProperty('width', rect.width + 'px', 'important')
    overlay.style.setProperty('height', rect.height + 'px', 'important')
  }

  const refreshOverlays = () => {
    pointerFrame = null
    if (selectedTarget && !selectedTarget.isConnected) selectedTarget = null
    placeOverlay(selectedOverlay, selectedTarget)
    if (hoverTarget === selectedTarget) {
      hoverOverlay.style.setProperty('display', 'none', 'important')
    } else {
      placeOverlay(hoverOverlay, hoverTarget)
    }
  }

  const scheduleRefresh = () => {
    if (pointerFrame !== null) return
    pointerFrame = window.requestAnimationFrame(refreshOverlays)
  }

  const onPointerMove = (event) => {
    if (!enabled) return
    hoverTarget = isSelectable(event.target) ? event.target : null
    scheduleRefresh()
  }

  const preventDefaultAction = (event) => {
    event.preventDefault()
    event.stopPropagation()
    event.stopImmediatePropagation()
  }

  const onClick = (event) => {
    if (!enabled || !isSelectable(event.target)) return
    preventDefaultAction(event)
    selectedTarget = event.target
    hoverTarget = null
    refreshOverlays()
    window.parent.postMessage(
      { channel: CHANNEL, type: 'selected', payload: elementInfo(selectedTarget) },
      parentOrigin
    )
  }

  const onSubmit = (event) => {
    if (!enabled) return
    preventDefaultAction(event)
  }

  const clearSelection = () => {
    selectedTarget = null
    hoverTarget = null
    hoverOverlay.style.setProperty('display', 'none', 'important')
    selectedOverlay.style.setProperty('display', 'none', 'important')
  }

  const setMode = (nextEnabled) => {
    enabled = Boolean(nextEnabled)
    document.documentElement.style.setProperty(
      'cursor',
      enabled ? 'crosshair' : '',
      enabled ? 'important' : ''
    )
    if (!enabled) clearSelection()
  }

  const onScroll = () => {
    if (enabled) scheduleRefresh()
  }

  window.addEventListener('message', (event) => {
    if (event.source !== window.parent) return
    if (event.origin !== parentOrigin) return
    if (!event.data || event.data.channel !== CHANNEL) return

    if (event.data.type === 'set-mode') {
      const nextEnabled = Boolean(event.data.payload?.enabled)
      setMode(nextEnabled)
      window.parent.postMessage(
        { channel: CHANNEL, type: 'mode-changed', payload: { enabled: nextEnabled } },
        parentOrigin
      )
    } else if (event.data.type === 'clear-selection') {
      clearSelection()
    }
  })

  document.addEventListener('pointermove', onPointerMove, true)
  document.addEventListener('click', onClick, true)
  document.addEventListener('submit', onSubmit, true)
  window.addEventListener('scroll', onScroll, true)
  window.addEventListener('resize', onScroll, true)

  window[runtimeKey] = { setMode, clearSelection }
  setMode(currentScript?.dataset?.initialMode === 'true')
  window.parent.postMessage({ channel: CHANNEL, type: 'ready' }, parentOrigin)
})()
`

const isSameOrigin = (url: string) => {
  try {
    return new URL(url, window.location.href).origin === window.location.origin
  } catch {
    return false
  }
}

export const formatVisualElementLabel = (element?: VisualElementInfo): string => {
  if (!element) return ''
  const tag = `<${element.tagName}>`
  const location = element.source
    ? element.source.replace(/:(\d+)$/, ' ($1)')
    : element.selector
  return location ? `${tag} · ${location}` : tag
}

export const buildVisualPrompt = (
  request: string,
  element?: VisualElementInfo
): string => {
  if (!element) return request
  return [
    '请对以下选中元素进行修改：',
    '',
    `修改需求：${request}`,
    '',
    '内部定位说明：元素信息中的 data-ai-source 属性由构建阶段 Vite 插件自动注入，仅用于定位源码文件和行号。',
    '请忽略该属性：不要在源码中添加、删除或修改 data-ai-source，也不要在最终回复中提及该属性或定位插件。',
    '',
    SELECTED_ELEMENT_BEGIN,
    JSON.stringify(element),
    SELECTED_ELEMENT_END
  ].join('\n')
}

export const parseVisualPrompt = (content: string): ParsedVisualPrompt => {
  const start = content.indexOf(SELECTED_ELEMENT_BEGIN)
  const end = content.indexOf(SELECTED_ELEMENT_END)
  if (start < 0 || end < 0 || end <= start) {
    return { request: content }
  }

  const beforeContext = content.slice(0, start).trim()
  const requestBlock = beforeContext.split('\n\n内部定位说明：')[0]?.trim() || beforeContext
  const request = requestBlock.match(/修改需求：([\s\S]*)$/)?.[1]?.trim() || requestBlock
  const rawElement = content.slice(start + SELECTED_ELEMENT_BEGIN.length, end).trim()

  try {
    const element = JSON.parse(rawElement) as VisualElementInfo
    if (!element || typeof element.selector !== 'string' || typeof element.tagName !== 'string') {
      return { request }
    }
    return { request, element }
  } catch {
    return { request }
  }
}

/**
 * 管理同源 iframe 中的可视化编辑模式。
 */
export const useVisualEditor = ({ iframeRef, previewUrl }: UseVisualEditorOptions) => {
  const isEditing = ref(false)
  const selectedElement = ref<VisualElementInfo>()
  let targetOrigin = ''

  const postToIframe = (type: string, payload?: Record<string, unknown>) => {
    const iframe = iframeRef.value
    if (!iframe || !targetOrigin) return
    iframe.contentWindow?.postMessage({ channel: CHANNEL, type, payload }, targetOrigin)
  }

  const injectRuntime = (): boolean => {
    const iframe = iframeRef.value
    if (!iframe) return false

    try {
      const document = iframe.contentDocument
      if (!document) return false
      if (document.querySelector('script[data-ai-visual-editor-runtime]')) return true

      const script = document.createElement('script')
      script.setAttribute('data-ai-visual-editor-runtime', 'true')
      script.dataset.parentOrigin = window.location.origin
      script.dataset.initialMode = String(isEditing.value)
      script.textContent = IFRAME_RUNTIME
      ;(document.head || document.documentElement).appendChild(script)
      return true
    } catch (error) {
      console.warn('无法注入可视化编辑运行时：', error)
      return false
    }
  }

  const clearSelectedElement = () => {
    selectedElement.value = undefined
    postToIframe('clear-selection')
  }

  const exitEditing = () => {
    postToIframe('set-mode', { enabled: false })
    isEditing.value = false
    selectedElement.value = undefined
  }

  const beginEditing = (): boolean => {
    const iframe = iframeRef.value
    if (!iframe || !previewUrl.value) {
      message.warning('请等待网站预览加载完成')
      return false
    }
    if (!isSameOrigin(previewUrl.value)) {
      message.warning('预览页面与主站不同源，无法启用可视化编辑')
      return false
    }

    targetOrigin = new URL(previewUrl.value, window.location.href).origin
    isEditing.value = true
    if (!injectRuntime()) {
      isEditing.value = false
      message.warning('预览页面尚未加载完成，请稍后重试')
      return false
    }
    postToIframe('set-mode', { enabled: true })
    return true
  }

  const toggleEditing = () => {
    if (isEditing.value) {
      exitEditing()
      return
    }
    beginEditing()
  }

  const handleIframeLoad = () => {
    selectedElement.value = undefined
    if (!isEditing.value || !previewUrl.value) return
    targetOrigin = new URL(previewUrl.value, window.location.href).origin
    if (injectRuntime()) {
      postToIframe('set-mode', { enabled: true })
    } else {
      isEditing.value = false
    }
  }

  const handleMessage = (event: MessageEvent) => {
    const iframe = iframeRef.value
    if (!iframe || event.source !== iframe.contentWindow) return
    if (event.origin !== targetOrigin) return
    if (!event.data || event.data.channel !== CHANNEL) return

    if (event.data.type === 'ready' && isEditing.value) {
      postToIframe('set-mode', { enabled: true })
      return
    }
    if (event.data.type !== 'selected') return

    const payload = event.data.payload as VisualElementInfo | undefined
    if (!payload || typeof payload.selector !== 'string' || typeof payload.tagName !== 'string') {
      return
    }
    selectedElement.value = payload
  }

  window.addEventListener('message', handleMessage)
  onUnmounted(() => {
    window.removeEventListener('message', handleMessage)
    postToIframe('set-mode', { enabled: false })
  })

  return {
    isEditing,
    selectedElement,
    beginEditing,
    exitEditing,
    toggleEditing,
    handleIframeLoad,
    clearSelectedElement,
    buildVisualPrompt,
    parseVisualPrompt,
    formatVisualElementLabel
  }
}
