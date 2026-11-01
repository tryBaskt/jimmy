import json
import re
import threading
from pathlib import Path
from datetime import datetime, timezone
RUN_ID = re.compile(r"^[A-Za-z0-9_-]{1,80}$")

def read_json(path, default=None):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default


class RunStore:
    def __init__(self, root):
        self.root = Path(root)
        self.runs = self.root / "runs"
        self.jobs = self.root / "jobs"
        self.uploads = self.root / "uploads"
        self.processes = {}
        self.lock = threading.RLock()
        self.stopping = False
        for folder in (self.runs, self.jobs, self.uploads):
            folder.mkdir(parents=True, exist_ok=True)

    def folder(self, run_id):
        if not RUN_ID.fullmatch(run_id):
            raise FileNotFoundError("Run not found")
        path = self.runs / run_id
        if not path.is_dir() and not (self.jobs / f"{run_id}.json").is_file():
            raise FileNotFoundError("Run not found")
        return path

    def write_job(self, run_id, job):
        path = self.jobs / f"{run_id}.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(job, indent=2))
        temporary.replace(path)

    def status(self, run_id, folder):
        return read_json(self.jobs / f"{run_id}.json", {}).get("status", "interrupted")

    def snapshot(self, run_id, detail=True):
        folder = self.folder(run_id)
        job = read_json(self.jobs / f"{run_id}.json", {})
        config = read_json(folder / "config.json", {})
        plan = read_json(folder / "plan.json")
        results = [read_json(path) for path in sorted(folder.glob("result_*.json"))]
        results = [result for result in results if result]
        if not results:
            results = read_json(folder / "results.json", [])
        created = job.get("created_at") or datetime.fromtimestamp(folder.stat().st_mtime, timezone.utc).isoformat()
        snapshot = {
            "id": run_id, "status": self.status(run_id, folder), "created_at": created,
            "filename": job.get("filename") or Path(config.get("paper", "Research paper.pdf")).name,
            "title": (plan or {}).get("title") or job.get("filename", "Research run"),
            "completed_experiments": sum(r.get("status") == "completed" for r in results),
            "failed_experiments": sum(r.get("status") == "failed" for r in results),
            "report_ready": (folder / "report.md").is_file(),
        }
        if not detail:
            return snapshot
        events = []
        try:
            for line in (folder / "events.jsonl").read_text().splitlines()[-1000:]:
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        except FileNotFoundError:
            pass
        snapshot.update(
            plan=plan, results=results, events=events,
            experiments=read_json(folder / "experiments.json", []),
            acquisitions=[value for path in sorted(folder.glob("data_attempt_*/manifest.json"))
                          if (value := read_json(path))],
            decisions=[value for path in sorted(folder.glob("data_decision_*.json"))
                       if (value := read_json(path))],
            web=[value for path in sorted(folder.glob("web_research_*.json"))
                 if (value := read_json(path))],
            failure=read_json(folder / "failure.json"),
            error=job.get("error"),
        )
        return snapshot

    def list_runs(self):
        ids = {p.name for p in self.runs.iterdir() if p.is_dir() and RUN_ID.fullmatch(p.name)}
        ids.update(p.stem for p in self.jobs.glob("*.json") if RUN_ID.fullmatch(p.stem))
        summaries = [self.snapshot(run_id, detail=False) for run_id in ids]
        return sorted(summaries, key=lambda run: run["created_at"], reverse=True)
