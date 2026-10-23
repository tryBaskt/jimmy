export type Health = { status: 'ok'; service: string }
export type AuthState = { mode: 'signed_out' | 'signed_in'; email?: string }

export async function getAuthState(signal: AbortSignal): Promise<AuthState> {
  const response = await fetch('/api/auth/session', { signal })
  if (response.status === 401) return { mode: 'signed_out' }
  if (!response.ok) throw new Error(`Authentication returned HTTP ${response.status}`)
  const body: unknown = await response.json()
  if (!isRecord(body) || typeof body.email !== 'string') throw new Error('Invalid session response')
  return { mode: 'signed_in', email: body.email }
}

export async function signOut(): Promise<void> {
  const response = await fetch('/api/auth/logout', { method: 'POST' })
  if (!response.ok) throw new Error('Could not sign out')
}

export type PeriodMetrics = {
  total_return: number
  annualized_return: number
  sharpe: number | null
  max_drawdown: number
}

export type StrategyResult = {
  schema_version: 3
  plan: {
    hypothesis: string
    universe: string
    top_n: number
    lookback_months: number
    rebalance: 'monthly' | 'quarterly'
    metric: string
    rank_direction: 'highest' | 'lowest'
    fees_bps: number
    slippage_bps: number
    weighting: 'equal' | 'model' | 'market_cap'
    allow_short: boolean
    selection_steps: { operation: 'filter' | 'rank' | 'sample'; feature?: string; comparison?: string; value?: number; direction?: string }[]
  }
  ranking_rule: string
  universe_symbol_count: number
  eligible_symbol_count: number
  excluded_symbols: string[]
  start: string
  end_exclusive: string
  strategy: PeriodMetrics
  spy: PeriodMetrics
  selections: {
    rebalance_date: string; signal_date: string; lookback_start: string | null; symbols: string[]
    positions: { symbol: string; direction: 'long' | 'short'; weight: number; reason: string }[]
  }[]
  performance_curve: { date: string; strategy: number; spy: number }[]
  limitations: string[]
}

const STRATEGY_SCHEMA_VERSION = 3
const incompatibleApi = 'The deployed API uses an incompatible strategy version.'
const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value)
const isMetrics = (value: unknown): value is PeriodMetrics =>
  isRecord(value) && typeof value.total_return === 'number' &&
  typeof value.max_drawdown === 'number' &&
  (value.sharpe === null || typeof value.sharpe === 'number')

const wait = (milliseconds: number, signal: AbortSignal): Promise<void> => new Promise((resolve, reject) => {
  if (signal.aborted) { reject(signal.reason); return }
  const onAbort = () => { clearTimeout(timer); reject(signal.reason) }
  const timer = setTimeout(() => { signal.removeEventListener('abort', onAbort); resolve() }, milliseconds)
  signal.addEventListener('abort', onAbort, { once: true })
})

export async function getStrategyCapabilities(signal: AbortSignal): Promise<boolean> {
  const response = await fetch('/api/strategy/capabilities', {
    signal: AbortSignal.any([signal, AbortSignal.timeout(5000)]),
  })
  if (!response.ok) throw new Error(`API returned HTTP ${response.status}`)
  const body: unknown = await response.json()
  if (!isRecord(body) || body.schema_version !== STRATEGY_SCHEMA_VERSION) throw new Error(incompatibleApi)
  if (typeof body.enabled !== 'boolean') throw new Error('Unexpected API response')
  return body.enabled
}

export async function runStrategy(prompt: string, lookbackMonths: number, signal: AbortSignal): Promise<StrategyResult> {
  const response = await fetch('/api/strategy/run', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ prompt, lookback_months: lookbackMonths }),
    signal: AbortSignal.any([signal, AbortSignal.timeout(600000)]),
  })
  const body: unknown = await response.json().catch(() => null)
  if (!response.ok) {
    const detail = typeof body === 'object' && body !== null && 'detail' in body &&
      typeof body.detail === 'string' ? body.detail : `API returned HTTP ${response.status}`
    throw new Error(detail)
  }
  if (response.status !== 202 || !isRecord(body) || typeof body.job_id !== 'string') {
    throw new Error('Invalid job response')
  }
  const deadline = Date.now() + 30 * 60 * 1000
  while (Date.now() < deadline) {
    await wait(3000, signal)
    const jobResponse = await fetch(`/api/strategy/jobs/${encodeURIComponent(body.job_id)}`, {
      signal: AbortSignal.any([signal, AbortSignal.timeout(15000)]),
    })
    if (!jobResponse.ok) throw new Error(`Job status returned HTTP ${jobResponse.status}`)
    const job: unknown = await jobResponse.json()
    if (!isRecord(job)) throw new Error('Invalid job status')
    if (job.status === 'failed') throw new Error(typeof job.error === 'string' ? job.error : 'Strategy run failed')
    if (job.status === 'complete') return validateStrategyResult(job.result)
  }
  throw new Error('Strategy run is still processing; try again later')
}

function validateStrategyResult(body: unknown): StrategyResult {
  if (!isRecord(body) || body.schema_version !== STRATEGY_SCHEMA_VERSION) throw new Error(incompatibleApi)
  if (!isRecord(body.plan) || !['equal', 'model', 'market_cap'].includes(String(body.plan.weighting)) ||
    !['monthly', 'quarterly'].includes(String(body.plan.rebalance)) ||
    typeof body.plan.metric !== 'string' || typeof body.plan.hypothesis !== 'string' ||
    typeof body.plan.top_n !== 'number' || typeof body.plan.lookback_months !== 'number' ||
    typeof body.plan.universe !== 'string' ||
    typeof body.plan.fees_bps !== 'number' || typeof body.plan.slippage_bps !== 'number' ||
    typeof body.ranking_rule !== 'string' || !body.ranking_rule.trim() ||
    !isMetrics(body.strategy) || !isMetrics(body.spy) ||
    !Array.isArray(body.selections) || !Array.isArray(body.performance_curve) ||
    !Array.isArray(body.limitations) || !Array.isArray(body.excluded_symbols) ||
    typeof body.start !== 'string' || typeof body.end_exclusive !== 'string' ||
    typeof body.eligible_symbol_count !== 'number' ||
    typeof body.universe_symbol_count !== 'number') throw new Error('The API returned an incomplete strategy report; no result was shown.')
  return body as StrategyResult
}

export async function getHealth(signal: AbortSignal): Promise<Health> {
  const response = await fetch('/api/health', {
    signal: AbortSignal.any([signal, AbortSignal.timeout(5000)]),
  })
  if (!response.ok) throw new Error(`API returned HTTP ${response.status}`)
  const body: unknown = await response.json()
  if (typeof body !== 'object' || body === null || !('status' in body) ||
    body.status !== 'ok' || !('service' in body) || typeof body.service !== 'string') {
    throw new Error('Unexpected API response')
  }
  return { status: 'ok', service: body.service }
}
