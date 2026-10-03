import Markdown, { type Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'

// Renders the agent's final answer as GitHub-Flavored Markdown.
//
// react-markdown compiles Markdown into React elements, so no
// `dangerouslySetInnerHTML` is involved, and raw HTML in the source is ignored
// because no `rehype-raw` plugin is used. URLs keep the library's default
// sanitization. Images are disabled so untrusted model output cannot trigger
// external image fetches.
const components: Components = {
  // Scroll wide tables within the answer instead of letting them widen the page.
  table({ node: _node, ...props }) {
    return (
      <div className="table-scroll">
        <table {...props} />
      </div>
    )
  },
  img() {
    return null
  },
}

export default function MarkdownAnswer({ markdown }: { markdown: string }) {
  return (
    <div className="markdown">
      <Markdown remarkPlugins={[remarkGfm]} components={components}>
        {markdown}
      </Markdown>
    </div>
  )
}
