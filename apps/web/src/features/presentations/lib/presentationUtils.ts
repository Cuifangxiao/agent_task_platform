const SUPPORTED_SOURCE_FILE = /\.(pdf|txt|md|markdown|csv|json|ya?ml|docx|png|jpe?g|webp)$/i
const MAX_SOURCE_FILE_BYTES = 20 * 1024 * 1024
const MAX_TOTAL_SOURCE_BYTES = 100 * 1024 * 1024
export const MAX_DESCRIPTION_CHARACTERS = 5_000

export interface PresentationSubmissionIdentity {
  agentConfigId: string | number
  description: string
  sourceFiles: ReadonlyArray<Pick<File, 'name' | 'size' | 'type' | 'lastModified'>>
}

export interface PresentationSubmissionAttempt {
  fingerprint: string
  threadId: string
  clientRequestId: string
}

export function createPresentationId(
  cryptoApi: Pick<Crypto, 'randomUUID' | 'getRandomValues'> | null = globalThis.crypto,
) {
  if (typeof cryptoApi?.randomUUID === 'function') return cryptoApi.randomUUID()

  const bytes = new Uint8Array(16)
  if (typeof cryptoApi?.getRandomValues === 'function') {
    cryptoApi.getRandomValues(bytes)
  } else {
    for (let index = 0; index < bytes.length; index += 1) {
      bytes[index] = Math.floor(Math.random() * 256)
    }
  }
  bytes[6] = ((bytes[6] ?? 0) & 0x0f) | 0x40
  bytes[8] = ((bytes[8] ?? 0) & 0x3f) | 0x80
  const hex = [...bytes].map((value) => value.toString(16).padStart(2, '0')).join('')
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`
}

export function createPresentationSubmissionAttempt(
  identity: PresentationSubmissionIdentity,
  previous: PresentationSubmissionAttempt | null = null,
  createId: () => string = createPresentationId,
) {
  const fingerprint = presentationSubmissionFingerprint(identity)
  if (previous?.fingerprint === fingerprint) return previous
  return {
    fingerprint,
    threadId: createId(),
    clientRequestId: createId(),
  }
}

export function presentationSubmissionFingerprint(identity: PresentationSubmissionIdentity) {
  return JSON.stringify({
    agent_config_id: String(identity.agentConfigId),
    description: identity.description,
    source_files: identity.sourceFiles.map((file) => ({
      name: file.name,
      size: file.size,
      type: file.type,
      last_modified: file.lastModified,
    })),
  })
}

export function validatePresentationDescription(description: string) {
  const normalized = description.trim()
  if (!normalized) return '先描述一下你想生成的 PPT 内容。'
  return Array.from(normalized).length > MAX_DESCRIPTION_CHARACTERS
    ? `PPT 描述不能超过 ${MAX_DESCRIPTION_CHARACTERS} 个字符。`
    : ''
}

export function validateSourceFiles(files: File[]) {
  let total = 0
  for (const file of files) {
    if (!SUPPORTED_SOURCE_FILE.test(file.name)) {
      return `不支持 ${file.name} 的格式。请上传 PDF、DOCX、Markdown、TXT、CSV、JSON、YAML、PNG、JPEG 或 WebP。`
    }
    if (file.size <= 0) {
      return `${file.name} 是空文件，请选择包含内容的文件。`
    }
    if (file.size > MAX_SOURCE_FILE_BYTES) {
      return `${file.name} 超过 20 MB 单文件限制。`
    }
    total += file.size
  }
  return total > MAX_TOTAL_SOURCE_BYTES ? '附件总大小不能超过 100 MB。' : ''
}

export function formatDateTime(value: string | undefined) {
  if (!value) return ''
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
}
