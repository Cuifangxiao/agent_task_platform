import { mkdir, writeFile } from 'node:fs/promises'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const repositoryRoot = resolve(here, '../../..')
const lucideIcons = join(repositoryRoot, 'apps/web/node_modules/@lucide/vue/dist/esm/icons')
const output = join(repositoryRoot, 'apps/runtime/core/deck/design_system/html/icons/lucide.json')

const requested = {
  rocket: ['launch', 'growth', 'start', '产品', '增长', '启动'],
  route: ['journey', 'path', 'roadmap', '路线', '路径'],
  target: ['goal', 'objective', 'focus', '目标', '聚焦'],
  lightbulb: ['idea', 'insight', 'innovation', '洞察', '创意'],
  'chart-no-axes-combined': ['chart', 'growth', 'metric', '趋势', '指标'],
  users: ['team', 'audience', 'customer', '团队', '用户'],
  'shield-check': ['security', 'trust', 'compliance', '安全', '可信'],
  clock: ['time', 'schedule', 'speed', '时间', '进度'],
  layers: ['stack', 'architecture', 'system', '层级', '架构'],
  workflow: ['process', 'automation', 'flow', '流程', '自动化'],
  'circle-check': ['done', 'success', 'approved', '完成', '成功'],
  'triangle-alert': ['risk', 'warning', 'problem', '风险', '警告'],
  sparkles: ['ai', 'new', 'quality', '智能', '创新'],
  globe: ['global', 'market', 'world', '全球', '市场'],
  database: ['data', 'storage', 'platform', '数据', '存储'],
  cpu: ['compute', 'ai', 'technology', '算力', '技术'],
  network: ['connection', 'ecosystem', 'graph', '连接', '网络'],
  scale: ['balance', 'tradeoff', 'legal', '平衡', '权衡'],
  'arrow-up-right': ['increase', 'next', 'direction', '增长', '下一步'],
  gauge: ['performance', 'speed', 'kpi', '性能', '指标'],
  'book-open': ['research', 'learning', 'knowledge', '研究', '学习'],
  'heart-pulse': ['health', 'care', 'medical', '健康', '医疗'],
  handshake: ['partner', 'agreement', 'collaboration', '合作', '共识'],
  flag: ['milestone', 'finish', 'outcome', '里程碑', '结果']
}

const icons = []
for (const [key, tags] of Object.entries(requested)) {
  const moduleUrl = pathToFileURL(join(lucideIcons, `${key}.mjs`)).href
  const loaded = await import(moduleUrl)
  const nodes = loaded.__iconNode.map(([tag, attributes]) => ({
    tag,
    attributes: Object.fromEntries(
      Object.entries(attributes)
        .filter(([name]) => name !== 'key')
        .sort(([a], [b]) => a.localeCompare(b))
    )
  }))
  icons.push({ key, tags, view_box: '0 0 24 24', nodes })
}

const registry = {
  schema: 'trusted-icon-registry.v1',
  source: '@lucide/vue 1.23.0',
  license: 'ISC (some Feather-derived icons are MIT; see LICENSE.txt)',
  icons
}

await mkdir(dirname(output), { recursive: true })
await writeFile(output, `${JSON.stringify(registry, null, 2)}\n`, 'utf8')
