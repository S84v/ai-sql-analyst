/**
 * Transport boundary for the AI SQL Analyst query stream.
 *
 * This module knows only the application-level SSE protocol exposed by
 * `POST /query` (ADR-006): `status`, `answer_delta`, `done`, and `error`.
 * It has no React dependency and no knowledge of the backend's internal
 * LangGraph or DeepSeek structures; the JSON payload's `type` is authoritative
 * and the SSE `event:` line is treated as transport metadata only.
 */

export type QueryEvent =
  | { type: 'status'; message: string }
  | { type: 'answer_delta'; text: string }
  | { type: 'done' }
  | { type: 'error'; message: string }

const QUERY_ENDPOINT = '/query'

/** Narrow one decoded SSE `data:` payload to a trusted application event. */
function parseEvent(payload: string): QueryEvent {
  let value: unknown
  try {
    value = JSON.parse(payload)
  } catch {
    throw new Error('Malformed query stream: event payload is not valid JSON')
  }
  if (typeof value !== 'object' || value === null) {
    throw new Error('Malformed query stream: event payload is not an object')
  }

  const event = value as Record<string, unknown>
  switch (event.type) {
    case 'status':
      if (typeof event.message === 'string') {
        return { type: 'status', message: event.message }
      }
      break
    case 'answer_delta':
      if (typeof event.text === 'string') {
        return { type: 'answer_delta', text: event.text }
      }
      break
    case 'done':
      return { type: 'done' }
    case 'error':
      if (typeof event.message === 'string') {
        return { type: 'error', message: event.message }
      }
      break
  }

  throw new Error(
    `Malformed query stream: invalid application event ${JSON.stringify(event.type)}`,
  )
}

/**
 * Extract the joined `data:` payload from one SSE frame, or `null` when the
 * frame carries no data (for example a `: ping` keepalive comment). Blank lines
 * delimit fields and `:`-prefixed lines are comments, per the SSE spec.
 */
function extractData(frame: string): string | null {
  const dataLines: string[] = []
  for (const line of frame.split(/\r\n|\n|\r/)) {
    if (line === '' || line.startsWith(':')) continue
    if (line.startsWith('data:')) {
      dataLines.push(line.slice(5).replace(/^ /, ''))
    }
  }
  return dataLines.length === 0 ? null : dataLines.join('\n')
}

/**
 * Stream the application events for one independent analytical question.
 *
 * Reconstruct the final answer by concatenating every `answer_delta.text` in
 * order. `done` and `error` are terminal: once a valid one is yielded the
 * generator finishes and ignores anything sent afterwards. A valid `error` is
 * yielded normally (not thrown). Malformed JSON, invalid event shapes, an
 * unterminated final frame, and EOF without a terminal event are protocol
 * errors and throw.
 */
export async function* streamQuery(
  question: string,
): AsyncGenerator<QueryEvent> {
  const response = await fetch(QUERY_ENDPOINT, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Accept: 'text/event-stream',
    },
    body: JSON.stringify({ question }),
  })

  // Reject HTTP failures before touching the stream body.
  if (!response.ok) {
    throw new Error(`Query request failed with HTTP ${response.status}`)
  }
  if (response.body === null) {
    throw new Error('Query response has no body to stream')
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  // Raw text that has not yet formed a complete frame (frames end with a blank
  // line). Retaining it across reads is what makes arbitrary chunk boundaries
  // and multiple frames per chunk safe.
  let buffer = ''

  const emit = (frame: string): QueryEvent | null => {
    const data = extractData(frame)
    if (data === null) return null
    return parseEvent(data)
  }

  try {
    for (;;) {
      const { value, done } = await reader.read()
      if (done) break
      // Normalizing after each append tolerates a CRLF split across chunks,
      // since a lone trailing "\r" is only joined on the next read.
      buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, '\n')
      let boundary = buffer.indexOf('\n\n')
      while (boundary !== -1) {
        const frame = buffer.slice(0, boundary)
        buffer = buffer.slice(boundary + 2)
        const event = emit(frame)
        if (event !== null) {
          yield event
          // A terminal application event ends the stream. Return rather than
          // reading on, so nothing sent after it can be parsed or accepted.
          if (event.type === 'done' || event.type === 'error') return
        }
        boundary = buffer.indexOf('\n\n')
      }
    }

    // Flush any pending multi-byte sequence before judging the remainder.
    buffer += decoder.decode()
    // A non-empty remainder means the server closed mid-frame, without the
    // blank-line terminator required by SSE framing. Its content is never
    // parsed: an unterminated frame is a premature-EOF protocol error even if
    // the bytes happen to form valid JSON.
    if (buffer.trim() !== '') {
      throw new Error('Query stream ended with an unterminated event')
    }
    // No terminal event was yielded, so the stream ended prematurely.
    throw new Error('Query stream ended without a done or error event')
  } finally {
    // Release the network stream on normal completion or early consumer exit.
    await reader.cancel().catch(() => {})
  }
}
