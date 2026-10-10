import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach, vi } from 'vitest'

// jsdom does not implement these browser APIs that App uses. Stub them so tests
// can exercise the real behavior: scrolling to the result section and reading
// the reduced-motion media query. Individual tests override `matchMedia`.
window.HTMLElement.prototype.scrollIntoView = vi.fn()
window.matchMedia = vi.fn(
  (query: string): MediaQueryList =>
    ({
      matches: false,
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }) as MediaQueryList,
)

// Testing Library's automatic cleanup relies on a global `afterEach`, which is
// not defined because this project uses explicit Vitest imports. Register it
// here so each component test starts from a clean DOM.
afterEach(() => {
  cleanup()
})
