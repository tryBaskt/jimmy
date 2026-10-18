import { useEffect, useState } from 'react'
import { getHealth, type Health } from './api/client'
import './App.css'

export default function App() {
  const [health, setHealth] = useState<Health | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    const controller = new AbortController()
    getHealth(controller.signal)
      .then((result) => { if (!controller.signal.aborted) setHealth(result) })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) {
          setError(reason instanceof Error ? reason.message : 'Connection failed')
        }
      })
      .finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [attempt])

  return (
    <main>
      <header><span className="brand">Jimmy</span><span>Research workspace</span></header>
      <section className="intro">
        <p className="eyebrow">QUANTITATIVE RESEARCH</p>
        <h1>A foundation for<br />your next idea.</h1>
        <p>A workspace for exploring stock universes and researching investment strategies.</p>
      </section>
      <section className="connection" aria-labelledby="connection-title">
        <div>
          <h2 id="connection-title">Backend status</h2>
          <p role="status" className={health ? 'connected' : ''}>
            {loading ? 'Checking connection…' : health ? `Connected to ${health.service}` : 'Backend unavailable'}
          </p>
          {error && <p className="hint">{error}. Check that the backend is running, then retry.</p>}
        </div>
        <button disabled={loading} onClick={() => { setLoading(true); setError(''); setHealth(null); setAttempt((value) => value + 1) }}>Check connection</button>
      </section>
      <footer>Research starts with a question.</footer>
    </main>
  )
}
