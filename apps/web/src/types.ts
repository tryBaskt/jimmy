export type Status = 'running' | 'completed' | 'failed' | 'cancelled' | 'interrupted';
export interface Summary {
  id: string; title: string; filename: string; status: Status; created_at: string;
  completed_experiments: number; failed_experiments: number; report_ready: boolean;
}
export interface Experiment {
  id: string; name: string; kind: 'paper' | 'extrapolation'; method: string; hypothesis: string;
  formation_months: number; holding_months: number; skip_months: number; quantiles: number;
  fees_bps: number; slippage_bps: number; deviations: string[]; start?: string; end?: string;
}
export interface Metrics {
  months: number; mean_monthly_return: number; hac_t_stat: number | null;
  annualized_sharpe: number | null; max_drawdown: number; hypothetical_total_return: number;
}
export interface Result {
  id: string; spec: Experiment; status: 'completed' | 'failed'; metrics?: Metrics;
  estimated_net_metrics?: Metrics; start?: string; end?: string; error?: string;
  vectorbt_error?: string; vectorbt?: { final_account_return: number; filled_orders: number; note: string };
  comparisons?: {metric: string; published: number; observed: number; difference: number; outcome: string; citation: {page: number; quote: string}}[];
}
export interface Acquisition {
  requested: { name: string; source: string; symbols: string[]; start: string; end: string; known_differences: string[] };
  source: string; verified_available: boolean; months?: number; error?: string; limitations?: string[];
  coverage?: Record<string, { months: number; first: string; last: string; missing_months: number }>;
}
export interface Run extends Summary {
  plan: null | { title: string; summary: string; main_claim: string; methodology: string;
    original_data_requirements: string; limitations: string[]; claim_citation: { page: number; quote: string };
    experiments: Experiment[] };
  experiments: Experiment[]; results: Result[];
  events: { time: string; stage: string; message: string }[];
  acquisitions: Acquisition[];
  decisions: { action: string; reason: string; deviations: string[]; evaluation_start?: string; evaluation_end?: string }[];
  web: { status?: string; notes: string; sources: Record<string, string> }[];
  failure?: { error: string; type: string }; error?: string;
}
export interface SeriesRow { date: string; gross_return: number; estimated_net_return: number; turnover: number }
