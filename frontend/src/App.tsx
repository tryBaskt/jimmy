import { useEffect, useRef, useState, type FormEvent } from 'react'
import {
  getAuthState, getHealth, getStrategyCapabilities, runStrategy, signOut,
  type AuthState, type Health, type StrategyCapabilities, type StrategyResult,
} from './api/client'
import './App.css'

const examplePrompt = 'Find the strongest momentum stocks in the S&P 500 at each monthly rebalance. Select up to 10 stocks, hold them at equal weights for the next month, and compare the results with SPY after trading costs.'

const percent = (value: number) => `${value >= 0 ? '+' : ''}${(value * 100).toFixed(1)}%`
const signed = (value: number | null) => value === null ? '—' : value.toFixed(2)
const isoDate = (date: Date) => date.toISOString().slice(0, 10)
const defaultToday = isoDate(new Date())
const oneYearBefore = (value: string) => {
  const date = new Date(`${value}T00:00:00Z`)
  date.setUTCFullYear(date.getUTCFullYear() - 1)
  return isoDate(date)
}
const inclusiveEnd = (exclusive: string) => {
  const date = new Date(`${exclusive}T00:00:00Z`)
  date.setUTCDate(date.getUTCDate() - 1)
  return isoDate(date)
}

function PerformanceChart({ points }: { points: StrategyResult['performance_curve'] }) {
  if (points.length < 2) return null
  const width = 760
  const height = 238
  const padding = 12
  const values = points.flatMap((point) => [point.strategy, point.spy])
  const low = Math.min(0.95, ...values)
  const high = Math.max(1.05, ...values)
  const range = high - low || 1
  const line = (key: 'strategy' | 'spy') => points.map((point, index) => {
    const x = padding + index * (width - 2 * padding) / (points.length - 1)
    const y = height - padding - (point[key] - low) * (height - 2 * padding) / range
    return `${x.toFixed(1)},${y.toFixed(1)}`
  }).join(' ')
  const baseline = height - padding - (1 - low) * (height - 2 * padding) / range

  return (
    <div className="chart-wrap">
      <div className="chart-legend"><span><i className="legend-strategy" />Selected strategy</span><span><i className="legend-spy" />SPY</span></div>
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Growth of one dollar over the backtest, strategy compared with SPY" preserveAspectRatio="none">
        <line x1="0" x2={width} y1={baseline} y2={baseline} className="chart-baseline" />
        <polyline points={line('spy')} className="chart-spy" />
        <polyline points={line('strategy')} className="chart-strategy" />
      </svg>
      <div className="chart-axis"><span>{points[0].date}</span><span>{points[points.length - 1].date}</span></div>
    </div>
  )
}

function Results({ result }: { result: StrategyResult }) {
  const latest = result.selections.at(-1)
  const samples = result.plan.selection_steps?.some((step) => step.operation === 'sample')
  const usesPrices = !samples || result.plan.selection_steps.length > 1

  return (
    <section className="results" aria-labelledby="results-title">
      <div className="section-heading">
        <div><p className="eyebrow">RESEARCH REPORT</p><h2 id="results-title">Strategy, tested.</h2></div>
        <span className="result-tag">{result.plan.universe} · {result.plan.rebalance} · {result.plan.weighting === 'equal' ? 'equal weight' : result.plan.weighting === 'market_cap' ? 'market-cap weight' : 'model weight'}</span>
      </div>
      <p className="results-lede">{result.plan.hypothesis} At each {result.plan.rebalance} rebalance, the saved selection plan chose stocks using information available at the prior close. The recorded decisions were replayed from {result.start} to {inclusiveEnd(result.end_exclusive)}{result.selected_sectors.length ? ` within ${result.selected_sectors.join(', ')}` : ' across all sectors'}.</p>

      <div className="metrics-grid">
        <article className="metric primary-metric"><span>Strategy return</span><strong>{percent(result.strategy.total_return)}</strong><small>Full backtest</small></article>
        <article className="metric"><span>SPY return</span><strong>{percent(result.spy.total_return)}</strong><small>Same period</small></article>
        <article className="metric"><span>Max drawdown</span><strong>{percent(result.strategy.max_drawdown)}</strong><small>Strategy</small></article>
        <article className="metric"><span>Sharpe ratio</span><strong>{signed(result.strategy.sharpe)}</strong><small>Strategy</small></article>
      </div>

      <div className="report-panel">
        <div className="panel-heading"><div><h3>Backtest growth</h3><p>Growth of $1 over the backtest period</p></div><span>{result.start} – {inclusiveEnd(result.end_exclusive)}</span></div>
        <PerformanceChart points={result.performance_curve} />
      </div>

      <div className="report-grid">
        <section className="report-panel" aria-labelledby="process-title">
          <div className="panel-heading"><div><h3 id="process-title">How each rebalance works</h3><p>One fixed plan, repeated throughout the backtest</p></div></div>
          <ol className="process-list">
            <li><strong>{usesPrices ? `Measure the previous ${result.plan.lookback_months} months` : 'Start with the eligible universe'}</strong><span>{usesPrices ? 'Use prices available at the prior close to apply the requested rule.' : 'A random sample uses the full eligible universe; the lookback does not affect the draw.'}</span></li>
            <li><strong>Choose up to {result.plan.top_n} stocks</strong><span>{samples ? 'Draw a repeatable random sample' : 'Apply the selection plan'}; {result.plan.weighting === 'market_cap' ? 'historical market caps set the weights' : result.plan.weighting === 'equal' ? 'weights are equal' : 'the agent suggests weights'}.</span></li>
            <li><strong>Replay the portfolio</strong><span>Trade at the first trading-day close, then hold until the next {result.plan.rebalance} rebalance.</span></li>
          </ol>
        </section>
        <section className="report-panel holdings-panel" aria-labelledby="holdings-title">
          <div className="panel-heading"><div><h3 id="holdings-title">Latest selection</h3><p>{latest ? `Rebalanced ${latest.rebalance_date} · signal ${latest.signal_date}` : 'No selections returned'}</p></div></div>
          <div className="ticker-list">{latest?.positions.map((position) => <span key={position.symbol} title={position.reason}>{position.symbol} · {position.direction} · {(position.weight * 100).toFixed(1)}%</span>)}</div>
          <details><summary>View all {result.selections.length} selections</summary>
            <div className="selection-history">{result.selections.map((selection) => <div key={selection.rebalance_date}><time>{selection.rebalance_date}</time><span>{selection.positions.map((position) => `${position.symbol} ${position.direction} ${(position.weight * 100).toFixed(1)}%`).join(' · ')}</span></div>)}</div>
          </details>
        </section>
      </div>

      <div className="report-note"><strong>How to read this result</strong><p>{result.eligible_symbol_count} of {result.universe_symbol_count} filtered universe symbols had complete price history. Jimmy fetched prices from {result.lookback_data_start} to support the {result.plan.lookback_months}-month signal, while performance covers only the selected dates. Costs are {result.plan.fees_bps} bps in fees and {result.plan.slippage_bps} bps in slippage per order.</p><p>{result.limitations[0]}</p>{result.excluded_symbols.length > 0 && <p>Excluded for missing prices: {result.excluded_symbols.join(', ')}.</p>}</div>
    </section>
  )
}

export default function App() {
  const [health, setHealth] = useState<Health | null>(null)
  const [auth, setAuth] = useState<AuthState | null>(null)
  const [capabilities, setCapabilities] = useState<StrategyCapabilities | null>(null)
  const [connectionError, setConnectionError] = useState('')
  const [prompt, setPrompt] = useState(examplePrompt)
  const [lookbackInput, setLookbackInput] = useState('3')
  const [startDate, setStartDate] = useState(oneYearBefore(defaultToday))
  const [endDate, setEndDate] = useState(defaultToday)
  const [selectedSectors, setSelectedSectors] = useState<string[]>([])
  const [result, setResult] = useState<StrategyResult | null>(null)
  const [runError, setRunError] = useState('')
  const [running, setRunning] = useState(false)
  const runController = useRef<AbortController | null>(null)
  const lookbackMonths = Number(lookbackInput)
  const validLookback = Number.isInteger(lookbackMonths) && lookbackMonths >= 1 && lookbackMonths <= 12
  const durationDays = (Date.parse(`${endDate}T00:00:00Z`) - Date.parse(`${startDate}T00:00:00Z`)) / 86400000
  const validDates = Boolean(startDate && endDate) && durationDays >= 90 &&
    (!capabilities || (startDate >= capabilities.earliest_start_date && endDate <= capabilities.latest_end_date))

  useEffect(() => {
    const controller = new AbortController()
    Promise.all([getHealth(controller.signal), getStrategyCapabilities(controller.signal), getAuthState(controller.signal)])
      .then(([service, strategyCapabilities, session]) => {
        if (!controller.signal.aborted) {
          setHealth(service); setCapabilities(strategyCapabilities); setAuth(session)
          setEndDate(strategyCapabilities.latest_end_date)
          setStartDate(oneYearBefore(strategyCapabilities.latest_end_date))
        }
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) {
          setConnectionError(reason instanceof Error ? reason.message : 'Connection failed')
          setCapabilities(null)
        }
      })
    return () => { controller.abort(); runController.current?.abort() }
  }, [])

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!capabilities?.enabled || auth?.mode !== 'signed_in' || running || !prompt.trim() || !validLookback || !validDates) return
    const controller = new AbortController()
    runController.current = controller
    setRunning(true)
    setRunError('')
    setResult(null)
    try {
      setResult(await runStrategy(prompt.trim(), lookbackMonths, selectedSectors, startDate, endDate, controller.signal))
    } catch (reason) {
      if (!controller.signal.aborted) setRunError(reason instanceof Error ? reason.message : 'Research failed')
    } finally {
      runController.current = null
      setRunning(false)
    }
  }

  return (
    <main>
      <header className="site-header"><div className="brand"><span className="brand-mark">J</span><span>jimmy<span className="brand-dot">.</span></span></div><div className="header-right"><span className="header-label">Strategy testing</span>{auth?.mode === 'signed_out' && <><a href="/api/auth/login">Sign in</a><a href="/api/auth/signup">Sign up</a></>}{auth?.mode === 'signed_in' && <><span>{auth.email}</span><button type="button" onClick={() => { void signOut().then(() => setAuth({ mode: 'signed_out' })).catch((error: unknown) => setRunError(error instanceof Error ? error.message : 'Could not sign out')) }}>Sign out</button></>}<span className={`status-pill ${health ? 'online' : ''}`}><i />{health ? 'API connected' : connectionError ? 'API unavailable' : 'Connecting'}</span></div></header>
      <section className="intro"><p className="eyebrow">STRATEGY TEST / 01</p><h1>From an idea to<br /><em>evidence.</em></h1><p>State a specific price-based trading hypothesis, choose the sectors, and set the test dates. Jimmy will make dated portfolio decisions and replay them over history.</p></section>

      <section className="prompt-panel" aria-labelledby="prompt-title">
        <div className="prompt-heading"><div><p className="eyebrow">YOUR HYPOTHESIS</p><h2 id="prompt-title">What would you like to test?</h2></div><span className="prompt-scope">S&P 500 · monthly or quarterly</span></div>
        <form onSubmit={submit}>
          <label className="sr-only" htmlFor="strategy-prompt">Trading strategy prompt</label>
          <textarea id="strategy-prompt" value={prompt} onChange={(event) => setPrompt(event.target.value)} maxLength={4000} rows={5} placeholder="Describe your trading strategy…" />
          <div className="lookback-control"><label htmlFor="lookback-months">Look back <input id="lookback-months" type="number" min="1" max="12" step="1" required value={lookbackInput} onChange={(event) => setLookbackInput(event.target.value)} /> months</label><p>Use the previous {validLookback ? lookbackMonths : 'selected'} months of prices to apply your rule at each rebalance.</p></div>
          <div className="filter-grid">
            <fieldset className="sector-control"><legend>Include sectors</legend>
              <label className="sector-option"><input type="checkbox" checked={selectedSectors.length === 0} onChange={() => setSelectedSectors([])} />All sectors</label>
              {capabilities?.sectors.map((sector) => <label className="sector-option" key={sector}><input type="checkbox" checked={selectedSectors.includes(sector)} onChange={() => setSelectedSectors((current) => current.includes(sector) ? current.filter((value) => value !== sector) : [...current, sector])} />{sector}</label>)}
            </fieldset>
            <fieldset className="date-control"><legend>Backtest dates</legend><div>
              <label htmlFor="start-date">Start<input id="start-date" type="date" required min={capabilities?.earliest_start_date} max={endDate} value={startDate} onChange={(event) => setStartDate(event.target.value)} /></label>
              <label htmlFor="end-date">End<input id="end-date" type="date" required min={startDate} max={capabilities?.latest_end_date} value={endDate} onChange={(event) => setEndDate(event.target.value)} /></label>
            </div><p>Performance spans these dates. Lookback prices are fetched from before the start date.</p>{!validDates && <p className="field-error">Choose a range of at least 90 days.</p>}</fieldset>
          </div>
          <div className="prompt-actions"><span>{auth?.mode === 'signed_out' ? 'Sign in to run a strategy.' : capabilities === null ? 'Checking strategy testing availability…' : capabilities.enabled ? 'Uses the latest stored S&P 500 and GICS snapshots with historical adjusted prices.' : 'Strategy testing is unavailable.'}</span><button type="submit" disabled={!capabilities?.enabled || auth?.mode !== 'signed_in' || running || !prompt.trim() || !validLookback || !validDates}>{running ? 'Testing…' : 'Run strategy'}<span aria-hidden="true">→</span></button></div>
        </form>
        {runError && <p className="error-message" role="alert">{runError}</p>}
        {connectionError && <p className="connection-hint">{connectionError}</p>}
      </section>

      {running && <div className="progress" role="status"><span className="progress-spinner" /><div><strong>Testing your strategy</strong><p>Planning with Bedrock, fetching prices, recording each rebalance decision, then replaying with VectorBT. This may take several minutes.</p></div></div>}
      {result && <Results result={result} />}
      <footer><span>JIMMY / STRATEGY RESEARCH</span><span>Research results are exploratory, not investment advice.</span></footer>
    </main>
  )
}
