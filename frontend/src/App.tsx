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

const EXAMPLE_QUESTIONS = [
  'How many orders were delivered in 2018?',
  'Which product categories had the most orders?',
  'Which states had the most customers?',
  'Who were the top sellers by number of orders?',
  'How many customers placed more than one order?',
  'Which payment types were most common?',
]

// Generic user-facing text for transport/protocol failures; raw exception
// details are kept for developers (see the console diagnostic in runQuery).
const TRANSPORT_ERROR_MESSAGE =
  'Something went wrong while running the analysis. Please try again.'

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
  const textareaRef = useRef<HTMLTextAreaElement>(null)
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
      // Keep the raw detail for developers; the UI shows a generic message.
      if (import.meta.env.DEV) {
        console.error('Query transport error:', error)
      }
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

  function selectExample(example: string) {
    setQuestion(example)
    textareaRef.current?.focus()
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
          Ask in plain English. OlistIQ inspects the schema, runs read-only
          SQL, and answers from database results.
        </p>
      </header>

      <form className="query-form" onSubmit={handleSubmit}>
        <label htmlFor="question" style={visuallyHidden}>
          Your question
        </label>
        <textarea
          id="question"
          ref={textareaRef}
          rows={3}
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="e.g. Which states had the most customers?"
        />
        <div className="query-actions">
          <p className="query-hint">Ctrl/⌘ + Enter to run</p>
          <button type="submit" disabled={!canSubmit}>
            {isRunning ? 'Analyzing…' : 'Ask'}
          </button>
        </div>
      </form>

      <section className="examples" aria-labelledby="examples-title">
        <h2 id="examples-title" className="examples-title">
          Try an example
        </h2>
        <ul className="examples-list">
          {EXAMPLE_QUESTIONS.map((example) => (
            <li key={example}>
              <button
                type="button"
                className="example"
                disabled={isRunning}
                onClick={() => selectExample(example)}
              >
                {example}
              </button>
            </li>
          ))}
        </ul>
      </section>

      {query.phase === 'idle' && (
        <section className="dataset" aria-labelledby="dataset-title">
          <h2 id="dataset-title" className="dataset-title">
            Explore the Olist dataset
          </h2>
          <p className="dataset-copy">
            Olist is a Brazilian e-commerce dataset spanning 2016–2018,
            covering orders, customers, products, sellers, payments, reviews,
            and delivery activity — about 99k orders in total.
          </p>
          <dl className="dataset-domains">
            <div className="dataset-domain">
              <dt>Orders &amp; delivery</dt>
              <dd>status · purchase date · delivery dates</dd>
            </div>
            <div className="dataset-domain">
              <dt>Customers &amp; geography</dt>
              <dd>city · state · ZIP · customer identity</dd>
            </div>
            <div className="dataset-domain">
              <dt>Products</dt>
              <dd>category · weight · dimensions · photos</dd>
            </div>
            <div className="dataset-domain">
              <dt>Order items</dt>
              <dd>product · seller · price · freight</dd>
            </div>
            <div className="dataset-domain">
              <dt>Payments</dt>
              <dd>type · installments · value</dd>
            </div>
            <div className="dataset-domain">
              <dt>Reviews</dt>
              <dd>score · comments · review dates</dd>
            </div>
            <div className="dataset-domain">
              <dt>Sellers</dt>
              <dd>city · state · ZIP</dd>
            </div>
            <div className="dataset-domain">
              <dt>Geolocation</dt>
              <dd>ZIP · latitude · longitude</dd>
            </div>
          </dl>
        </section>
      )}

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
                : TRANSPORT_ERROR_MESSAGE}
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
