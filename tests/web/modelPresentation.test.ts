import assert from 'node:assert/strict'
import test from 'node:test'

import {
  inferModelType,
  inferModelTypeFromName,
  normalizeModelType,
  resolveProviderBrand,
} from '../../apps/web/src/features/models/modelPresentation.ts'

test('normalizes missing and unknown persisted model types as text', () => {
  assert.equal(normalizeModelType(undefined), 'text')
  assert.equal(normalizeModelType('chat'), 'text')
  assert.equal(normalizeModelType('multimodal'), 'multimodal')
  assert.equal(normalizeModelType('image'), 'image')
})

test('uses normalized provider catalog type as the source of truth', () => {
  assert.equal(inferModelType({ model_name: 'gpt-5-vision-like-name', type: 'text' }), 'text')
  assert.equal(inferModelType({ model_name: 'plain-name', type: 'multimodal' }), 'multimodal')
  assert.equal(inferModelType({ model_name: 'plain-name', type: 'image' }), 'image')
})

test('infers capabilities for legacy chat catalogs', () => {
  assert.equal(inferModelType({ model_name: 'qwen3-vl-plus', type: 'chat' }), 'multimodal')
  assert.equal(inferModelType({ model_name: 'gpt-5.4-mini', type: 'chat' }), 'multimodal')
  assert.equal(inferModelType({ model_name: 'deepseek-v4-pro', type: 'chat' }), 'text')
  assert.equal(inferModelTypeFromName('gpt-image-1'), 'image')
})

test('maps known provider aliases and preserves a generic fallback', () => {
  assert.equal(resolveProviderBrand('openai'), 'openai')
  assert.equal(resolveProviderBrand('DashScope'), 'qwen')
  assert.equal(resolveProviderBrand('deepseek-compatible'), 'deepseek')
  assert.equal(resolveProviderBrand('local-ollama'), 'generic')
})
