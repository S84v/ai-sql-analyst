import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'

// Testing Library's automatic cleanup relies on a global `afterEach`, which is
// not defined because this project uses explicit Vitest imports. Register it
// here so each component test starts from a clean DOM.
afterEach(() => {
  cleanup()
})
