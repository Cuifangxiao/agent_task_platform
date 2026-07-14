import assert from 'node:assert/strict'
import test from 'node:test'

import { assetCacheKey, eventSequenceDecision, interactionPhase, modelNameSupportsImageInput } from '../../apps/web/src/features/presentations/lib/workflow.ts'
import {
  currentProgressLabel,
  currentProgressStageLabel,
  normalizeProgressStage,
  pipelineStages,
} from '../../apps/web/src/features/presentations/lib/progressView.ts'
import type { StatusMessage } from '../../apps/web/src/features/presentations/types.ts'
import { validateSourceFiles } from '../../apps/web/src/features/presentations/lib/presentationUtils.ts'

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

test('design lock is a first-class progress stage before page writing', () => {
  assert.equal(normalizeProgressStage('design_lock'), 'design_lock')
  const keys = pipelineStages.map((stage) => stage.key)
  assert.ok(keys.indexOf('style_review') < keys.indexOf('design_lock'))
  assert.ok(keys.indexOf('design_lock') < keys.indexOf('writer'))
})

test('current progress reflects the latest failure instead of the previous stage', () => {
  const message: StatusMessage = {
    id: 1,
    role: 'assistant',
    kind: 'status',
    title: 'Failed',
    text: 'The run stopped',
    state: 'error',
    slides: [],
    steps: [
      { id: 1, label: 'Writing', message: 'Writing slides', stage: 'writer', tone: 'normal' },
      { id: 2, label: 'Error', message: 'Model request failed', stage: 'error', tone: 'error' },
    ],
  }

  assert.equal(currentProgressStageLabel(message), 'Error')
  assert.equal(currentProgressLabel(message), 'Model request failed')
})

test('presentation uploads accept safe raster image formats and keep the 20 MB limit', () => {
  assert.equal(validateSourceFiles([{ name: 'hero.webp', size: 1024 } as File]), '')
  assert.equal(validateSourceFiles([{ name: 'photo.jpeg', size: 1024 } as File]), '')
  assert.match(validateSourceFiles([{ name: 'vector.svg', size: 1024 } as File]), /不支持/)
  assert.match(validateSourceFiles([{ name: 'huge.png', size: 20 * 1024 * 1024 + 1 } as File]), /20 MB/)
})
