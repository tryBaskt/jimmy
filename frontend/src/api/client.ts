export type Health = { status: 'ok'; service: string }

export type PeriodMetrics = {
  total_return: number
  annualized_return: number
  sharpe: number | null
  max_drawdown: number
}

export type ResearchResult = {
  schema_version: 2
  plan: {
    universe: string
    top_n: number
    lookback_months: number
    factors: { metric: string; direction: 'highest' | 'lowest'; weight: number }[]
    fees_bps: number
    slippage_bps: number
    weighting: 'equal'
  }
  ranking_rule: string
  universe_symbol_count: number
  eligible_symbol_count: number
  excluded_symbols: string[]
  start: string
  end_exclusive: string
  strategy: PeriodMetrics
  spy: PeriodMetrics
  selections: { rebalance_date: string; signal_date: string; lookback_start: string; symbols: string[] }[]
  performance_curve: { date: string; strategy: number; spy: number }[]
  limitations: string[]
}

const RESEARCH_SCHEMA_VERSION = 2
const incompatibleApi = 'The API is running an older research version. Restart the local app and try again.'
const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value)
const isMetrics = (value: unknown): value is PeriodMetrics =>
  isRecord(value) && typeof value.total_return === 'number' &&
  typeof value.max_drawdown === 'number' &&
  (value.sharpe === null || typeof value.sharpe === 'number')

export async function getResearchCapabilities(signal: AbortSignal): Promise<boolean> {
  const response = await fetch('/api/research/capabilities', {
    signal: AbortSignal.any([signal, AbortSignal.timeout(5000)]),
  })
  if (!response.ok) throw new Error(`API returned HTTP ${response.status}`)
  const body: unknown = await response.json()
  if (!isRecord(body) || body.schema_version !== RESEARCH_SCHEMA_VERSION) throw new Error(incompatibleApi)
  if (typeof body.enabled !== 'boolean') throw new Error('Unexpected API response')
  return body.enabled
}

export async function runResearch(prompt: string, lookbackMonths: number, signal: AbortSignal): Promise<ResearchResult> {
  const response = await fetch('/api/research/run', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ prompt, lookback_months: lookbackMonths }),
    signal: AbortSignal.any([signal, AbortSignal.timeout(180000)]),
  })
  const body: unknown = await response.json().catch(() => null)
  if (!response.ok) {
    const detail = typeof body === 'object' && body !== null && 'detail' in body &&
      typeof body.detail === 'string' ? body.detail : `API returned HTTP ${response.status}`
    throw new Error(detail)
  }
  if (!isRecord(body) || body.schema_version !== RESEARCH_SCHEMA_VERSION) throw new Error(incompatibleApi)
  if (!isRecord(body.plan) || body.plan.weighting !== 'equal' ||
    !Array.isArray(body.plan.factors) || body.plan.factors.length === 0 ||
    typeof body.plan.top_n !== 'number' || typeof body.plan.lookback_months !== 'number' ||
    typeof body.plan.universe !== 'string' ||
    typeof body.plan.fees_bps !== 'number' || typeof body.plan.slippage_bps !== 'number' ||
    typeof body.ranking_rule !== 'string' || !body.ranking_rule.trim() ||
    !isMetrics(body.strategy) || !isMetrics(body.spy) ||
    !Array.isArray(body.selections) || !Array.isArray(body.performance_curve) ||
    !Array.isArray(body.limitations) || !Array.isArray(body.excluded_symbols) ||
    typeof body.start !== 'string' || typeof body.end_exclusive !== 'string' ||
    typeof body.eligible_symbol_count !== 'number' ||
    typeof body.universe_symbol_count !== 'number') throw new Error('The API returned an incomplete research report; no result was shown.')
  return body as ResearchResult
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
