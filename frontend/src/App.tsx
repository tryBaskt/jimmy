import { useEffect, useRef, useState, type FormEvent } from 'react'
import {
  getHealth, getResearchCapabilities, runResearch,
  type Health, type ResearchResult,
} from './api/client'
import './App.css'

const examplePrompt = 'Find the strongest momentum stocks in the S&P 500 at each monthly rebalance. Select up to 10 stocks, hold them at equal weights for the next month, and compare the results with SPY after trading costs.'

const percent = (value: number) => `${value >= 0 ? '+' : ''}${(value * 100).toFixed(1)}%`
const signed = (value: number | null) => value === null ? '—' : value.toFixed(2)

function PerformanceChart({ points }: { points: ResearchResult['performance_curve'] }) {
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

function Results({ result }: { result: ResearchResult }) {
  const latest = result.selections.at(-1)

  return (
    <section className="results" aria-labelledby="results-title">
      <div className="section-heading">
        <div><p className="eyebrow">RESEARCH REPORT</p><h2 id="results-title">Strategy, tested.</h2></div>
        <span className="result-tag">{result.plan.universe} · monthly · {result.plan.weighting === 'equal' ? 'equal weight' : result.plan.weighting}</span>
      </div>
      <p className="results-lede">At each monthly rebalance, the strategy ranked stocks by {result.ranking_rule} and held its selections for the next month. The figures cover the backtest from {result.start} to {result.end_exclusive}.</p>

      <div className="metrics-grid">
        <article className="metric primary-metric"><span>Strategy return</span><strong>{percent(result.strategy.total_return)}</strong><small>Full backtest</small></article>
        <article className="metric"><span>SPY return</span><strong>{percent(result.spy.total_return)}</strong><small>Same period</small></article>
        <article className="metric"><span>Max drawdown</span><strong>{percent(result.strategy.max_drawdown)}</strong><small>Strategy</small></article>
        <article className="metric"><span>Sharpe ratio</span><strong>{signed(result.strategy.sharpe)}</strong><small>Strategy</small></article>
      </div>

      <div className="report-panel">
        <div className="panel-heading"><div><h3>Backtest growth</h3><p>Growth of $1 over the backtest period</p></div><span>{result.start} – {result.end_exclusive}</span></div>
        <PerformanceChart points={result.performance_curve} />
      </div>

      <div className="report-grid">
        <section className="report-panel" aria-labelledby="process-title">
          <div className="panel-heading"><div><h3 id="process-title">How each month works</h3><p>One chosen lookback, repeated throughout the backtest</p></div></div>
          <ol className="process-list">
            <li><strong>Measure the previous {result.plan.lookback_months} months</strong><span>Use prices ending at the previous month’s final close to calculate the requested signal.</span></li>
            <li><strong>Choose up to {result.plan.top_n} stocks</strong><span>Rank by {result.ranking_rule} and assign {result.plan.weighting === 'equal' ? 'equal weights' : result.plan.weighting}.</span></li>
            <li><strong>Hold through the next month</strong><span>Rebalance at the first trading-day close, then repeat.</span></li>
          </ol>
        </section>
        <section className="report-panel holdings-panel" aria-labelledby="holdings-title">
          <div className="panel-heading"><div><h3 id="holdings-title">Latest selection</h3><p>{latest ? `Rebalanced ${latest.rebalance_date} · signal ${latest.signal_date}` : 'No selections returned'}</p></div></div>
          <div className="ticker-list">{latest?.symbols.map((symbol) => <span key={symbol}>{symbol}</span>)}</div>
          <details><summary>View all {result.selections.length} monthly selections</summary>
            <div className="selection-history">{result.selections.map((selection) => <div key={selection.rebalance_date}><time>{selection.rebalance_date}</time><span>{selection.symbols.join(' · ')}</span></div>)}</div>
          </details>
        </section>
      </div>

      <div className="report-note"><strong>How to read this result</strong><p>{result.eligible_symbol_count} of {result.universe_symbol_count} current universe symbols had complete price history. Costs are {result.plan.fees_bps} bps in fees and {result.plan.slippage_bps} bps in slippage per order.</p><p>{result.limitations[0]}</p>{result.excluded_symbols.length > 0 && <p>Excluded for missing prices: {result.excluded_symbols.join(', ')}.</p>}</div>
    </section>
  )
}

export default function App() {
  const [health, setHealth] = useState<Health | null>(null)
  const [available, setAvailable] = useState<boolean | null>(null)
  const [connectionError, setConnectionError] = useState('')
  const [prompt, setPrompt] = useState(examplePrompt)
  const [lookbackInput, setLookbackInput] = useState('3')
  const [result, setResult] = useState<ResearchResult | null>(null)
  const [runError, setRunError] = useState('')
  const [running, setRunning] = useState(false)
  const runController = useRef<AbortController | null>(null)
  const lookbackMonths = Number(lookbackInput)
  const validLookback = Number.isInteger(lookbackMonths) && lookbackMonths >= 1 && lookbackMonths <= 12

  useEffect(() => {
    const controller = new AbortController()
    Promise.all([getHealth(controller.signal), getResearchCapabilities(controller.signal)])
      .then(([service, enabled]) => { if (!controller.signal.aborted) { setHealth(service); setAvailable(enabled) } })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) {
          setConnectionError(reason instanceof Error ? reason.message : 'Connection failed')
          setAvailable(false)
        }
      })
    return () => { controller.abort(); runController.current?.abort() }
  }, [])

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!available || running || !prompt.trim() || !validLookback) return
    const controller = new AbortController()
    runController.current = controller
    setRunning(true)
    setRunError('')
    setResult(null)
    try {
      setResult(await runResearch(prompt.trim(), lookbackMonths, controller.signal))
    } catch (reason) {
      if (!controller.signal.aborted) setRunError(reason instanceof Error ? reason.message : 'Research failed')
    } finally {
      runController.current = null
      setRunning(false)
    }
  }

  return (
    <main>
      <header className="site-header"><div className="brand"><span className="brand-mark">J</span><span>jimmy<span className="brand-dot">.</span></span></div><div className="header-right"><span className="header-label">Strategy research</span><span className={`status-pill ${health ? 'online' : ''}`}><i />{health ? 'API connected' : connectionError ? 'API unavailable' : 'Connecting'}</span></div></header>
      <section className="intro"><p className="eyebrow">RESEARCH WORKSPACE / 01</p><h1>From an idea to<br /><em>evidence.</em></h1><p>Describe a price-based trading idea and choose how many past months to use. Jimmy will select stocks at each monthly rebalance and show how the strategy performed over history.</p></section>

      <section className="prompt-panel" aria-labelledby="prompt-title">
        <div className="prompt-heading"><div><p className="eyebrow">YOUR IDEA</p><h2 id="prompt-title">What would you like to test?</h2></div><span className="prompt-scope">S&P 500 · monthly</span></div>
        <form onSubmit={submit}>
          <label className="sr-only" htmlFor="strategy-prompt">Trading strategy prompt</label>
          <textarea id="strategy-prompt" value={prompt} onChange={(event) => setPrompt(event.target.value)} maxLength={4000} rows={5} placeholder="Describe your trading strategy…" />
          <div className="lookback-control"><label htmlFor="lookback-months">Look back <input id="lookback-months" type="number" min="1" max="12" step="1" required value={lookbackInput} onChange={(event) => setLookbackInput(event.target.value)} /> months</label><p>Use the previous {validLookback ? lookbackMonths : 'selected'} months of prices to apply your rule and choose stocks for the next month.</p></div>
          <div className="prompt-actions"><span>{available === null ? 'Checking research availability…' : available ? 'Uses current S&P 500 membership and historical adjusted prices.' : 'Research runs locally. Start the app with AWS credentials to enable it.'}</span><button type="submit" disabled={!available || running || !prompt.trim() || !validLookback}>{running ? 'Researching…' : 'Run research'}<span aria-hidden="true">→</span></button></div>
        </form>
        {runError && <p className="error-message" role="alert">{runError}</p>}
        {connectionError && <p className="connection-hint">{connectionError}</p>}
      </section>

      {running && <div className="progress" role="status"><span className="progress-spinner" /><div><strong>Running your research</strong><p>Planning with Bedrock, fetching prices, then repeating the monthly backtest. This may take a minute.</p></div></div>}
      {result && <Results result={result} />}
      <footer><span>JIMMY / STRATEGY RESEARCH</span><span>Research results are exploratory, not investment advice.</span></footer>
    </main>
  )
}
