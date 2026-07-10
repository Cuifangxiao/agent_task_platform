import assert from 'node:assert/strict'
import test from 'node:test'

import { assetCacheKey, eventSequenceDecision, interactionPhase, modelNameSupportsImageInput } from '../src/pptAgentWorkflow.ts'

test('job states expose the correct key interaction phase', () => {
  assert.equal(interactionPhase('queued'), 'initial')
  assert.equal(interactionPhase('waiting_for_outline'), 'outline')
  assert.equal(interactionPhase('waiting_for_style'), 'style')
  assert.equal(interactionPhase('running'), 'pages')
  assert.equal(interactionPhase('exporting'), 'pages')
  assert.equal(interactionPhase('success_with_warnings'), 'completed')
  assert.equal(interactionPhase('needs_review'), 'completed')
  assert.equal(interactionPhase('cancelled'), 'failed')
})

test('replayed SSE sequences are rejected while newer events advance the cursor', () => {
  const seen = new Set([3, 4])
  assert.deepEqual(eventSequenceDecision(4, seen, 4), { accept: false, last: 4 })
  assert.deepEqual(eventSequenceDecision(4, seen, 5), { accept: true, last: 5 })
})

test('preview cache keys change when a slide version changes', () => {
  assert.notEqual(assetCacheKey('request', 'slides/3.png', 1), assetCacheKey('request', 'slides/3.png', 2))
})

test('Qwen VL model names are recognized as image-capable', () => {
  assert.equal(modelNameSupportsImageInput('qwen3-vl-plus'), true)
  assert.equal(modelNameSupportsImageInput('qwen-vl-plus'), true)
  assert.equal(modelNameSupportsImageInput('qwen-plus'), false)
})
