import {
  useReducer,
  useRef,
  useState,
  type CSSProperties,
  type FormEvent,
  type KeyboardEvent,
} from 'react'
import { streamQuery, type QueryEvent } from './api'
import './App.css'

type Phase = 'idle' | 'running' | 'complete' | 'error'

interface QueryState {
  phase: Phase
  statuses: string[]
  answer: string
  error: { kind: 'application' | 'transport'; message: string } | null
}

type QueryAction =
  | { type: 'start' }
  | { type: 'status'; message: string }
  | { type: 'delta'; text: string }
  | { type: 'complete' }
  | { type: 'applicationError'; message: string }
  | { type: 'transportError'; message: string }

const initialQueryState: QueryState = {
  phase: 'idle',
  statuses: [],
  answer: '',
  error: null,
}

// Visually hidden, but still available to assistive technology.
const visuallyHidden: CSSProperties = {
  position: 'absolute',
  width: 1,
  height: 1,
  padding: 0,
  margin: -1,
  overflow: 'hidden',
  clip: 'rect(0, 0, 0, 0)',
  whiteSpace: 'nowrap',
  border: 0,
}

function queryReducer(state: QueryState, action: QueryAction): QueryState {
  switch (action.type) {
    case 'start':
      // A new submission always resets the previous run's progress and answer.
      return { phase: 'running', statuses: [], answer: '', error: null }
    case 'status':
      return { ...state, statuses: [...state.statuses, action.message] }
    case 'delta':
      return { ...state, answer: state.answer + action.text }
    case 'complete':
      return { ...state, phase: 'complete' }
    case 'applicationError':
      return {
        ...state,
        phase: 'error',
        error: { kind: 'application', message: action.message },
      }
    case 'transportError':
      // Keep any partial statuses/answer so the user sees how far it got.
      return {
        ...state,
        phase: 'error',
        error: { kind: 'transport', message: action.message },
      }
  }
}

function App() {
  const [question, setQuestion] = useState('')
  const [query, dispatch] = useReducer(queryReducer, initialQueryState)
  // Ref, not state: guards against a double submit within a single tick, before
  // the disabled button can re-render.
  const inFlight = useRef(false)

  const isRunning = query.phase === 'running'
  const canSubmit = question.trim() !== '' && !isRunning
  const showResult = query.error !== null || query.answer !== '' || isRunning

  function applyEvent(event: QueryEvent) {
    switch (event.type) {
      case 'status':
        dispatch({ type: 'status', message: event.message })
        break
      case 'answer_delta':
        dispatch({ type: 'delta', text: event.text })
        break
      case 'done':
        dispatch({ type: 'complete' })
        break
      case 'error':
        dispatch({ type: 'applicationError', message: event.message })
        break
    }
  }

  async function runQuery() {
    const trimmed = question.trim()
    // Local validation: never send an empty/whitespace-only question.
    if (trimmed === '' || inFlight.current) return
    inFlight.current = true
    dispatch({ type: 'start' })
    try {
      for await (const event of streamQuery(trimmed)) {
        applyEvent(event)
      }
    } catch (error) {
      dispatch({
        type: 'transportError',
        message: error instanceof Error ? error.message : String(error),
      })
    } finally {
      inFlight.current = false
    }
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    void runQuery()
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') {
      event.preventDefault()
      void runQuery()
    }
  }

  return (
    <main className="app">
      <header className="app-header">
        <h1>AI SQL Analyst</h1>
        <p>
          Ask natural-language questions about the Olist e-commerce dataset. The
          agent inspects the database schema and runs read-only SQL to answer.
        </p>
      </header>

      <form className="query-form" onSubmit={handleSubmit}>
        <label htmlFor="question">Your question</label>
        <textarea
          id="question"
          rows={3}
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="e.g. How many orders were placed in 2017?"
        />
        <p className="query-hint">
          Enter adds a new line. Press Ctrl+Enter (Cmd+Enter on Mac) to submit.
        </p>
        <button type="submit" disabled={!canSubmit}>
          {isRunning ? 'Analyzing…' : 'Ask'}
        </button>
      </form>

      {/* Only the latest status is announced, to avoid re-reading the whole
          log on every incremental update. The visible log below is not a live
          region. This element stays mounted so updates are announced. */}
      <p style={visuallyHidden} aria-live="polite">
        {query.statuses.at(-1) ?? ''}
      </p>

      {query.statuses.length > 0 && (
        <section className="run-progress" aria-label="Progress">
          <h2>Progress</h2>
          <ol>
            {query.statuses.map((status, index) => (
              <li key={index}>{status}</li>
            ))}
          </ol>
        </section>
      )}

      {showResult && (
        <section className="run-result">
          <h2>{query.error ? 'Error' : 'Answer'}</h2>
          {query.error ? (
            <div className="error-box" role="alert">
              {query.error.kind === 'application'
                ? query.error.message
                : `Could not reach the analysis service. ${query.error.message}`}
            </div>
          ) : (
            <p className="answer">
              {query.answer !== ''
                ? query.answer
                : isRunning
                  ? 'Preparing answer…'
                  : ''}
            </p>
          )}
        </section>
      )}
    </main>
  )
}

export default App
