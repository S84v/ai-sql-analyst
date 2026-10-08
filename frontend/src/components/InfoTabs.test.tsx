import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import InfoTabs from './InfoTabs'

function clickTab(name: string) {
  fireEvent.click(screen.getByRole('tab', { name }))
}

describe('InfoTabs', () => {
  it('selects no tab and renders no panel initially', () => {
    render(<InfoTabs />)

    const tabs = screen.getAllByRole('tab')
    expect(tabs).toHaveLength(3)
    for (const tab of tabs) {
      expect(tab).toHaveAttribute('aria-selected', 'false')
    }
    expect(screen.queryByRole('tabpanel')).not.toBeInTheDocument()
  })

  it('starts with the roving tabindex on the first tab only', () => {
    render(<InfoTabs />)

    const tabs = screen.getAllByRole('tab')
    expect(tabs[0]).toHaveAttribute('tabindex', '0')
    expect(tabs[1]).toHaveAttribute('tabindex', '-1')
    expect(tabs[2]).toHaveAttribute('tabindex', '-1')
  })

  it('keeps only the active tab in the tab order', () => {
    render(<InfoTabs />)

    clickTab('Project')

    const tabs = screen.getAllByRole('tab')
    expect(tabs.map((tab) => tab.getAttribute('tabindex'))).toEqual([
      '-1',
      '0',
      '-1',
    ])
  })

  it('remounts the panel when the active tab changes', () => {
    render(<InfoTabs />)

    clickTab('Data')
    const first = screen.getByRole('tabpanel')
    clickTab('Project')
    const second = screen.getByRole('tabpanel')
    expect(second).not.toBe(first)
  })

  it('shows the Data content when Data is selected', () => {
    render(<InfoTabs />)

    clickTab('Data')

    expect(screen.getByRole('tab', { name: 'Data' })).toHaveAttribute(
      'aria-selected',
      'true',
    )
    const panel = screen.getByRole('tabpanel')
    expect(within(panel).getByText(/roughly 99,000 orders/i)).toBeInTheDocument()
    expect(within(panel).getByText('Orders & delivery')).toBeInTheDocument()
  })

  it('switches the panel to Project content', () => {
    render(<InfoTabs />)

    clickTab('Project')

    const panel = screen.getByRole('tabpanel')
    expect(within(panel).getByText('Ask a question')).toBeInTheDocument()
    expect(within(panel).getByText('Grounded')).toBeInTheDocument()
    expect(within(panel).queryByText(/roughly 99,000 orders/i)).not.toBeInTheDocument()
  })

  it('switches the panel to Contact content', () => {
    render(<InfoTabs />)

    clickTab('Contact')

    const panel = screen.getByRole('tabpanel')
    expect(within(panel).getByRole('link', { name: 'GitHub' })).toBeInTheDocument()
  })

  it('exposes aria-selected on exactly the active tab', () => {
    render(<InfoTabs />)

    clickTab('Project')

    expect(screen.getByRole('tab', { name: 'Data' })).toHaveAttribute(
      'aria-selected',
      'false',
    )
    expect(screen.getByRole('tab', { name: 'Project' })).toHaveAttribute(
      'aria-selected',
      'true',
    )
    expect(screen.getByRole('tab', { name: 'Contact' })).toHaveAttribute(
      'aria-selected',
      'false',
    )
  })

  it('relates the active tab and its panel', () => {
    render(<InfoTabs />)

    clickTab('Contact')

    const tab = screen.getByRole('tab', { name: 'Contact' })
    const panel = screen.getByRole('tabpanel')
    expect(tab).toHaveAttribute('aria-controls', panel.id)
    expect(panel).toHaveAttribute('aria-labelledby', tab.id)
  })

  it('renders the contact links with their destinations', () => {
    render(<InfoTabs />)

    clickTab('Contact')

    const github = screen.getByRole('link', { name: 'GitHub' })
    expect(github).toHaveAttribute('href', 'https://github.com/S84v/ai-sql-analyst')
    expect(github).toHaveAttribute('target', '_blank')
    expect(github).toHaveAttribute('rel', 'noreferrer')

    const linkedin = screen.getByRole('link', { name: 'LinkedIn' })
    expect(linkedin).toHaveAttribute(
      'href',
      'https://www.linkedin.com/in/sarang-dave/',
    )
    expect(linkedin).toHaveAttribute('target', '_blank')
    expect(linkedin).toHaveAttribute('rel', 'noreferrer')

    const email = screen.getByRole('link', { name: 'Email' })
    expect(email).toHaveAttribute('href', 'mailto:davesarang08@gmail.com')
    expect(email).not.toHaveAttribute('target')
  })

  it('moves and activates tabs with the keyboard', () => {
    render(<InfoTabs />)
    const tabs = screen.getAllByRole('tab')

    tabs[0].focus()
    fireEvent.keyDown(tabs[0], { key: 'ArrowRight' })
    expect(tabs[1]).toHaveFocus()
    expect(tabs[1]).toHaveAttribute('aria-selected', 'true')

    fireEvent.keyDown(tabs[1], { key: 'ArrowRight' })
    expect(tabs[2]).toHaveFocus()

    // ArrowRight wraps around from the last tab.
    fireEvent.keyDown(tabs[2], { key: 'ArrowRight' })
    expect(tabs[0]).toHaveFocus()

    // ArrowLeft wraps backward from the first tab.
    fireEvent.keyDown(tabs[0], { key: 'ArrowLeft' })
    expect(tabs[2]).toHaveFocus()

    fireEvent.keyDown(tabs[2], { key: 'Home' })
    expect(tabs[0]).toHaveFocus()

    fireEvent.keyDown(tabs[0], { key: 'End' })
    expect(tabs[2]).toHaveFocus()
    expect(tabs[2]).toHaveAttribute('aria-selected', 'true')
  })
})
