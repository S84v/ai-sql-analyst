import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App'
import { streamQuery, type QueryEvent } from './api'

vi.mock('./api', () => ({ streamQuery: vi.fn() }))

const mockStreamQuery = vi.mocked(streamQuery)

function scripted(events: QueryEvent[]): AsyncGenerator<QueryEvent> {
  return (async function* generate() {
    for (const event of events) yield event
  })()
}

async function ask(question: string) {
  fireEvent.change(screen.getByLabelText('Your question'), {
    target: { value: question },
  })
  fireEvent.click(screen.getByRole('button', { name: 'Ask' }))
}

beforeEach(() => {
  mockStreamQuery.mockReset()
  mockStreamQuery.mockImplementation(() => scripted([{ type: 'done' }]))
})

describe('App', () => {
  it('renders the question input and Ask button initially', () => {
    render(<App />)
    expect(screen.getByLabelText('Your question')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Ask' })).toBeInTheDocument()
  })

  it('does not submit a blank question', () => {
    render(<App />)
    fireEvent.change(screen.getByLabelText('Your question'), {
      target: { value: '   ' },
    })
    const askButton = screen.getByRole('button', { name: 'Ask' })
    expect(askButton).toBeDisabled()
    fireEvent.click(askButton)
    expect(mockStreamQuery).not.toHaveBeenCalled()
  })

  it('submits the trimmed question through the transport', async () => {
    render(<App />)
    await ask('  How many orders?  ')
    await waitFor(() =>
      expect(mockStreamQuery).toHaveBeenCalledWith('How many orders?'),
    )
  })

  it('displays backend status events', async () => {
    let release!: () => void
    const gate = new Promise<void>((resolve) => {
      release = resolve
    })
    mockStreamQuery.mockImplementation(() =>
      (async function* generate() {
        yield { type: 'status', message: 'Running analytical query' }
        await gate
        yield { type: 'done' }
      })(),
    )

    render(<App />)
    await ask('How many orders?')

    expect(
      (await screen.findAllByText('Running analytical query')).length,
    ).toBeGreaterThan(0)
    release()
    await screen.findByRole('heading', { name: 'Answer' })
  })

  it('accumulates multiple answer_delta events in order', async () => {
    mockStreamQuery.mockImplementation(() =>
      scripted([
        { type: 'answer_delta', text: 'There were ' },
        { type: 'answer_delta', text: '95 orders.' },
        { type: 'done' },
      ]),
    )

    render(<App />)
    await ask('How many orders?')

    expect(await screen.findByText('There were 95 orders.')).toBeInTheDocument()
  })

  it('shows the completed answer state after done', async () => {
    mockStreamQuery.mockImplementation(() =>
      scripted([{ type: 'answer_delta', text: 'Final answer.' }, { type: 'done' }]),
    )

    render(<App />)
    await ask('How many orders?')

    expect(await screen.findByRole('heading', { name: 'Answer' })).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('renders the backend error message for an application error event', async () => {
    mockStreamQuery.mockImplementation(() =>
      scripted([{ type: 'error', message: 'Backend is unavailable' }]),
    )

    render(<App />)
    await ask('How many orders?')

    expect(await screen.findByRole('alert')).toHaveTextContent('Backend is unavailable')
    expect(screen.getByRole('heading', { name: 'Error' })).toBeInTheDocument()
  })

  it('renders the generic message for a transport failure', async () => {
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    mockStreamQuery.mockImplementation(() => {
      throw new Error('socket exploded')
    })

    render(<App />)
    await ask('How many orders?')

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Something went wrong while running the analysis. Please try again.',
    )
    errorSpy.mockRestore()
  })

  it('populates the textarea from an example question', () => {
    render(<App />)
    fireEvent.click(
      screen.getByRole('button', {
        name: /Which states had the most customers/,
      }),
    )
    expect(screen.getByLabelText('Your question')).toHaveValue(
      'Which states had the most customers?',
    )
  })

  it('does not start a second request while one is in flight', async () => {
    let release!: () => void
    const gate = new Promise<void>((resolve) => {
      release = resolve
    })
    mockStreamQuery.mockImplementation(() =>
      (async function* generate() {
        yield { type: 'status', message: 'Running analytical query' }
        await gate
        yield { type: 'done' }
      })(),
    )

    render(<App />)
    await ask('How many orders?')
    await screen.findByRole('button', { name: 'Analyzing…' })

    fireEvent.keyDown(screen.getByLabelText('Your question'), {
      key: 'Enter',
      ctrlKey: true,
    })

    expect(mockStreamQuery).toHaveBeenCalledTimes(1)
    release()
    await screen.findByRole('heading', { name: 'Answer' })
  })

  it('exposes the latest status through the aria-live region', async () => {
    let release!: () => void
    const gate = new Promise<void>((resolve) => {
      release = resolve
    })
    mockStreamQuery.mockImplementation(() =>
      (async function* generate() {
        yield { type: 'status', message: 'Preparing final answer' }
        await gate
        yield { type: 'done' }
      })(),
    )

    render(<App />)
    await ask('How many orders?')

    const liveRegion = document.querySelector('[aria-live="polite"]')
    await waitFor(() =>
      expect(liveRegion).toHaveTextContent('Preparing final answer'),
    )
    release()
    await screen.findByRole('heading', { name: 'Answer' })
  })
})
