import type { PptStreamChunk } from '../types'

const SUPPORTED_SOURCE_FILE = /\.(pdf|txt|md|markdown|csv|json|ya?ml|docx|png|jpe?g|webp)$/i
const MAX_SOURCE_FILE_BYTES = 20 * 1024 * 1024
const MAX_TOTAL_SOURCE_BYTES = 100 * 1024 * 1024

export function validateSourceFiles(files: File[]) {
  let total = 0
  for (const file of files) {
    if (!SUPPORTED_SOURCE_FILE.test(file.name)) {
      return `不支持 ${file.name} 的格式。请上传 PDF、DOCX、Markdown、TXT、CSV、JSON、YAML、PNG、JPEG 或 WebP。`
    }
    if (file.size > MAX_SOURCE_FILE_BYTES) {
      return `${file.name} 超过 20 MB 单文件限制。`
    }
    total += file.size
  }
  return total > MAX_TOTAL_SOURCE_BYTES ? '附件总大小不能超过 100 MB。' : ''
}

export function normalizeStreamChunk(value: unknown): PptStreamChunk {
  return value && typeof value === 'object' ? (value as PptStreamChunk) : { message: String(value) }
}

export function formatHistoryTime(value: string) {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
}
