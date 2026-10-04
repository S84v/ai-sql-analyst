import {
  useReducer,
  useRef,
  useState,
  type CSSProperties,
  type FormEvent,
  type KeyboardEvent,
} from 'react'
import { streamQuery, type QueryEvent } from './api'
import AnswerReveal from './AnswerReveal'
import './App.css'

type Phase = 'idle' | 'running' | 'complete' | 'error'

interface QueryState {
  phase: Phase
  status: string | null
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
  status: null,
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
      // A new submission always resets the previous run's status and answer.
      return { phase: 'running', status: null, answer: '', error: null }
    case 'status':
      // The latest backend status replaces the previous one.
      return { ...state, status: action.message }
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
      // Keep the current status and any partial answer so the user sees how
      // far it got.
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
      <header className="hero">
        {/* Decorative brand mark; the h1 names the product. */}
        <img
          className="brand-mark"
          src="/olistiq.svg"
          alt=""
          aria-hidden="true"
          width={46}
          height={46}
        />
        <h1 className="brand-name">OlistIQ</h1>
        <p className="brand-tagline">Ask questions about Olist</p>
        <p className="hero-sub">
          Ask in plain English. OlistIQ inspects the Olist dataset and runs
          read-only SQL to answer.
        </p>
      </header>

      <form className="query-form" onSubmit={handleSubmit}>
        <label htmlFor="question" style={visuallyHidden}>
          Your question
        </label>
        <textarea
          id="question"
          rows={3}
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="What would you like to know?"
        />
        <div className="query-actions">
          <p className="query-hint">Ctrl + Enter to run</p>
          <button type="submit" disabled={!canSubmit}>
            {isRunning ? 'Analyzing…' : 'Ask'}
          </button>
        </div>
      </form>

      <section className="dataset" aria-labelledby="dataset-title">
        <h2 id="dataset-title" className="dataset-title">
          Built for the Olist dataset
        </h2>
        <p className="dataset-copy">
          Brazilian e-commerce data spanning orders, customers, products,
          sellers, payments, reviews, and more.
        </p>
        <dl className="dataset-stats">
          <div className="dataset-stat">
            <dt>Orders</dt>
            <dd>~99k</dd>
          </div>
          <div className="dataset-stat">
            <dt>Products</dt>
            <dd>~32.9k</dd>
          </div>
          <div className="dataset-stat">
            <dt>Sellers</dt>
            <dd>~3.1k</dd>
          </div>
        </dl>
      </section>

      {/* Only the latest status is announced, to avoid re-reading a growing
          log. The visible status below is not a live region. This element stays
          mounted so updates are announced. */}
      <p style={visuallyHidden} aria-live="polite">
        {query.status ?? ''}
      </p>

      {query.status !== null && query.phase !== 'complete' && (
        <p className={`run-status${isRunning ? ' run-status-active' : ''}`}>
          <span className="run-status-mark" aria-hidden="true">
            ✦
          </span>
          <span className="run-status-text">{query.status}</span>
        </p>
      )}

      {showResult && (
        <section className="run-result">
          <h2>{query.error ? 'Error' : 'Answer'}</h2>
          {query.error && (
            <div className="error-box" role="alert">
              {query.error.kind === 'application'
                ? query.error.message
                : `Could not reach the analysis service. ${query.error.message}`}
            </div>
          )}
          {query.phase === 'complete' ? (
            <AnswerReveal markdown={query.answer} />
          ) : query.answer !== '' ? (
            // Partial answer (still running, or a transport/protocol failure):
            // show the raw text rather than feeding incomplete Markdown to the
            // parser.
            <p className="answer-partial">{query.answer}</p>
          ) : isRunning ? (
            <p className="answer-placeholder">Preparing answer…</p>
          ) : null}
        </section>
      )}
    </main>
  )
}

export default App
