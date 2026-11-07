import path from 'node:path'
import { parse } from 'vue/compiler-sfc'

const SOURCE_ATTRIBUTE = 'data-ai-source'
const SKIPPED_TAGS = new Set(['script', 'style', 'template'])

const escapeAttribute = (value) =>
  value
    .replace(/&/g, '&amp;')
    .replace(/"/g, '&quot;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')

const hasSourceAttribute = (node) =>
  Array.isArray(node.props) &&
  node.props.some((prop) => prop?.type === 6 && prop.name === SOURCE_ATTRIBUTE)

const visitElements = (node, visitor) => {
  if (!node || typeof node !== 'object') return
  if (node.type === 1) visitor(node)
  if (Array.isArray(node.children)) {
    node.children.forEach((child) => visitElements(child, visitor))
  }
}

/**
 * 为 Vue 模板中的原生 DOM 元素注入源码路径和行号。
 *
 * 属性只存在于 Vite 的编译输入中，不会回写 .vue 源文件。
 */
export function vueSourceLocator() {
  let root = process.cwd()

  return {
    name: 'ai-code-vue-source-locator',
    enforce: 'pre',
    configResolved(config) {
      root = config.root || root
    },
    transform(code, id) {
      const [filename] = id.split('?')
      if (!filename.endsWith('.vue')) return null

      const { descriptor, errors } = parse(code, { filename })
      if (errors?.length || !descriptor.template?.ast) return null

      const relativePath = path.relative(root, filename).split(path.sep).join('/')
      const insertions = []

      visitElements(descriptor.template.ast, (node) => {
        if (node.tagType !== 0) return
        const tag = String(node.tag || '').toLowerCase()
        if (!tag || SKIPPED_TAGS.has(tag)) return
        if (hasSourceAttribute(node)) return

        const start = node.loc?.start
        if (!start || typeof start.offset !== 'number' || typeof start.line !== 'number') return

        const source = `${relativePath}:${start.line}`
        insertions.push({
          offset: start.offset + 1 + node.tag.length,
          text: ` ${SOURCE_ATTRIBUTE}="${escapeAttribute(source)}"`
        })
      })

      if (!insertions.length) return null

      let transformed = code
      insertions
        .sort((left, right) => right.offset - left.offset)
        .forEach(({ offset, text }) => {
          transformed = transformed.slice(0, offset) + text + transformed.slice(offset)
        })

      return { code: transformed, map: null }
    }
  }
}
