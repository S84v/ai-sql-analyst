import { useId, useRef, useState, type KeyboardEvent } from 'react'
import './InfoTabs.css'

type TabId = 'data' | 'project' | 'contact'

interface TabDefinition {
  id: TabId
  label: string
}

const TABS: TabDefinition[] = [
  { id: 'data', label: 'Data' },
  { id: 'project', label: 'Project' },
  { id: 'contact', label: 'Contact' },
]

const GITHUB_URL = 'https://github.com/S84v/ai-sql-analyst'
const LINKEDIN_URL = 'https://www.linkedin.com/in/sarang-dave/'
const CONTACT_EMAIL = 'davesarang08@gmail.com'

// Prefilled draft for the email link. The body deliberately ends with a
// trailing space after "because" so the visitor can finish the sentence.
const EMAIL_SUBJECT = 'About OlistIQ'
const EMAIL_BODY = 'Hi,\n\nI wanted to reach out about OlistIQ because '
const EMAIL_URL = `mailto:${CONTACT_EMAIL}?subject=${encodeURIComponent(
  EMAIL_SUBJECT,
)}&body=${encodeURIComponent(EMAIL_BODY)}`

function DataPanel() {
  return (
    <>
      <p className="info-copy">
        OlistIQ answers questions about a real Brazilian e-commerce dataset:
        roughly 99,000 orders placed between 2016 and 2018. It follows each order
        from purchase to delivery, and also covers the customers, products,
        sellers, payments, and reviews around it.
      </p>
      <dl className="info-domains">
        <div className="info-domain">
          <dt>Orders &amp; delivery</dt>
          <dd>status · purchase date · delivery dates</dd>
        </div>
        <div className="info-domain">
          <dt>Customers &amp; geography</dt>
          <dd>city · state · ZIP · customer identity</dd>
        </div>
        <div className="info-domain">
          <dt>Products</dt>
          <dd>category · weight · dimensions · photos</dd>
        </div>
        <div className="info-domain">
          <dt>Order items</dt>
          <dd>product · seller · price · freight</dd>
        </div>
        <div className="info-domain">
          <dt>Payments</dt>
          <dd>type · installments · value</dd>
        </div>
        <div className="info-domain">
          <dt>Reviews</dt>
          <dd>score · comments · review dates</dd>
        </div>
        <div className="info-domain">
          <dt>Sellers</dt>
          <dd>city · state · ZIP</dd>
        </div>
        <div className="info-domain">
          <dt>Geolocation</dt>
          <dd>ZIP · latitude · longitude</dd>
        </div>
      </dl>
    </>
  )
}

function ProjectPanel() {
  return (
    <>
      <p className="info-copy">
        OlistIQ lets you ask questions about the Olist dataset in plain English.
        It works out which data is needed, runs a read-only analysis, and answers
        from the results it finds — not from the model's imagination. You can
        follow the analysis as it runs.
      </p>
      <ol className="info-flow">
        <li>Ask a question</li>
        <li>Understand the data</li>
        <li>Find the evidence</li>
        <li>Answer from the results</li>
      </ol>
      <ul className="info-principles">
        <li>
          <strong>Grounded</strong> — every answer comes from real query results.
        </li>
        <li>
          <strong>Read-only</strong> — the dataset is only ever read, never
          changed.
        </li>
        <li>
          <strong>Transparent</strong> — you can see the analysis as it happens.
        </li>
      </ul>
    </>
  )
}

function ContactPanel() {
  return (
    <>
      <p className="info-copy">
        Questions, feedback, or just want to connect? Reach out.
      </p>
      <ul className="info-links">
        <li>
          <a href={GITHUB_URL} target="_blank" rel="noreferrer">
            GitHub
            <span className="info-link-mark" aria-hidden="true">
              ↗
            </span>
          </a>
        </li>
        <li>
          <a href={LINKEDIN_URL} target="_blank" rel="noreferrer">
            LinkedIn
            <span className="info-link-mark" aria-hidden="true">
              ↗
            </span>
          </a>
        </li>
        <li>
          <a href={EMAIL_URL}>Email</a>
        </li>
      </ul>
    </>
  )
}

function InfoTabs() {
  // No tab is selected initially, so the information area stays optional.
  const [activeTab, setActiveTab] = useState<TabId | null>(null)
  const baseId = useId()
  const tabRefs = useRef<Array<HTMLButtonElement | null>>([])

  function activateTab(index: number) {
    const tab = TABS[index]
    if (!tab) return
    setActiveTab(tab.id)
    tabRefs.current[index]?.focus()
  }

  function handleKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    const currentIndex = tabRefs.current.findIndex(
      (node) => node === document.activeElement,
    )
    if (currentIndex === -1) return

    let nextIndex: number | null = null
    switch (event.key) {
      case 'ArrowRight':
        nextIndex = (currentIndex + 1) % TABS.length
        break
      case 'ArrowLeft':
        nextIndex = (currentIndex - 1 + TABS.length) % TABS.length
        break
      case 'Home':
        nextIndex = 0
        break
      case 'End':
        nextIndex = TABS.length - 1
        break
      default:
        return
    }
    event.preventDefault()
    activateTab(nextIndex)
  }

  return (
    <section className="info-tabs">
      <div
        className="info-tablist"
        role="tablist"
        aria-label="Project information"
        onKeyDown={handleKeyDown}
      >
        {TABS.map((tab, index) => {
          const selected = activeTab === tab.id
          return (
            <button
              key={tab.id}
              ref={(node) => {
                tabRefs.current[index] = node
              }}
              type="button"
              role="tab"
              id={`${baseId}-tab-${tab.id}`}
              aria-selected={selected}
              aria-controls={`${baseId}-panel-${tab.id}`}
              tabIndex={
                selected || (activeTab === null && index === 0) ? 0 : -1
              }
              className={`info-tab${selected ? ' info-tab-active' : ''}`}
              onClick={() => setActiveTab(tab.id)}
            >
              {tab.label}
            </button>
          )
        })}
      </div>

      {activeTab !== null && (
        <div
          // Remount on tab change so the existing panel animation replays for
          // every switch, not only the first open.
          key={activeTab}
          className="info-panel"
          role="tabpanel"
          id={`${baseId}-panel-${activeTab}`}
          aria-labelledby={`${baseId}-tab-${activeTab}`}
          tabIndex={0}
        >
          {activeTab === 'data' && <DataPanel />}
          {activeTab === 'project' && <ProjectPanel />}
          {activeTab === 'contact' && <ContactPanel />}
        </div>
      )}
    </section>
  )
}

export default InfoTabs
