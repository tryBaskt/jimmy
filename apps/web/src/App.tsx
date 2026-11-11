import { api, ApiError, download, logout } from './client';
import { useEffect, useRef, useState, type DragEvent, type ReactNode } from 'react';
import { Activity, ArrowDownToLine, ArrowRight, ArrowUpRight, Beaker, BookOpen, Check, CheckCircle2,
  ChevronDown, ChevronRight, Circle, CircleAlert, Clock3, Database, FileText, FlaskConical,
  FolderOpen, Globe2, Layers3, LoaderCircle, PanelLeftClose, Plus, Search, Settings2,
  ShieldCheck, Sparkles, Square, Upload, Wifi, X } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type { Experiment, Result, Run, SeriesRow, Status, Summary } from './types';

const COLORS = ['#d9673f', '#334c78', '#47958d', '#a48bba', '#c39842', '#7d98c4'];
const TERMINAL = new Set(['completed', 'failed', 'cancelled', 'interrupted']);
const money = (n?: number | null) => n == null ? '—' : `${(n * 100).toFixed(2)}%`;
const decimal = (n?: number | null) => n == null ? '—' : n.toFixed(2);
const date = (s: string) => new Date(s).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
const label = (status: Status) => ({ running: 'In progress', completed: 'Completed', failed: 'Failed', cancelled: 'Stopped', interrupted: 'Interrupted' }[status]);
function Badge({ status }: { status: Status }) {
  return <span className={`badge ${status}`}><span className={status === 'running' ? 'pulse' : ''} />{label(status)}</span>;
}
function Empty({ icon, title, children }: { icon: ReactNode; title: string; children: ReactNode }) {
  return <div className="empty"><div className="empty-icon">{icon}</div><h3>{title}</h3><p>{children}</p></div>;
}

function UploadPanel({ onCreated, keyReady }: { onCreated: (run: Run) => void; keyReady: boolean | null }) {
  const input = useRef<HTMLInputElement>(null);
  const submissionId = useRef<string | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [dragging, setDragging] = useState(false);
  const [advanced, setAdvanced] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [options, setOptions] = useState({ extrapolations: 3, data_attempts: 4, workers: 4, symbols: '' });
  function select(file?: File) {
    if (!file) return;
    if (!file.name.toLowerCase().endsWith('.pdf')) { setError('Please choose a PDF research paper.'); return; }
    if (file.size > 3 * 1024 * 1024) { setError('This PDF exceeds the 3 MB upload limit.'); return; }
    submissionId.current = null;
    setError(''); setFile(file);
  }
  async function launch() {
    if (!file) return;
    setBusy(true); setError('');
    submissionId.current ??= crypto.randomUUID();
    const body = new FormData(); body.append('paper', file); body.append('run_id', submissionId.current);
    Object.entries(options).forEach(([key, value]) => body.append(key, String(value)));
    try { const run = await api<Run>('/api/paper-replication/runs', { method: 'POST', body }); setFile(null); onCreated(run); }
    catch (error) { setError((error as Error).message); }
    finally { setBusy(false); }
  }
  function drop(event: DragEvent) { event.preventDefault(); setDragging(false); if (!busy) select(event.dataTransfer.files[0]); }
  return <section className="upload-panel">
    <div className="section-heading"><div><span className="eyebrow">START AN INVESTIGATION</span><h2>A paper. A question. Let’s test it.</h2></div><span className="quiet-tag"><FileText size={13} /> PDF up to 3 MB</span></div>
    <input ref={input} type="file" accept=".pdf,application/pdf" aria-label="Upload financial research paper" onChange={event => { select(event.target.files?.[0]); event.target.value = ''; }} hidden />
    <div className={`dropzone ${dragging ? 'dragging' : ''} ${file ? 'has-file' : ''}`} onDragOver={event => { event.preventDefault(); setDragging(true); }} onDragLeave={() => setDragging(false)} onDrop={drop}>
      <div className="upload-icon">{file ? <FileText size={25} /> : <Upload size={25} />}</div>
      <div className="drop-copy"><h3>{file ? file.name : 'Drop your financial research paper here'}</h3><p>{file ? `${(file.size / 1024 / 1024).toFixed(2)} MB · Ready for the research planner` : 'We’ll trace the methodology, gather data, and put its claims to the test.'}</p></div>
      {file ? <><button className="icon-button" aria-label="Remove selected paper" disabled={busy} onClick={() => setFile(null)}><X size={17} /></button><button className="primary" disabled={busy || keyReady === false} onClick={launch}>{busy ? <LoaderCircle className="spin" size={16} /> : <Sparkles size={16} />}{busy ? 'Starting…' : 'Start research'}{!busy && <ArrowRight size={16} />}</button></>
        : <button className="secondary" onClick={() => input.current?.click()}>Choose PDF <ArrowUpRight size={15} /></button>}
    </div>
    <div className="upload-footer"><span><ShieldCheck size={14} /> Reports remain available after the run finishes.</span><button className="text-button" aria-expanded={advanced} onClick={() => setAdvanced(!advanced)}><Settings2 size={14} /> Run settings <ChevronDown size={13} className={advanced ? 'rotate' : ''} /></button></div>
    {advanced && <div className="settings-grid">
      <label>Exploratory experiments<select value={options.extrapolations} onChange={e => setOptions({ ...options, extrapolations: +e.target.value })}>{[0, 1, 2, 3, 4, 5].map(n => <option key={n}>{n}</option>)}</select></label>
      <label>Data acquisition rounds<select value={options.data_attempts} onChange={e => setOptions({ ...options, data_attempts: +e.target.value })}>{[1, 2, 3, 4].map(n => <option key={n}>{n}</option>)}</select></label>
      <label>Parallel workers<select value={options.workers} onChange={e => setOptions({ ...options, workers: +e.target.value })}>{[1, 2, 3, 4, 6, 8].map(n => <option key={n}>{n}</option>)}</select></label>
      <label className="ticker-setting">Optional ticker universe<input placeholder="AAPL, MSFT, IBM…" value={options.symbols} onChange={e => setOptions({ ...options, symbols: e.target.value })} /></label>
    </div>}
    {keyReady === false && <p className="inline-error"><CircleAlert size={15} /> The paper replication runtime is not configured.</p>}
    {error && <p role="alert" className="inline-error"><CircleAlert size={15} />{error}</p>}
  </section>;
}

function Process({ run }: { run: Run }) {
  const latest = run.events.at(-1);
  const decision = run.decisions.at(-1);
  const terminal = TERMINAL.has(run.status);
  const steps = [
    { title: 'Understand the paper', description: 'Claim & methodology', icon: BookOpen, done: !!run.plan, active: !run.plan },
    { title: 'Collect & verify data', description: run.acquisitions.length ? `${run.acquisitions.length} acquisition attempt${run.acquisitions.length > 1 ? 's' : ''}` : 'Prices & web sources', icon: Database, done: decision?.action === 'accept', active: !!run.plan && !run.experiments.length },
    { title: 'Design experiments', description: 'Paper tests & extrapolations', icon: FlaskConical, done: !!run.experiments.length, active: latest?.message.startsWith('Freezing') },
    { title: 'Run in parallel', description: run.experiments.length ? `${run.results.length} of ${run.experiments.length} finished` : 'Trusted backtests', icon: Layers3, done: !!run.experiments.length && run.results.length >= run.experiments.length, active: !!run.experiments.length && run.results.length < run.experiments.length },
    { title: 'Review the evidence', description: 'Findings & final report', icon: FileText, done: run.report_ready, active: latest?.stage === 'report' && !run.report_ready },
  ];
  return <section className="panel process-panel"><div className="panel-heading"><h3><Activity size={16} /> Research pipeline</h3><span className="muted small">{run.status === 'running' ? 'Agents are working' : label(run.status)}</span></div>
    <div className="pipeline">{steps.map((step, index) => {
      const active = step.active && !terminal && !step.done;
      const Icon = step.icon;
      return <div className={`pipeline-step ${step.done ? 'done' : active ? 'active' : ''}`} key={step.title}><div className="step-top"><div className="step-icon">{step.done ? <Check size={17} /> : active ? <LoaderCircle className="spin" size={17} /> : <Icon size={17} />}</div>{index < 4 && <div className="step-line" />}</div><h4>{step.title}</h4><p>{step.description}</p></div>;
    })}</div>
  </section>;
}

function EquityChart({ results, series }: { results: Result[]; series: Record<string, SeriesRow[]> }) {
  const [mode, setMode] = useState<'gross_return' | 'estimated_net_return'>('gross_return');
  const lines = results.filter(r => series[r.id]?.length).map((result, index) => {
    let wealth = 1;
    const baseline = new Date(`${series[result.id][0].date}T00:00:00Z`);
    baseline.setUTCDate(0);
    return { id: result.id, name: result.spec.name, color: COLORS[index % COLORS.length],
      values: [{ date: baseline.toISOString().slice(0, 10), value: 0 }, ...series[result.id].map(row => { wealth *= 1 + row[mode]; return { date: row.date, value: (wealth - 1) * 100 }; })] };
  });
  const [hover, setHover] = useState<number | null>(null);
  const all = lines.flatMap(line => line.values.map(p => p.value));
  const min = Math.min(0, ...all), max = Math.max(10, ...all);
  const lower = Math.floor(min / 20) * 20, upper = Math.ceil(max / 20) * 20;
  const W = 760, H = 235, left = 48, right = 15, top = 18, bottom = 30;
  const timeline = [...new Set(lines.flatMap(line => line.values.map(point => point.date)))].sort();
  const maxLength = Math.max(2, timeline.length);
  const x = (index: number) => left + index / (maxLength - 1) * (W - left - right);
  const y = (value: number) => top + (upper - value) / (upper - lower || 1) * (H - top - bottom);
  const ticks = Array.from({ length: 5 }, (_, i) => lower + (upper - lower) / 4 * i);
  return <section className="panel chart-panel"><div className="panel-heading"><div><h3>How the evidence compounds</h3><p>Hypothetical cumulative return · monthly observations</p></div><div className="segmented">{(['gross_return', 'estimated_net_return'] as const).map(value => <button className={mode === value ? 'selected' : ''} key={value} onClick={() => setMode(value)}>{value === 'gross_return' ? 'Gross' : 'Est. net'}</button>)}</div></div>
    {!lines.length ? <Empty icon={<Activity size={24} />} title="The first result starts the chart">Return curves will appear as experiments finish.</Empty> : <>
      <div className="chart-wrap"><svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Cumulative experiment return comparison" onMouseLeave={() => setHover(null)} onMouseMove={event => { const rect = event.currentTarget.getBoundingClientRect(); const point = (event.clientX - rect.left) / rect.width * W; setHover(Math.max(0, Math.min(maxLength - 1, Math.round((point - left) / (W - left - right) * (maxLength - 1))))); }}>
        {ticks.map(tick => <g key={tick}><line x1={left} x2={W - right} y1={y(tick)} y2={y(tick)} stroke="#e9edf1" strokeDasharray={tick === 0 ? '' : '3 5'} /><text x={left - 10} y={y(tick) + 4} textAnchor="end" className="chart-label">{tick.toFixed(0)}%</text></g>)}
        {lines.map(line => <path key={line.id} d={line.values.map((point, i) => `${i ? 'L' : 'M'}${x(timeline.indexOf(point.date)).toFixed(2)},${y(point.value).toFixed(2)}`).join(' ')} stroke={line.color} strokeWidth="2.3" fill="none" strokeLinejoin="round" />)}
        {[0, Math.floor((maxLength - 1) / 2), maxLength - 1].map(index => <text key={index} x={x(index)} y={H - 7} textAnchor={index === 0 ? 'start' : index === maxLength - 1 ? 'end' : 'middle'} className="chart-label">{timeline[index]?.slice(0, 7)}</text>)}
        {hover !== null && <line x1={x(hover)} x2={x(hover)} y1={top} y2={H - bottom} stroke="#929cab" strokeDasharray="3 3" />}
      </svg>{hover !== null && <div className="chart-tooltip"><strong>{timeline[hover]}</strong>{lines.map(line => { const point = line.values.find(point => point.date === timeline[hover]); return <div key={line.id}><i style={{ background: line.color }} />{shortName(line.name)}<b>{point ? `${point.value.toFixed(2)}%` : '—'}</b></div>; })}</div>}</div>
      <div className="chart-legend">{lines.map(line => <span key={line.id}><i style={{ background: line.color }} />{shortName(line.name)}</span>)}</div>
      <p className="chart-note">Compounded long–short spreads are hypothetical. Estimated net excludes borrowing, financing, and market impact.</p>
    </>}
  </section>;
}
function shortName(name: string) { return name.replace('Table I Panel A: ', '').replace('representative ', '').replace('momentum: ', '· ').replace('momentum', '').trim(); }

function ExperimentList({ run, filter }: { run: Run; filter: string }) {
  const [expanded, setExpanded] = useState<string | null>(null);
  const specs = run.experiments.length ? run.experiments : run.plan?.experiments || [];
  const filtered = specs.filter(spec => filter === 'all' || spec.kind === filter);
  return <section className="panel experiments-panel"><div className="panel-heading"><div><h3>Every experiment, accounted for</h3><p>Paper tests and exploratory extensions, side by side.</p></div><span className="count-tag">{specs.length} planned</span></div>
    {!filtered.length ? <Empty icon={<FlaskConical size={23} />} title="The experiment slate is taking shape">The planner will publish the methods and hypotheses before testing them.</Empty> : <div className="experiment-table"><table><thead><tr><th>Experiment</th><th>Type</th><th>Monthly return</th><th>HAC t-stat</th><th>Status</th><th><span className="sr-only">Details</span></th></tr></thead><tbody>{filtered.map(spec => {
      const result = run.results.find(r => r.id === spec.id);
      const running = !result && !TERMINAL.has(run.status) && run.events.some(e => e.message.startsWith(`Starting ${spec.id}:`));
      const state = result?.status || (running ? 'running' : TERMINAL.has(run.status) ? 'not run' : 'pending');
      return <ExperimentRows key={spec.id} spec={spec} result={result} state={state} expanded={expanded === spec.id} toggle={() => setExpanded(expanded === spec.id ? null : spec.id)} />;
    })}</tbody></table></div>}
  </section>;
}
function ExperimentRows({ spec, result, state, expanded, toggle }: { spec: Experiment; result?: Result; state: string; expanded: boolean; toggle: () => void }) {
  const costs = spec.fees_bps + spec.slippage_bps > 0;
  const metrics = costs ? result?.estimated_net_metrics : result?.metrics;
  return <><tr className={expanded ? 'expanded-row' : ''}><td><button className="experiment-name" onClick={toggle} aria-expanded={expanded}><span className={`mini-icon ${spec.kind}`}><FlaskConical size={15} /></span><span>{spec.name}<small>{spec.formation_months}m formation · {spec.holding_months}m holding · {spec.skip_months}m skip</small></span></button></td><td><span className={`type-tag ${spec.kind}`}>{spec.kind === 'paper' ? 'Paper test' : 'Extrapolation'}</span></td><td className="numeric"><strong>{money(metrics?.mean_monthly_return)}</strong><small>{costs ? 'estimated net' : 'gross'}</small></td><td className="numeric">{decimal(metrics?.hac_t_stat)}</td><td><span className={`experiment-status ${state}`}>{state === 'completed' ? <CheckCircle2 size={13} /> : state === 'running' ? <LoaderCircle className="spin" size={13} /> : state === 'failed' ? <CircleAlert size={13} /> : <Circle size={11} />}{state === 'completed' ? 'Complete' : state === 'running' ? 'Running' : state === 'failed' ? 'Failed' : state === 'not run' ? 'Not run' : 'Pending'}</span></td><td><button className="icon-button" aria-label={`Details for ${spec.name}`} onClick={toggle}><ChevronDown size={15} className={expanded ? 'rotate' : ''} /></button></td></tr>
    {expanded && <tr className="details-row"><td colSpan={6}><div className="experiment-details"><p><strong>Hypothesis</strong>{spec.hypothesis}</p><div className="detail-metrics"><span>Method<b>{spec.method.replaceAll('_', ' ')}</b></span><span>Sample<b>{result?.start || spec.start || 'Awaiting result'} → {result?.end || spec.end || '—'}</b></span><span>Annualized Sharpe<b>{decimal(metrics?.annualized_sharpe)}</b></span><span>Max drawdown<b>{money(metrics?.max_drawdown)}</b></span><span>Fees / slippage<b>{spec.fees_bps} / {spec.slippage_bps} bps</b></span></div>
      {result?.error && <p className="inline-error"><CircleAlert size={14} />{result.error}</p>}{result?.vectorbt_error && <p className="inline-error">Execution diagnostic: {result.vectorbt_error}</p>}
      {result?.vectorbt && <p className="diagnostic"><CheckCircle2 size={14} /> VectorBT completed · {result.vectorbt.filled_orders.toLocaleString()} filled orders · {money(result.vectorbt.final_account_return)} account return</p>}
      {!!spec.deviations.length && <details><summary>Methodology & data differences ({spec.deviations.length})</summary><ul>{spec.deviations.map((text, i) => <li key={i}>{text}</li>)}</ul></details>}
      {result?.comparisons?.map((comparison, i) => { const format = comparison.metric.includes('return') ? money : decimal; return <p className="comparison" key={i}>Published {comparison.metric.replaceAll('_', ' ')}: <strong>{format(comparison.published)}</strong> · Observed: <strong>{format(comparison.observed)}</strong> · PDF page {comparison.citation.page}<br /><span>Contextual comparison only. Modified data cannot establish an exact replication.</span></p>; })}
    </div></td></tr>}
  </>;
}

function Feed({ run }: { run: Run }) {
  const [all, setAll] = useState(false);
  const events = [...run.events].reverse();
  const shown = all ? events : events.slice(0, 9);
  return <section className="panel feed-panel"><div className="panel-heading"><h3><Activity size={16} /> Agent activity</h3><span className={`live-label ${run.status === 'running' ? 'is-live' : ''}`}><span />{run.status === 'running' ? 'LIVE' : 'RUN HISTORY'}</span></div>
    {!events.length ? <p className="feed-wait"><LoaderCircle className="spin" size={16} /> Waiting for the planner’s first update…</p> : <div className="feed-list">{shown.map((event, index) => <div className="feed-event" key={`${event.time}-${index}`}><div className={`feed-dot ${event.stage}`} /><div><div className="feed-meta"><strong>{({ planner: 'Research planner', data: 'Data collector', web: 'Web researcher', experiment: 'Experiment agent', report: 'Report writer', failed: 'Run error' } as Record<string, string>)[event.stage] || event.stage}</strong><time>{new Date(event.time).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}</time></div><p>{event.message.length > 220 ? <details><summary>{event.message.slice(0, 155)}…</summary>{event.message}</details> : event.message}</p></div></div>)}</div>}
    {events.length > 9 && <button className="feed-more" onClick={() => setAll(!all)}>{all ? 'Show recent activity' : `View all ${events.length} updates`} <ChevronDown size={13} className={all ? 'rotate' : ''} /></button>}
  </section>;
}

function Sources({ run }: { run: Run }) {
  return <div className="sources-grid"><section className="panel"><div className="panel-heading"><h3><Database size={16} /> Data acquisition</h3></div>{!run.acquisitions.length ? <Empty icon={<Database />} title="Finding the right observations">Actual download and coverage evidence will appear here.</Empty> : run.acquisitions.map((acquisition, index) => <div className="source-attempt" key={index}><div className="source-title"><span>ATTEMPT {index + 1}</span><span className={`type-tag ${acquisition.verified_available ? 'verified' : 'unavailable'}`}>{acquisition.verified_available ? 'Download verified' : 'Unavailable'}</span></div><h3>{acquisition.requested.name}</h3><p>{acquisition.source} · {acquisition.requested.start} → {acquisition.requested.end} (exclusive)</p>{acquisition.error && <p className="inline-error">{acquisition.error}</p>}<div className="symbol-list">{acquisition.requested.symbols.map(symbol => <span key={symbol}>{symbol}</span>)}</div>
      {!!acquisition.coverage && <details><summary>Inspect coverage for {Object.keys(acquisition.coverage).length} securities</summary><div className="coverage-table"><table><thead><tr><th>Symbol</th><th>Observed months</th><th>First / last</th><th>Missing</th></tr></thead><tbody>{Object.entries(acquisition.coverage).map(([symbol, coverage]) => <tr key={symbol}><td>{symbol}</td><td>{coverage.months}</td><td>{coverage.first} / {coverage.last}</td><td>{coverage.missing_months}</td></tr>)}</tbody></table></div></details>}
      <details><summary>Known limitations</summary><ul>{[...acquisition.requested.known_differences, ...(acquisition.limitations || [])].map((item, i) => <li key={i}>{item}</li>)}</ul></details></div>)}</section>
      <section className="panel"><div className="panel-heading"><h3><Globe2 size={16} /> Web research</h3></div>{!run.web.length ? <Empty icon={<Globe2 />} title="Searching beyond the paper">Discovered data sources will appear with their original links.</Empty> : run.web.map((web, index) => <div className="web-section" key={index}><h4>Discovery pass {index + 1}</h4><SourceLinks sources={web.sources || {}} /><details><summary>Research notes</summary><div className="markdown"><ReactMarkdown remarkPlugins={[remarkGfm]}>{web.notes}</ReactMarkdown></div></details></div>)}</section>
    {!!run.decisions.length && <section className="panel decisions-panel"><div className="panel-heading"><h3><BookOpen size={16} /> Planner’s data decisions</h3></div>{run.decisions.map((decision, index) => <div className="decision" key={index}><span className={`type-tag ${decision.action}`}>{decision.action}</span><p>{decision.reason}</p>{decision.evaluation_start && <small>Chosen evaluation: {decision.evaluation_start} → {decision.evaluation_end}</small>}</div>)}</section>}
  </div>;
}
function hostname(url: string) { try { return new URL(url).hostname; } catch { return url; } }
function SourceLinks({ sources }: { sources: Record<string, string> }) {
  const entries = Object.entries(sources).sort(([urlA, titleA], [urlB, titleB]) => Number(titleA === urlA) - Number(titleB === urlB));
  const link = ([url, title]: [string, string]) => <a className="source-link" href={url} target="_blank" rel="noreferrer" key={url}><Globe2 size={15} /><span>{title}<small>{hostname(url)}</small></span><ArrowUpRight size={14} /></a>;
  return <>{entries.slice(0, 8).map(link)}{entries.length > 8 && <details className="more-sources"><summary>Show {entries.length - 8} more discovered sources</summary>{entries.slice(8).map(link)}</details>}</>;
}

export default function App() {
  const [runs, setRuns] = useState<Summary[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [run, setRun] = useState<Run | null>(null);
  const [loading, setLoading] = useState(false);
  const [health, setHealth] = useState<{ runtime_configured: boolean; model: string } | null>(null);
  const [connection, setConnection] = useState('connecting');
  const [error, setError] = useState('');
  const [query, setQuery] = useState('');
  const [tab, setTab] = useState('overview');
  const [filter, setFilter] = useState('all');
  const [report, setReport] = useState('');
  const [series, setSeries] = useState<Record<string, SeriesRow[]>>({});
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const initial = useRef(true);
  const uploadRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let mounted = true;
    const refresh = async () => {
      try {
        const [list, status] = await Promise.all([api<Summary[]>('/api/paper-replication/runs'), api<{ runtime_configured: boolean; model: string }>('/api/paper-replication/health')]);
        if (!mounted) return;
        setRuns(list); setHealth(status);
        if (initial.current) {
          initial.current = false;
          const saved = localStorage.getItem('jimmy-selected-run');
          setSelectedId(list.find(item => item.id === saved)?.id || list.find(item => item.id.endsWith('_date_fix'))?.id || list[0]?.id || null);
        }
        setError(previous => previous.startsWith('Cannot connect') ? '' : previous);
      } catch { if (mounted) setError('Cannot connect to the paper replication API. Check your connection and sign in again if needed.'); }
    };
    refresh(); const timer = window.setInterval(refresh, 5000);
    return () => { mounted = false; clearInterval(timer); };
  }, []);

  useEffect(() => {
    setRun(null); setSeries({}); setReport(''); setTab('overview');
    if (!selectedId) { setLoading(false); return; }
    localStorage.setItem('jimmy-selected-run', selectedId);
    const controller = new AbortController();
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    setLoading(true); setConnection('connecting');
    async function refresh() {
      try {
        const snapshot = await api<Run>(`/api/paper-replication/runs/${selectedId}`, { signal: controller.signal });
        if (!active) return;
        setRun(snapshot); setLoading(false);
        setConnection(TERMINAL.has(snapshot.status) ? 'saved' : 'live');
        if (!TERMINAL.has(snapshot.status)) timer = setTimeout(refresh, 3000);
      } catch (error) {
        if (active && (error as Error).name !== 'AbortError') {
          setError((error as Error).message); setLoading(false); setConnection('reconnecting');
          if (!(error instanceof ApiError && error.status === 404)) timer = setTimeout(refresh, 10000);
        }
      }
    }
    refresh();
    return () => { active = false; controller.abort(); clearTimeout(timer); };
  }, [selectedId]);

  const completedIds = run?.results.filter(result => result.status === 'completed').map(result => result.id).join(',') || '';
  useEffect(() => {
    if (!run) return;
    let active = true;
    Promise.all(completedIds.split(',').filter(Boolean).map(async id => {
      try { return [id, await api<SeriesRow[]>(`/api/paper-replication/runs/${run.id}/experiments/${id}/series`)] as const; }
      catch { return null; }
    })).then(entries => { if (active) setSeries(Object.fromEntries(entries.filter(entry => entry !== null))); });
    return () => { active = false; };
  }, [run?.id, completedIds]);

  useEffect(() => {
    if (!run?.report_ready) return;
    let active = true;
    api<{ markdown: string }>(`/api/paper-replication/runs/${run.id}/report`).then(data => { if (active) setReport(data.markdown); }).catch(error => { if (active) setError(error.message); });
    return () => { active = false; };
  }, [run?.id, run?.report_ready]);

  function select(id: string | null) {
    if (id !== selectedId) { setRun(null); setReport(''); setSeries({}); setTab('overview'); setLoading(id !== null); }
    setSelectedId(id); setSidebarOpen(false); setError('');
  }
  function newResearch() { select(null); setTimeout(() => uploadRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' }), 0); }
  function created(snapshot: Run) { setReport(''); setSeries({}); setTab('overview'); setRuns(previous => [snapshot, ...previous]); setSelectedId(snapshot.id); setRun(snapshot); setSidebarOpen(false); }
  async function cancel() {
    if (!run) return;
    setCancelling(true);
    try { setRun(await api<Run>(`/api/paper-replication/runs/${run.id}/cancel`, { method: 'POST' })); }
    catch (error) { setError((error as Error).message); }
    finally { setCancelling(false); }
  }
  const completed = run?.results.filter(result => result.status === 'completed') || [];
  const paperCount = (run?.experiments.length ? run.experiments : run?.plan?.experiments || []).filter(e => e.kind === 'paper').length;
  const extraCount = run?.experiments.filter(e => e.kind === 'extrapolation').length || 0;
  const covered = run?.acquisitions.at(-1)?.coverage;
  const symbols = covered ? Object.values(covered).filter(value => value.months > 0).length : null;

  return <div className="app-shell">
    {sidebarOpen && <button className="sidebar-overlay" aria-label="Close navigation" onClick={() => setSidebarOpen(false)} />}
    <aside className={`sidebar ${sidebarOpen ? 'open' : ''}`}><a className="brand" href="#" onClick={event => { event.preventDefault(); newResearch(); }}><span className="brand-mark"><Sparkles size={23} strokeWidth={1.8} /></span>jimmy<span className="brand-label">RESEARCH</span></a>
      <button className="new-research" onClick={newResearch}><Plus size={17} /> New research <span>↗</span></button>
      <div className="sidebar-label">WORKSPACE</div><button className="nav-item active" onClick={() => { setTab('overview'); setSidebarOpen(false); }}><FolderOpen size={17} /> Research lab <span>{runs.length}</span></button>
      <div className="sidebar-label history-heading">RECENT RESEARCH <Clock3 size={13} /></div><label className="sidebar-search"><Search size={14} /><input aria-label="Search research runs" placeholder="Find a paper…" value={query} onChange={e => setQuery(e.target.value)} /></label>
      <div className="run-history">{runs.filter(item => `${item.title} ${item.filename}`.toLowerCase().includes(query.toLowerCase())).map(item => <button key={item.id} className={`history-item ${selectedId === item.id ? 'selected' : ''}`} onClick={() => select(item.id)}><FileText size={15} /><span><strong>{item.title}</strong><small><i className={item.status} />{date(item.created_at)}</small></span>{selectedId === item.id && <ChevronRight size={13} />}</button>)}{!runs.length && <p className="history-empty">Your investigations will<br />find a home here.</p>}{!!runs.length && !runs.some(item => `${item.title} ${item.filename}`.toLowerCase().includes(query.toLowerCase())) && <p className="history-empty">No matching papers.</p>}</div>
      <div className="sidebar-bottom"><div className="local-indicator"><span /><div><strong>Research engine</strong><small>Built for evidence, not assumptions.</small></div></div><div className="sidebar-footer"><span className="avatar">JR</span><div><b>Your workspace</b><button className="text-button" onClick={logout}>Sign out</button><small>Paper replication</small></div><ShieldCheck size={15} /></div></div>
    </aside>
    <div className="main-shell"><header className="topbar"><div><button className="icon-button mobile-toggle" aria-label="Open navigation" onClick={() => setSidebarOpen(true)}><PanelLeftClose size={19} /></button><span>Workspace</span><ChevronRight size={13} /><strong>Research lab</strong></div><span className={`server-state ${health ? '' : 'offline'}`}><span />{health ? 'Paper replication API connected' : 'Connecting to paper replication API'}</span></header>
      <main><div className="page-heading"><div><span className="eyebrow">THE RESEARCH LAB</span><h1>From paper to evidence<span>.</span></h1><p>Follow the reasoning. Inspect the experiments. Draw your own conclusions.</p></div><span className="workspace-chip"><Beaker size={15} /> Local workspace</span></div>
        {error && <div role="alert" className="error-banner"><CircleAlert size={17} /><span>{error}</span><button className="icon-button" aria-label="Dismiss error" onClick={() => setError('')}><X size={16} /></button></div>}
        <div ref={uploadRef}><UploadPanel onCreated={created} keyReady={health?.runtime_configured ?? null} /></div>
        {loading ? <div className="loading-panel"><LoaderCircle className="spin" size={22} /> Opening the research workspace…</div> : run ? <>
          <section className="run-header"><div className="run-title-block"><div className="run-kicker"><span>RESEARCH RUN</span><span className="kicker-separator">/</span><span>{date(run.created_at)}</span></div><h2>{run.title}</h2><div className="run-meta"><FileText size={13} /><span>{run.filename}</span><Badge status={run.status} />{run.status === 'running' && <span className={`stream-state ${connection}`}><Wifi size={12} />{connection === 'live' ? 'Live updates' : connection === 'reconnecting' ? 'Reconnecting…' : 'Connecting…'}</span>}</div></div><div className="run-actions">{run.status === 'running' && <button className="secondary" disabled={cancelling} onClick={cancel}><Square size={12} />{cancelling ? 'Stopping…' : 'Stop run'}</button>}{run.report_ready && <button className="secondary" onClick={() => download(`/api/paper-replication/runs/${run.id}/download/report`).catch(e => setError(e.message))}><ArrowDownToLine size={15} /> Download report</button>}</div></section>
          {(run.failure || run.error || run.status === 'interrupted' || run.status === 'cancelled') && <div className="warning-banner"><CircleAlert size={17} /><span>{run.failure?.error || run.error || (run.status === 'cancelled' ? 'This run was stopped. Completed artifacts are still available below.' : 'This run was interrupted. Existing artifacts are preserved; start a new run to try again.')}</span></div>}
          <Process run={run} />
          <div className="workspace-tabs" role="tablist" aria-label="Research views">{[{ id: 'overview', label: 'Overview', icon: Activity }, { id: 'experiments', label: 'Experiments', icon: FlaskConical }, { id: 'sources', label: 'Data & sources', icon: Database }, { id: 'report', label: 'Final report', icon: FileText }].map(item => <button role="tab" aria-selected={tab === item.id} className={tab === item.id ? 'active' : ''} key={item.id} onClick={() => setTab(item.id)}><item.icon size={15} />{item.label}{item.id === 'experiments' && !!run.experiments.length && <span>{run.experiments.length}</span>}{item.id === 'report' && run.report_ready && <i />}</button>)}</div>
          {tab === 'overview' && <><div className="stats-grid"><Stat label="PAPER EXPERIMENTS" value={String(paperCount)} description="Original methodology, explicit differences" icon={<BookOpen size={18} />} /><Stat label="EXTRAPOLATIONS" value={String(extraCount)} description="Exploratory robustness checks" icon={<Sparkles size={18} />} /><Stat label="EXPERIMENTS COMPLETED" value={`${completed.length}`} suffix={run.experiments.length ? `/ ${run.experiments.length}` : ''} description={run.failed_experiments ? `${run.failed_experiments} failed · inspect the details below` : 'Results published as each agent finishes'} icon={<CheckCircle2 size={18} />} /><Stat label="OBSERVED SECURITIES" value={symbols === null ? '—' : String(symbols)} description="From the latest downloaded dataset" icon={<Database size={18} />} /></div>
            <div className="overview-grid"><div className="overview-main"><EquityChart results={completed} series={series} />{run.plan && <section className="panel claim-panel"><div className="panel-heading"><h3><BookOpen size={16} /> What are we testing?</h3><span className="quiet-tag">PDF p. {run.plan.claim_citation.page}</span></div><p>{run.plan.main_claim}</p><details><summary>Read the methodology</summary><p>{run.plan.methodology}</p><blockquote>{run.plan.claim_citation.quote}</blockquote></details><div className="evidence-note"><ShieldCheck size={15} /><span>Modified data provides qualified evidence. Exact replication remains unverified.</span></div></section>}</div><Feed run={run} /></div>
            <ExperimentList run={run} filter="all" /></>}
          {tab === 'experiments' && <><div className="experiment-filter"><div className="segmented">{[['all', 'All experiments'], ['paper', 'Paper tests'], ['extrapolation', 'Extrapolations']].map(([value, text]) => <button key={value} className={filter === value ? 'selected' : ''} onClick={() => setFilter(value)}>{text}</button>)}</div><button className="text-button" disabled={!run.report_ready} onClick={() => download(`/api/paper-replication/runs/${run.id}/download/results`).catch(e => setError(e.message))}><ArrowDownToLine size={14} /> Results JSON</button></div><ExperimentList run={run} filter={filter} /><EquityChart results={completed.filter(result => filter === 'all' || result.spec.kind === filter)} series={series} /></>}
          {tab === 'sources' && <Sources run={run} />}
          {tab === 'report' && <section className="panel report-panel">{report ? <div className="markdown"><ReactMarkdown remarkPlugins={[remarkGfm]} components={{ a: props => <a {...props} target="_blank" rel="noreferrer" /> }}>{report}</ReactMarkdown></div> : <Empty icon={<FileText size={27} />} title={run.status === 'running' ? 'The final word comes after the evidence' : 'A final report is not available'}>{run.status === 'running' ? 'Once the experiments finish, the planner will assemble the results, comparisons, and limitations here.' : 'Inspect the activity and any completed experiment results for details.'}</Empty>}</section>}
        </> : <section className="welcome-panel"><div className="welcome-illustration"><div className="orbit orbit-one" /><div className="orbit orbit-two" /><FileText size={36} /><span className="orbit-badge"><FlaskConical size={17} /></span></div><span className="eyebrow">CURIOSITY, MEET REPRODUCIBILITY</span><h2>Your next finding starts with a paper.</h2><p>Upload a financial research PDF to watch the planner, data collectors, and experiment agents work together.</p><div className="welcome-steps"><span><BookOpen size={16} /> Understand</span><ArrowRight size={13} /><span><Database size={16} /> Gather</span><ArrowRight size={13} /><span><FlaskConical size={16} /> Experiment</span><ArrowRight size={13} /><span><FileText size={16} /> Report</span></div></section>}
        <footer className="page-footer"><span><Sparkles size={13} /> Jimmy research lab</span><span>Evidence over assumptions.<span className="footer-divider">·</span>Momentum & buy-and-hold supported; other methods are flagged.</span></footer>
      </main>
    </div>
  </div>;
}
function Stat({ label, value, suffix, description, icon }: { label: string; value: string; suffix?: string; description: string; icon: ReactNode }) {
  return <div className="stat"><div className="stat-label">{label}{icon}</div><div className="stat-number">{value}<span>{suffix}</span></div><p>{description}</p></div>;
}
