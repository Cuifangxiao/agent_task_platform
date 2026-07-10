import { onBeforeUnmount, reactive, ref } from 'vue'

import type { PresentationApi } from '../api'
import { assetCacheKey } from '../lib/workflow'
import type { PreviewLightbox } from '../types'

export function usePresentationAssets(api: PresentationApi, onError: (message: string) => void) {
  const objectUrls = reactive<Record<string, string>>({})
  const lightbox = ref<PreviewLightbox | null>(null)
  const failedLoads = new Set<string>()
  const pendingLoads = new Set<string>()

  onBeforeUnmount(() => {
    Object.values(objectUrls).forEach((url) => URL.revokeObjectURL(url))
  })

  async function downloadDeck(requestId: string, preferredName: string) {
    onError('')
    try {
      const result = await api.downloadArtifact(requestId)
      const url = URL.createObjectURL(result.blob)
      const link = document.createElement('a')
      link.href = url
      link.download = result.filename || preferredName || `deck-${requestId}.pptx`
      document.body.appendChild(link)
      link.click()
      link.remove()
      URL.revokeObjectURL(url)
    } catch (error) {
      onError(error instanceof Error ? error.message : String(error))
    }
  }

  function previewSrc(requestId: string | undefined, thumbB64: string, pngPath: string) {
    const thumbUrl = thumbB64 ? `data:image/webp;base64,${thumbB64}` : ''
    const normalizedPath = pngPath.trim()
    if (!normalizedPath) return thumbUrl
    if (/^(data:|blob:|https?:\/\/)/i.test(normalizedPath)) return normalizedPath

    const cacheKey = assetCacheKey(requestId, normalizedPath, versionFromAssetPath(normalizedPath))
    if (objectUrls[cacheKey]) return objectUrls[cacheKey]
    if (requestId && !failedLoads.has(cacheKey)) {
      void loadPreview(cacheKey, requestId, normalizedPath)
    }
    return thumbUrl
  }

  function openPreview(src: string, title: string, subtitle: string) {
    if (src) lightbox.value = { src, title, subtitle }
  }

  function closePreview() {
    lightbox.value = null
  }

  async function loadPreview(cacheKey: string, requestId: string, relativePath: string) {
    if (pendingLoads.has(cacheKey)) return
    pendingLoads.add(cacheKey)
    try {
      const blob = await api.downloadAsset(requestId, relativePath)
      objectUrls[cacheKey] = URL.createObjectURL(blob)
    } catch {
      failedLoads.add(cacheKey)
    } finally {
      pendingLoads.delete(cacheKey)
    }
  }

  return { closePreview, downloadDeck, openPreview, previewLightbox: lightbox, previewSrc }
}

function versionFromAssetPath(path: string) {
  const match = path.match(/_v(\d+)\.[^.]+$/i)
  return match ? Number(match[1]) : 1
}
