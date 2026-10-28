import React, { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  Activity,
  ArrowDownToLine,
  ArrowRight,
  Beaker,
  BookOpen,
  Check,
  ChevronRight,
  Database,
  FileText,
  FlaskConical,
  FolderOpen,
  LoaderCircle,
  LogOut,
  Plus,
  Search,
  ShieldCheck,
  Upload,
  X,
} from "lucide-react";
import {
  api,
  config,
  initialize,
  hasSession,
  liveSubscribe,
  login,
  logout,
  upload,
} from "./client";
import "./style.css";

type Project = {
  id: string;
  title: string;
  created_at: number;
  uploads?: UploadItem[];
  runs?: Run[];
};
type UploadItem = { id: string; name: string; kind: string; status: string };
type Experiment = {
  id: string;
  title: string;
  claim: string;
  method: string;
  citations: { page: number; quote: string }[];
  requirement: Record<string, unknown>;
  caveats: string[];
  dependencies: string[];
};
type Result = {
  curve?: { date: string; value: number }[];
  experiment: Experiment;
  status: string;
  reason?: string;
  mode?: string;
  metrics?: Record<string, number | string | null>;
  comparisons?: Record<string, unknown>[];
  evaluation?: Record<string, unknown>;
  result?: string;
  rationale?: string;
};
type Run = {
  id: string;
  status: string;
  stage: string;
  created_at: number;
  error?: string;
  plan?: {
    main_claim: string;
    title: string;
    supporting_claims: string[];
    experiments: Experiment[];
    limitations: string[];
  };
  prepared?: Result[];
  compared?: Result[];
  extensions?: Record<string, unknown>;
  extension_evaluated?: Result[];
  report?: {
    explanation: string;
    limitations: string[];
    coverage: {
      required: number;
      completed: number;
      central_missing: string[];
    };
    paper_experiments: Result[];
    extrapolated_experiments: Result[];
  };
};
type Event = {
  run_id?: string;
  id: string;
  cursor: string;
  kind: string;
  summary: string;
  timestamp: number;
  provisional: boolean;
  experiment_id?: string;
  detail?: unknown;
};
const stages = [
  "ingest",
  "plan",
  "data",
  "freeze",
  "build",
  "run",
  "compare",
  "extend",
  "extension_data",
  "extension_freeze",
  "extension_build",
  "extension_run",
  "extension_evaluate",
  "report",
];
const labels = [
  "Read paper",
  "Plan experiments",
  "Find data",
  "Freeze inputs",
  "Build",
  "Execute",
  "Compare",
  "Plan extensions",
  "Extension data",
  "Freeze",
  "Build",
  "Execute",
  "Evaluate",
  "Report",
];
const pretty = (s: string) => s.replace(/_/g, " ");
const percent = (x: unknown) =>
  typeof x === "number" ? `${(x * 100).toFixed(2)}%` : "—";

function App() {
  const [ready, setReady] = useState(false),
    [signed, setSigned] = useState(false),
    [projects, setProjects] = useState<Project[]>([]),
    [project, setProject] = useState<Project | null>(null),
    [run, setRun] = useState<Run | null>(null),
    [events, setEvents] = useState<Event[]>([]),
    [tab, setTab] = useState("Overview"),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [name, setName] = useState(""),
    [modal, setModal] = useState(false),
    [detail, setDetail] = useState<unknown>(null),
    [search, setSearch] = useState("");
  const cursor = useRef(""),
    paperInput = useRef<HTMLInputElement>(null),
    dataInput = useRef<HTMLInputElement>(null);
  const refreshProjects = async () => {
    const v = await api<{ items: Project[] }>("/api/projects");
    setProjects(v.items);
    return v.items;
  };
  useEffect(() => {
    (async () => {
      await initialize();
      if (!config.local && !hasSession()) return;
      await api("/api/identity/me");
      setSigned(true);
      await refreshProjects();
    })()
      .catch((e) => {
        setError(
          e instanceof Error ? e.message : "Sign-in failed. Please try again.",
        );
      })
      .finally(() => setReady(true));
  }, []);
  useEffect(() => {
    if (!project?.id) return;
    let stopped = false,
      fetching = false,
      cleanup = () => {};
    const pid = project.id;
    const refresh = async () => {
      if (fetching) return;
      fetching = true;
      try {
        const p = await api<Project>(`/api/projects/${pid}`);
        if (stopped) return;
        setProject(p);
        const latest = [...(p.runs || [])].sort(
          (a, b) => b.created_at - a.created_at,
        )[0];
        if (latest) {
          const r = await api<Run>(
            `/api/research/projects/${pid}/runs/${latest.id}`,
          );
          if (!stopped) setRun(r);
        }
        let more = true;
        while (more && !stopped) {
          const batch = await api<{ items: Event[]; cursor: string }>(
            `/api/activity/projects/${pid}/activity?after=${encodeURIComponent(cursor.current)}`,
          );
          if (stopped) break;
          cursor.current = batch.cursor;
          setEvents((prev) => {
            const seen = new Set(prev.map((x) => x.id));
            return [
              ...prev,
              ...batch.items.filter((x) => !seen.has(x.id)),
            ].sort((a, b) => a.cursor.localeCompare(b.cursor));
          });
          more = batch.items.length === 100;
        }
      } catch (e) {
        if (!stopped) setError(String(e));
      } finally {
        fetching = false;
      }
    };
    void refresh();
    const timer = setInterval(() => void refresh(), 2500);
    void liveSubscribe(pid, () => void refresh())
      .then((fn) => {
        if (stopped) fn();
        else cleanup = fn;
      })
      .catch(() => {});
    return () => {
      stopped = true;
      clearInterval(timer);
      cleanup();
    };
  }, [project?.id]);
  async function act(fn: () => Promise<unknown>) {
    setBusy(true);
    setError("");
    try {
      await fn();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }
  function select(p: Project) {
    cursor.current = "";
    setEvents([]);
    setRun(null);
    setProject(p);
    setTab("Overview");
  }
  async function newProject() {
    const p = await api<Project>("/api/projects", { title: name.trim() });
    await refreshProjects();
    select(p);
    setModal(false);
    setName("");
  }
  async function addFile(file: File | undefined, kind: "paper" | "data") {
    if (!file || !project) return;
    await upload(project.id, file, kind);
    setProject(await api<Project>(`/api/projects/${project.id}`));
  }
  async function start() {
    if (!project) return;
    const paper = [...(project.uploads || [])]
      .reverse()
      .find((x) => x.kind === "paper" && x.status === "completed");
    if (!paper) throw Error("Upload a paper first");
    await api(`/api/research/projects/${project.id}/runs`, {
      paper_id: paper.id,
      idempotency_key: crypto.randomUUID(),
    });
    setTab("Activity");
  }
  async function downloadReport() {
    if (!run?.report) return;
    const url = URL.createObjectURL(
      new Blob([JSON.stringify(run.report, null, 2)], {
        type: "application/json",
      }),
    );
    const a = document.createElement("a");
    a.href = url;
    a.download = "research-report.json";
    a.click();
    URL.revokeObjectURL(url);
  }
  async function openArtifacts() {
    if (!project) return;
    setDetail(await api(`/api/artifacts/projects/${project.id}/artifacts`));
  }
  const isRunning =
    run && !["completed", "failed", "cancelled"].includes(run.status);
  const current = run ? stages.indexOf(run.stage) : -1;
  const paperResults =
    run?.report?.paper_experiments ||
    run?.compared ||
    events
      .filter(
        (e) =>
          e.run_id === run?.id &&
          e.kind === "experiment_result" &&
          (e.detail as { group?: string })?.group === "paper_experiment",
      )
      .map((e) => e.detail as Result);
  const extended =
    run?.report?.extrapolated_experiments ||
    run?.extension_evaluated ||
    events
      .filter(
        (e) =>
          e.run_id === run?.id &&
          e.kind === "experiment_result" &&
          (e.detail as { group?: string })?.group === "extrapolated_experiment",
      )
      .map((e) => e.detail as Result);
  if (!ready)
    return (
      <div className="loading">
        <LoaderCircle className="spin" /> Opening workspace
      </div>
    );
  if (!signed)
    return (
      <div className="signin">
        <div className="mark">j</div>
        <span className="eyebrow">JIMMY / RESEARCH WORKSPACE</span>
        <h1>
          From a paper’s claim
          <br />
          to inspectable evidence.
        </h1>
        <p>
          Reconstruct experiments, follow every decision, and see how findings
          extend beyond the original sample.
        </p>
        <button
          onClick={() =>
            void login().catch((e) =>
              setError(e instanceof Error ? e.message : "Unable to start sign-in."),
            )
          }
        >
          Sign in to workspace <ArrowRight size={16} />
        </button>
        {error && <p className="error">{error}</p>}
      </div>
    );
  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="mark">j</span>
          <strong>
            jimmy<span>research workspace</span>
          </strong>
        </div>
        <div className="workspace-label">
          PERSONAL WORKSPACE <span>01</span>
        </div>
        <button className="sidebar-action" onClick={() => setModal(true)}>
          <Plus size={16} /> New research project
        </button>
        <div className="nav-label">
          PROJECTS <span>{projects.length.toString().padStart(2, "0")}</span>
        </div>
        <div className="project-list">
          {projects
            .filter((p) => p.title.toLowerCase().includes(search.toLowerCase()))
            .map((p) => (
              <button
                key={p.id}
                className={project?.id === p.id ? "active" : ""}
                onClick={() => select(p)}
              >
                <FolderOpen size={16} />
                <span>{p.title}</span>
                <ChevronRight size={13} />
              </button>
            ))}
        </div>
        <div className="sidebar-bottom">
          <div>
            <ShieldCheck size={16} />
            <span>
              Traceable by design
              <small>Versioned inputs. Visible decisions.</small>
            </span>
          </div>
          <button onClick={logout}>
            <LogOut size={14} /> Sign out
          </button>
        </div>
      </aside>
      <main>
        <header>
          <div className="breadcrumb">
            Workspace <ChevronRight size={13} />{" "}
            <span>{project?.title || "Research projects"}</span>
          </div>
          <div className="header-right">
            <span className="connection">
              <i /> {config.local ? "Local workspace" : "Connected"}
            </span>
            <span className="avatar">R</span>
          </div>
        </header>
        <div className="content">
          {error && (
            <div className="error banner">
              {error}
              <button onClick={() => setError("")} aria-label="Dismiss error">
                <X size={15} />
              </button>
            </div>
          )}
          {!project ? (
            <>
              <div className="page-heading">
                <div>
                  <div className="eyebrow">YOUR RESEARCH, RECONSTRUCTED</div>
                  <h1>Research projects</h1>
                  <p>
                    A clear path from published claims to reproducible
                    experiments.
                  </p>
                </div>
                <button onClick={() => setModal(true)}>
                  <Plus size={16} /> New project
                </button>
              </div>
              <div className="search">
                <Search size={16} />
                <input
                  placeholder="Search projects"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                />
              </div>
              {projects.length ? (
                <div className="project-grid">
                  {projects
                    .filter((p) =>
                      p.title.toLowerCase().includes(search.toLowerCase()),
                    )
                    .map((p) => (
                      <button
                        className="project-card"
                        key={p.id}
                        onClick={() => select(p)}
                      >
                        <BookOpen size={24} />
                        <h3>{p.title}</h3>
                        <p>
                          Created{" "}
                          {new Date(p.created_at * 1000).toLocaleDateString()}
                        </p>
                        <span>
                          Open project <ArrowRight size={15} />
                        </span>
                      </button>
                    ))}
                </div>
              ) : (
                <div className="empty">
                  <FlaskConical size={34} />
                  <h2>Your next investigation starts here.</h2>
                  <p>
                    Create a project, upload an academic paper, and follow the
                    evidence as it takes shape.
                  </p>
                  <button onClick={() => setModal(true)}>
                    <Plus size={16} /> Create your first project
                  </button>
                </div>
              )}
            </>
          ) : (
            <>
              <div className="page-heading">
                <div>
                  <div className="eyebrow">PAPER REPLICATION PROJECT</div>
                  <h1>{project.title}</h1>
                  <p>
                    Original experiments. Transparent modifications. Further
                    exploration.
                  </p>
                </div>
                <div className="actions">
                  {isRunning ? (
                    <button
                      className="secondary"
                      onClick={() =>
                        void act(() =>
                          api(
                            `/api/research/projects/${project.id}/runs/${run.id}/cancel`,
                            {},
                          ),
                        )
                      }
                    >
                      Cancel run
                    </button>
                  ) : (
                    <button
                      disabled={
                        busy ||
                        !project.uploads?.some(
                          (x) => x.kind === "paper" && x.status === "completed",
                        )
                      }
                      onClick={() => void act(start)}
                    >
                      <Beaker size={16} />{" "}
                      {run ? "Start new run" : "Start analysis"}
                    </button>
                  )}
                </div>
              </div>
              <div className="metrics-strip">
                <div>
                  <FileText size={18} />
                  <span>
                    Paper
                    <small>
                      {project.uploads?.find((x) => x.kind === "paper")?.name ||
                        "Awaiting upload"}
                    </small>
                  </span>
                </div>
                <div>
                  <FlaskConical size={18} />
                  <span>
                    Experiments
                    <small>
                      {run?.plan?.experiments.length || "—"} identified
                    </small>
                  </span>
                </div>
                <div>
                  <Database size={18} />
                  <span>
                    Data
                    <small>
                      {run?.prepared?.filter((x) => x.status === "ready")
                        .length || 0}{" "}
                      inputs ready
                    </small>
                  </span>
                </div>
                <div>
                  <Activity size={18} />
                  <span>
                    Status
                    <small className={isRunning ? "live-text" : ""}>
                      {run ? pretty(run.status) : "Ready to begin"}
                    </small>
                  </span>
                </div>
              </div>
              <nav className="tabs">
                {["Overview", "Plan", "Data", "Results", "Activity"].map(
                  (t) => (
                    <button
                      key={t}
                      className={tab === t ? "selected" : ""}
                      onClick={() => setTab(t)}
                    >
                      {t}
                      {t === "Activity" && isRunning && <i />}
                    </button>
                  ),
                )}
                <button
                  className="artifacts"
                  onClick={() => void act(openArtifacts)}
                >
                  <ArrowDownToLine size={14} /> Artifacts
                </button>
              </nav>
              {tab === "Overview" && (
                <>
                  <div className="overview-grid">
                    <section className="panel">
                      <div className="section-label">01 / SOURCE MATERIAL</div>
                      <h2>The starting point</h2>
                      <p>
                        Upload a searchable academic PDF. Add numerical data if
                        you have it; the planner also checks connected sources.
                      </p>
                      <button
                        className="dropzone"
                        onClick={() => paperInput.current?.click()}
                        disabled={busy}
                      >
                        <Upload size={23} />
                        <strong>Upload research paper</strong>
                        <span>Searchable PDF · up to 25 MB</span>
                      </button>
                      <button
                        className="text-button"
                        onClick={() => dataInput.current?.click()}
                        disabled={busy}
                      >
                        <Plus size={14} /> Attach monthly return data (.csv)
                      </button>
                      <div className="files">
                        {project.uploads?.map((u) => (
                          <div key={u.id}>
                            <FileText size={14} />
                            <span>{u.name}</span>
                            <span className="pill">{u.status}</span>
                          </div>
                        ))}
                      </div>
                    </section>
                    <section className="panel">
                      <div className="section-label">02 / THE PROCESS</div>
                      <h2>Every step, in view</h2>
                      <p>
                        The application interprets the paper automatically.
                        Plans, data choices, and results appear here as work
                        progresses.
                      </p>
                      <div className="steps">
                        {[
                          "Identify the main claim & experiments",
                          "Find data & disclose modifications",
                          "Reconstruct & compare the results",
                          "Extrapolate & assess the evidence",
                        ].map((v, i) => (
                          <div key={v}>
                            <span>{i + 1}</span>
                            <div>
                              <strong>{v}</strong>
                              <small>
                                {
                                  [
                                    "References back to the original paper",
                                    "Existing catalog first, connected sources next",
                                    "Frozen inputs and inspectable calculations",
                                    "Separate findings, shared context",
                                  ][i]
                                }
                              </small>
                            </div>
                          </div>
                        ))}
                      </div>
                    </section>
                  </div>
                  {run?.plan && (
                    <section className="panel claim">
                      <div className="section-label">
                        MAIN CLAIM / APPLICATION INTERPRETATION
                      </div>
                      <h2>{run.plan.main_claim}</h2>
                      <button
                        className="text-button"
                        onClick={() => setTab("Plan")}
                      >
                        Inspect experiment plan <ArrowRight size={14} />
                      </button>
                    </section>
                  )}
                  {run?.report && (
                    <section className="panel">
                      <div className="section-label">FINAL ASSESSMENT</div>
                      <h2>What the evidence shows</h2>
                      <p className="prose">{run.report.explanation}</p>
                      <button
                        className="secondary"
                        onClick={() => void downloadReport()}
                      >
                        <ArrowDownToLine size={15} /> Download assessment
                      </button>
                    </section>
                  )}
                </>
              )}
              {tab === "Plan" && (
                <>
                  {run?.plan ? (
                    <>
                      <section className="panel claim">
                        <div className="section-label">
                          MAIN CLAIM · READ ONLY
                        </div>
                        <h2>{run.plan.main_claim}</h2>
                        {run.plan.supporting_claims.map((s, i) => (
                          <p key={i}>{s}</p>
                        ))}
                        <p className="muted">
                          {run.plan.limitations.join(" ")}
                        </p>
                      </section>
                      <div className="section-top">
                        <h2>Supporting experiment suite</h2>
                        <span className="pill">
                          {run.plan.experiments.length} experiments
                        </span>
                      </div>
                      {run.plan.experiments.map((e, i) => (
                        <section className="panel experiment" key={e.id}>
                          <div className="exp-number">
                            {String(i + 1).padStart(2, "0")}
                          </div>
                          <div>
                            <span className="pill">{pretty(e.method)}</span>
                            <h3>{e.title}</h3>
                            <p>{e.claim}</p>
                            {e.citations.map((c, j) => (
                              <blockquote key={j}>
                                “{c.quote}” <span>Page {c.page}</span>
                              </blockquote>
                            ))}
                            <button
                              className="text-button"
                              onClick={() => setDetail(e)}
                            >
                              Inspect specification <ArrowRight size={14} />
                            </button>
                          </div>
                        </section>
                      ))}
                    </>
                  ) : (
                    <Empty
                      icon={<BookOpen />}
                      title="The plan will appear here"
                      text="Once analysis starts, the main claim and supporting experiments become visible as they are identified."
                    />
                  )}
                </>
              )}
              {tab === "Data" && (
                <>
                  <div className="section-top">
                    <div>
                      <h2>Data & methodology</h2>
                      <p>
                        Source discovery, dataset reuse, and modifications
                        remain attached to each experiment.
                      </p>
                    </div>
                    <button
                      className="secondary"
                      onClick={() => dataInput.current?.click()}
                    >
                      <Plus size={15} /> Attach data
                    </button>
                  </div>
                  {run?.prepared?.map((x) => (
                    <section className="panel" key={x.experiment.id}>
                      <div className="section-top">
                        <h3>{x.experiment.title}</h3>
                        <span className="pill">{x.mode || x.status}</span>
                      </div>
                      <p>{x.rationale || x.reason}</p>
                      {x.experiment.caveats?.length > 0 && (
                        <div className="caveat">
                          <strong>Modifications & caveats</strong>
                          {x.experiment.caveats.map((c, i) => (
                            <p key={i}>{c}</p>
                          ))}
                        </div>
                      )}
                      <button
                        className="text-button"
                        onClick={() => setDetail(x)}
                      >
                        Inspect dataset manifest <ArrowRight size={14} />
                      </button>
                    </section>
                  ))}
                  {events
                    .filter((e) =>
                      [
                        "cache_hit",
                        "data_candidate",
                        "fetch_started",
                        "dataset_committed",
                        "data_selected",
                      ].includes(e.kind),
                    )
                    .map((e) => (
                      <EventRow
                        key={e.id}
                        event={e}
                        inspect={() => setDetail(e.detail)}
                      />
                    ))}
                  {!run?.prepared &&
                    !events.some((e) => e.kind === "data_candidate") && (
                      <Empty
                        icon={<Database />}
                        title="Data discovery has not started"
                        text="The adapter checks the internal catalog before fetching from yfinance or Bigdata.com. Suitable shared datasets are reused."
                      />
                    )}
                </>
              )}
              {tab === "Results" && (
                <>
                  <div className="section-top">
                    <div>
                      <h2>Paper experiment results</h2>
                      <p>
                        Original or modified implementations. Caveats accompany
                        each result.
                      </p>
                    </div>
                    {isRunning && (
                      <span className="pill">
                        <LoaderCircle size={12} className="spin" /> Updating
                        live
                      </span>
                    )}
                  </div>
                  {paperResults.length ? (
                    paperResults.map((x) => (
                      <ResultCard
                        key={x.experiment.id}
                        result={x}
                        inspect={() => setDetail(x)}
                      />
                    ))
                  ) : (
                    <Empty
                      icon={<Beaker />}
                      title="Results arrive experiment by experiment"
                      text="You do not need to wait for the final report. Completed calculations will appear as they become available."
                    />
                  )}
                  <div className="section-top extension-title">
                    <div>
                      <h2>Extrapolated experiment results</h2>
                      <p>
                        Extensions to the executed methodology, evaluated
                        separately.
                      </p>
                    </div>
                  </div>
                  {extended.length ? (
                    extended.map((x) => (
                      <ResultCard
                        key={x.experiment.id}
                        result={x}
                        inspect={() => setDetail(x)}
                      />
                    ))
                  ) : (
                    <div className="panel muted">
                      {run?.extensions
                        ? "Extrapolations planned or unavailable; inspect activity for details."
                        : "Extrapolations are planned after the paper experiments are compared."}
                    </div>
                  )}
                  {run?.report && (
                    <section className="panel">
                      <h2>Coverage & limitations</h2>
                      <p>
                        {run.report.coverage.completed} of{" "}
                        {run.report.coverage.required} paper experiments
                        completed.
                      </p>
                      {run.report.limitations.map((x, i) => (
                        <p key={i} className="muted">
                          {x}
                        </p>
                      ))}
                    </section>
                  )}
                </>
              )}
              {tab === "Activity" && (
                <>
                  <div className="section-top">
                    <div>
                      <h2>Live research activity</h2>
                      <p>
                        A persistent record of actions, decisions, and available
                        evidence.
                      </p>
                    </div>
                    <span className="connection">
                      <i /> Updates automatically
                    </span>
                  </div>
                  {run && (
                    <div className="pipeline">
                      {stages.map((s, i) => (
                        <div
                          key={s}
                          className={
                            i === current
                              ? "current"
                              : i < current || run.status === "completed"
                                ? "done"
                                : ""
                          }
                        >
                          <span>
                            {i < current || run.status === "completed" ? (
                              <Check size={12} />
                            ) : (
                              i + 1
                            )}
                          </span>
                          <small>{labels[i]}</small>
                        </div>
                      ))}
                    </div>
                  )}
                  {run?.error && (
                    <div className="error banner">{run.error}</div>
                  )}
                  <div className="timeline">
                    {[...events].reverse().map((e) => (
                      <EventRow
                        key={e.id}
                        event={e}
                        inspect={() => setDetail(e.detail)}
                      />
                    ))}
                  </div>
                </>
              )}
              <input
                ref={paperInput}
                type="file"
                accept="application/pdf"
                hidden
                onChange={(e) => {
                  void act(() => addFile(e.target.files?.[0], "paper"));
                  e.target.value = "";
                }}
              />
              <input
                ref={dataInput}
                type="file"
                accept=".csv"
                hidden
                onChange={(e) => {
                  void act(() => addFile(e.target.files?.[0], "data"));
                  e.target.value = "";
                }}
              />
            </>
          )}
          <footer>
            <span>JIMMY RESEARCH WORKSPACE</span>
            <span>
              Reproduction is evidence, not a guarantee of future performance.
            </span>
          </footer>
        </div>
      </main>
      {modal && (
        <div className="overlay" onClick={() => setModal(false)}>
          <form
            className="modal"
            onClick={(e) => e.stopPropagation()}
            onSubmit={(e) => {
              e.preventDefault();
              void act(newProject);
            }}
          >
            <button
              className="close"
              type="button"
              aria-label="Close"
              onClick={() => setModal(false)}
            >
              <X size={18} />
            </button>
            <span className="eyebrow">A NEW INVESTIGATION</span>
            <h2>Create a research project</h2>
            <p>Give your workspace a name. You can upload the paper next.</p>
            <label>
              Project name
              <input
                autoFocus
                value={name}
                onChange={(e) => setName(e.target.value)}
                maxLength={180}
                placeholder="e.g. Revisiting equity momentum"
                required
              />
            </label>
            <button disabled={busy || !name.trim()} type="submit">
              Create project <ArrowRight size={15} />
            </button>
          </form>
        </div>
      )}
      {detail !== null && (
        <div className="overlay" onClick={() => setDetail(null)}>
          <section
            className="modal inspector"
            onClick={(e) => e.stopPropagation()}
          >
            <button
              className="close"
              aria-label="Close"
              onClick={() => setDetail(null)}
            >
              <X size={18} />
            </button>
            <span className="eyebrow">EVIDENCE INSPECTOR</span>
            <h2>Recorded artifact</h2>
            <pre>{JSON.stringify(detail, null, 2)}</pre>
          </section>
        </div>
      )}
    </div>
  );
}
function Empty({
  icon,
  title,
  text,
}: {
  icon: React.ReactNode;
  title: string;
  text: string;
}) {
  return (
    <div className="empty compact">
      {icon}
      <h3>{title}</h3>
      <p>{text}</p>
    </div>
  );
}
function EventRow({
  event: e,
  inspect,
}: {
  event: Event;
  inspect: () => void;
}) {
  return (
    <article className="event">
      <span className="event-dot" />
      <div>
        <div className="event-head">
          <span>{pretty(e.kind)}</span>
          <time>
            {new Date(e.timestamp * 1000).toLocaleTimeString([], {
              hour: "2-digit",
              minute: "2-digit",
              second: "2-digit",
            })}
          </time>
        </div>
        <p>{e.summary}</p>
        <div className="event-meta">
          {e.experiment_id && <span>{e.experiment_id}</span>}
          {e.provisional && <span>Provisional</span>}
          {e.detail != null && (
            <button onClick={inspect}>
              Inspect details <ChevronRight size={12} />
            </button>
          )}
        </div>
      </div>
    </article>
  );
}
function ResultCard({
  result: r,
  inspect,
}: {
  result: Result;
  inspect: () => void;
}) {
  return (
    <section className="panel result">
      <div className="section-top">
        <h3>{r.experiment.title}</h3>
        <span className="pill">{r.mode || r.status}</span>
      </div>
      {r.metrics ? (
        <div className="result-metrics">
          <div>
            <span>Mean monthly return</span>
            <strong>{percent(r.metrics.mean_monthly_return)}</strong>
          </div>
          <div>
            <span>Annualized volatility</span>
            <strong>{percent(r.metrics.annualized_volatility)}</strong>
          </div>
          <div>
            <span>HAC t-statistic</span>
            <strong>
              {typeof r.metrics.t_stat === "number"
                ? r.metrics.t_stat.toFixed(2)
                : "—"}
            </strong>
          </div>
          <div>
            <span>Months evaluated</span>
            <strong>{r.metrics.months}</strong>
          </div>
        </div>
      ) : (
        <p>{r.reason || "Awaiting execution"}</p>
      )}
      {r.curve && r.curve.length > 1 && <Curve points={r.curve} />}{" "}
      {r.comparisons && r.comparisons.length > 0 && (
        <div className="comparison-table">
          <table>
            <thead>
              <tr>
                <th>Metric</th>
                <th>Published</th>
                <th>Calculated</th>
                <th>Assessment</th>
              </tr>
            </thead>
            <tbody>
              {r.comparisons.map((c, i) => (
                <tr key={i}>
                  <td>{pretty(String(c.metric))}</td>
                  <td>
                    {typeof c.published === "number"
                      ? c.published.toFixed(4)
                      : "—"}
                  </td>
                  <td>
                    {typeof c.calculated === "number"
                      ? c.calculated.toFixed(4)
                      : "—"}
                  </td>
                  <td>{pretty(String(c.outcome))}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {r.experiment.caveats?.length > 0 && (
        <p className="caveat">{r.experiment.caveats.join(" ")}</p>
      )}
      <button className="text-button" onClick={inspect}>
        Inspect result & evidence <ArrowRight size={14} />
      </button>
    </section>
  );
}

function Curve({ points }: { points: { date: string; value: number }[] }) {
  const lo = Math.min(1, ...points.map((p) => p.value)),
    hi = Math.max(1, ...points.map((p) => p.value));
  const y = (v: number) => 105 - ((v - lo) / (hi - lo || 1)) * 90;
  const path = points
    .map(
      (p, i) =>
        `${i ? "L" : "M"}${((i / (points.length - 1)) * 600).toFixed(2)},${y(p.value).toFixed(2)}`,
    )
    .join(" ");
  return (
    <div className="curve">
      <div>
        <span>Hypothetical compounded spread · before costs</span>
        <span>
          {points[0].date} — {points[points.length - 1].date}
        </span>
      </div>
      <svg
        viewBox="0 0 600 120"
        role="img"
        aria-label="Hypothetical compounded return series"
      >
        <line
          x1="0"
          x2="600"
          y1={y(1)}
          y2={y(1)}
          stroke="#dfe7e2"
          strokeDasharray="4 4"
        />
        <path d={path} fill="none" stroke="#397d60" strokeWidth="2" />
      </svg>
    </div>
  );
}

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
