"""ECS task entry point. Load one job, invoke the harness, and publish its artifacts."""

import os
import re
from datetime import date
from pathlib import Path
from threading import Lock

import storage
from artifacts import RunStore
from harness.tools.reporting import save_json
from harness.workflow import Runtime, chat_model, run_research
from jobs import Options
from settings import configure_openai


def run_job(prefix, workspace):
    if not re.fullmatch(r"runs/[0-9a-f]{64}/[0-9a-f-]{36}", prefix):
        raise ValueError("Invalid run prefix")
    request = storage.get_json(f"{prefix}/request.json")
    if request is None:
        raise RuntimeError("Run request is missing")
    run_id = prefix.rsplit("/", 1)[1]
    store = RunStore(workspace)
    folder = store.runs / run_id
    folder.mkdir(exist_ok=True)
    job = {key: request[key] for key in ("id", "filename", "created_at")} | {"status": "running"}
    lock = Lock()
    published = set()

    def publish():
        with lock:
            completed_series = [path for path in folder.glob("experiments/*/returns.csv")
                                if (folder / f"result_{path.parent.name}.json").is_file()]
            for path in [folder / "plan.json", folder / "results.json", folder / "report.md",
                         *completed_series]:
                if path.is_file() and path not in published:
                    storage.put(f"{prefix}/artifacts/{path.relative_to(folder)}", path.read_bytes())
                    published.add(path)
            store.write_job(run_id, job)
            storage.put_json(f"{prefix}/snapshot.json", store.snapshot(run_id))

    try:
        options = Options.model_validate(request["options"])
        paper = storage.get(request["paper_key"])
        if paper is None:
            raise RuntimeError("Run upload is missing")
        paper_path = store.uploads / f"{run_id}.pdf"
        paper_path.write_bytes(paper)
        pi = chat_model(configure_openai(), max_tokens=12000)
        assistant = chat_model(os.getenv("RESEARCH_ASSISTANT_MODEL", "gpt-5-mini"), max_tokens=2048)
        symbols = [symbol.strip().upper() for symbol in options.symbols.split(",") if symbol.strip()]
        config = Runtime(
            pi, assistant, folder, date.today().isoformat(), {"symbols": symbols} if symbols else {},
            max_data_attempts=options.data_attempts, max_extrapolations=options.extrapolations,
            workers=options.workers, pi_max_model_calls=int(os.getenv("PI_MAX_MODEL_CALLS", "48")),
            publish_progress=publish,
        )
        publish()
        run_research(config, paper_path)
        job["status"] = "completed"
    except Exception as error:
        save_json(folder / "failure.json", {"error": str(error), "type": type(error).__name__})
        job.update(status="failed", error="Paper replication failed. See the saved failure details.")
        raise
    finally:
        publish()


def main():
    run_job(os.environ["RUN_PREFIX"], Path(os.getenv("JIMMY_WORKSPACE", "/tmp/jimmy")))


if __name__ == "__main__":
    main()
