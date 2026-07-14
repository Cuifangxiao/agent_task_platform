const SUPPORTED_SOURCE_FILE = /\.(pdf|txt|md|markdown|csv|json|ya?ml|docx|png|jpe?g|webp)$/i
const MAX_SOURCE_FILE_BYTES = 20 * 1024 * 1024
const MAX_TOTAL_SOURCE_BYTES = 100 * 1024 * 1024
const MAX_DESCRIPTION_CHARACTERS = 5_000

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
