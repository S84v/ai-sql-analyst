import { afterEach, describe, expect, it, vi } from 'vitest'
import { streamQuery, type QueryEvent } from './api'

/** Build a deterministic body stream from already-encoded text chunks. */
function bodyOf(chunks: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder()
  return new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk))
      controller.close()
    },
  })
}

/** Stub `fetch` with a single canned response. */
function mockFetch(body: ReadableStream<Uint8Array> | null, status = 200) {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    body,
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

async function collect(gen: AsyncGenerator<QueryEvent>): Promise<QueryEvent[]> {
  const events: QueryEvent[] = []
  for await (const event of gen) events.push(event)
  return events
}

/** One SSE frame terminated by a blank line. */
function frame(...lines: string[]): string {
  return lines.join('\n') + '\n\n'
}

const STATUS = frame('event: status', 'data: {"type":"status","message":"Analyzing"}')
const DONE = frame('event: done', 'data: {"type":"done"}')

afterEach(() => {
  vi.unstubAllGlobals()
  vi.unstubAllEnvs()
})

describe('streamQuery', () => {
  it('yields a valid status event', async () => {
    mockFetch(bodyOf([STATUS + DONE]))
    expect(await collect(streamQuery('q'))).toEqual([
      { type: 'status', message: 'Analyzing' },
      { type: 'done' },
    ])
  })

  it('yields a valid answer_delta event', async () => {
    const delta = frame('data: {"type":"answer_delta","text":"Hello "}')
    mockFetch(bodyOf([delta + DONE]))
    expect(await collect(streamQuery('q'))).toEqual([
      { type: 'answer_delta', text: 'Hello ' },
      { type: 'done' },
    ])
  })

  it('yields done and terminates', async () => {
    mockFetch(bodyOf([DONE]))
    expect(await collect(streamQuery('q'))).toEqual([{ type: 'done' }])
  })

  it('yields a valid error event without throwing', async () => {
    const error = frame('data: {"type":"error","message":"backend failed"}')
    mockFetch(bodyOf([error]))
    expect(await collect(streamQuery('q'))).toEqual([
      { type: 'error', message: 'backend failed' },
    ])
  })

  it('joins multiple data lines in one frame', async () => {
    const split = frame('data: {"type":"answer_delta",', 'data: "text":"Hello world"}')
    mockFetch(bodyOf([split + DONE]))
    expect(await collect(streamQuery('q'))).toEqual([
      { type: 'answer_delta', text: 'Hello world' },
      { type: 'done' },
    ])
  })

  it('ignores keepalive comment frames', async () => {
    mockFetch(bodyOf([frame(': ping') + DONE]))
    expect(await collect(streamQuery('q'))).toEqual([{ type: 'done' }])
  })

  it('reassembles frames split across arbitrary chunks', async () => {
    const text = STATUS + DONE
    mockFetch(bodyOf([...text]))
    expect(await collect(streamQuery('q'))).toEqual([
      { type: 'status', message: 'Analyzing' },
      { type: 'done' },
    ])
  })

  it('parses multiple frames delivered in one chunk', async () => {
    mockFetch(bodyOf([STATUS + DONE]))
    expect(await collect(streamQuery('q'))).toEqual([
      { type: 'status', message: 'Analyzing' },
      { type: 'done' },
    ])
  })

  it('stops parsing after a terminal done event', async () => {
    mockFetch(bodyOf([DONE + frame('data: not-json')]))
    expect(await collect(streamQuery('q'))).toEqual([{ type: 'done' }])
  })

  it('stops parsing after a terminal error event', async () => {
    const error = frame('data: {"type":"error","message":"boom"}')
    mockFetch(bodyOf([error + frame('data: not-json')]))
    expect(await collect(streamQuery('q'))).toEqual([
      { type: 'error', message: 'boom' },
    ])
  })

  it('throws on a non-2xx HTTP response', async () => {
    mockFetch(bodyOf([DONE]), 500)
    await expect(collect(streamQuery('q'))).rejects.toThrow(/HTTP 500/)
  })

  it('throws when the response has no body', async () => {
    mockFetch(null)
    await expect(collect(streamQuery('q'))).rejects.toThrow(/no body/)
  })

  it('throws on malformed JSON', async () => {
    mockFetch(bodyOf([frame('data: not-json')]))
    await expect(collect(streamQuery('q'))).rejects.toThrow(/not valid JSON/)
  })

  it('throws on an invalid application event shape', async () => {
    mockFetch(bodyOf([frame('data: {"type":"status"}')]))
    await expect(collect(streamQuery('q'))).rejects.toThrow(/invalid application event/)
  })

  it('throws on an unterminated final frame', async () => {
    mockFetch(bodyOf(['data: {"type":"done"}']))
    await expect(collect(streamQuery('q'))).rejects.toThrow(/unterminated/)
  })

  it('throws on EOF without a done or error event', async () => {
    mockFetch(bodyOf([STATUS]))
    await expect(collect(streamQuery('q'))).rejects.toThrow(/without a done or error/)
  })
})

describe('query endpoint resolution', () => {
  /** The first fetch URL used by one streamQuery call. */
  async function endpointFor(origin?: string): Promise<unknown> {
    if (origin !== undefined) vi.stubEnv('VITE_API_ORIGIN', origin)
    const fetchMock = mockFetch(bodyOf([DONE]))
    await collect(streamQuery('q'))
    return fetchMock.mock.calls[0][0]
  }

  it('uses the relative /query path when VITE_API_ORIGIN is unset', async () => {
    expect(await endpointFor()).toBe('/query')
  })

  it('uses an explicit API origin when configured', async () => {
    expect(await endpointFor('https://olistiq.run.app')).toBe(
      'https://olistiq.run.app/query',
    )
  })

  it('normalizes trailing slashes in the configured origin', async () => {
    expect(await endpointFor('https://olistiq.run.app/')).toBe(
      'https://olistiq.run.app/query',
    )
  })

  it('treats a blank/whitespace origin as unset', async () => {
    expect(await endpointFor('   ')).toBe('/query')
  })

  it('keeps the POST + SSE request shape unchanged', async () => {
    const fetchMock = mockFetch(bodyOf([DONE]))
    await collect(streamQuery('How many orders?'))
    const [, init] = fetchMock.mock.calls[0]
    expect(init.method).toBe('POST')
    expect(init.headers).toMatchObject({
      'Content-Type': 'application/json',
      Accept: 'text/event-stream',
    })
    expect(init.body).toBe(JSON.stringify({ question: 'How many orders?' }))
  })
})
