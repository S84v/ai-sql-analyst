import { useLayoutEffect, useRef } from 'react'
import MarkdownAnswer from './MarkdownAnswer'

// Reveals the already-rendered answer: react-markdown parses the complete
// Markdown once, and this component only staggers a CSS fade over the resulting
// top-level blocks. Incomplete Markdown is never parsed or animated.
const STEP_MS = 70
const MAX_STEPS = 12

export default function AnswerReveal({ markdown }: { markdown: string }) {
  const containerRef = useRef<HTMLDivElement>(null)

  useLayoutEffect(() => {
    // `MarkdownAnswer` renders a single `.markdown` wrapper whose direct
    // children are the top-level blocks (paragraphs, headings, lists, and the
    // single-block `.table-scroll` / `pre` wrappers).
    const markdownElement = containerRef.current?.querySelector('.markdown')
    if (!markdownElement) return
    Array.from(markdownElement.children).forEach((block, index) => {
      const delay = Math.min(index, MAX_STEPS) * STEP_MS
      const element = block as HTMLElement
      element.style.setProperty('--reveal-delay', `${delay}ms`)
    })
  }, [markdown])

  return (
    <div className="answer-reveal" ref={containerRef}>
      <MarkdownAnswer markdown={markdown} />
    </div>
  )
}
