export interface ParsedSseEvent {
  id?: number
  event: string
  data: unknown
}

export async function consumeEventStream(
  stream: ReadableStream<Uint8Array>,
  onEvent: (event: ParsedSseEvent) => void,
) {
  const reader = stream.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    buffer += decoder.decode(value ?? new Uint8Array(), { stream: !done }).replace(/\r/g, '')

    let boundary = buffer.indexOf('\n\n')
    while (boundary >= 0) {
      emitBlock(buffer.slice(0, boundary), onEvent)
      buffer = buffer.slice(boundary + 2)
      boundary = buffer.indexOf('\n\n')
    }

    if (done) {
      emitBlock(buffer, onEvent)
      return
    }
  }
}

export function parseEventBlock(block: string): ParsedSseEvent | null {
  let event = 'message'
  let id: number | undefined
  const data: string[] = []

  for (const line of block.trim().split('\n')) {
    if (line.startsWith('event:')) {
      event = line.slice(6).trim() || event
    } else if (line.startsWith('id:')) {
      const parsed = Number(line.slice(3).trim())
      if (Number.isFinite(parsed) && parsed >= 0) id = parsed
    } else if (line.startsWith('data:')) {
      data.push(line.slice(5).trimStart())
    }
  }

  if (data.length === 0) {
    return null
  }
  const raw = data.join('\n')
  try {
    return { event, id, data: JSON.parse(raw) }
  } catch {
    return { event, id, data: raw }
  }
}

function emitBlock(block: string, onEvent: (event: ParsedSseEvent) => void) {
  const parsed = parseEventBlock(block)
  if (parsed) onEvent(parsed)
}
