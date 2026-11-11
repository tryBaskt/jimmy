"""Session-scoped AgentCore entrypoint; no durable data layer yet."""

import base64
import asyncio
import csv
import json
import math
import os
import re
import subprocess
import sys
import threading
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from pydantic import BaseModel, ConfigDict, Field

from artifacts import RunStore, read_json
from models import MAX_DATA_ATTEMPTS
from settings import configure_openai

store = RunStore(Path(os.getenv("JIMMY_WORKSPACE", "/tmp/jimmy")))
lock = threading.RLock()
processes = {}


@asynccontextmanager
async def lifespan(application):
    yield
    with lock:
        active = list(processes.items())
        for run_id, process in active:
            job = read_json(store.jobs / f"{run_id}.json", {})
            if job.get("status") == "running":
                job["status"] = "interrupted"
                store.write_job(run_id, job)
            if process.poll() is None:
                process.terminate()
    for _, process in active:
        try:
            await asyncio.to_thread(process.wait, timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()


app = BedrockAgentCoreApp(lifespan=lifespan)


class Options(BaseModel):
    model_config = ConfigDict(extra="forbid")
    extrapolations: int = Field(3, ge=0, le=5)
    data_attempts: int = Field(MAX_DATA_ATTEMPTS, ge=1, le=MAX_DATA_ATTEMPTS)
    workers: int = Field(4, ge=1, le=8)
    symbols: str = Field("", max_length=1000, pattern=r"^[A-Za-z0-9.,^= _-]*$")


def watch(run_id, process, log, task):
    code = -1
    try:
        code = process.wait(timeout=7 * 60 * 60)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        code = -1
    finally:
        log.close()
        with lock:
            job = read_json(store.jobs / f"{run_id}.json", {})
            if job.get("status") not in {"cancelled", "interrupted"}:
                job["status"] = "completed" if code == 0 else "failed"
                if code:
                    job["error"] = "Paper replication failed; see the saved failure details."
            job["finished_at"] = datetime.now(timezone.utc).isoformat()
            store.write_job(run_id, job)
            processes.pop(run_id, None)
        app.complete_async_task(task)


@app.entrypoint
def invoke(payload):
    try:
        run_id = str(UUID(payload["run_id"]))
        owner = payload["owner"]
        if not isinstance(owner, str) or not owner:
            raise ValueError("An authenticated owner is required")
        action = payload.get("action", "snapshot")
        with lock:
            job = read_json(store.jobs / f"{run_id}.json")
            if job and job.get("owner") != owner:
                return {"error": "Run not found", "status_code": 404}
            if action == "start" and not job:
                options = Options.model_validate(payload.get("options", {}))
                paper = base64.b64decode(payload["paper"], validate=True)
                if len(paper) > 3 * 1024 * 1024 or not paper.startswith(b"%PDF-"):
                    raise ValueError("Provide a PDF up to 3 MB")
                paper_path = store.uploads / f"{run_id}.pdf"
                paper_path.write_bytes(paper)
                job = {"id": run_id, "owner": owner, "filename": Path(payload["filename"]).name,
                       "created_at": datetime.now(timezone.utc).isoformat(), "status": "running"}
                store.write_job(run_id, job)
                (store.runs / run_id).mkdir(exist_ok=True)
                task = app.add_async_task("paper_replication")
                log = (store.jobs / f"{run_id}.log").open("w")
                try:
                    process = subprocess.Popen(
                        [sys.executable, "-m", "workflow", run_id, json.dumps(options.model_dump())],
                        stdout=log, stderr=subprocess.STDOUT,
                    )
                except Exception:
                    log.close()
                    app.complete_async_task(task)
                    job["status"] = "failed"
                    store.write_job(run_id, job)
                    raise
                processes[run_id] = process
                threading.Thread(target=watch, args=(run_id, process, log, task), daemon=True).start()
            elif not job:
                return {"error": "Session expired or run not found. Run artifacts are temporary.", "status_code": 404}
            if action == "cancel":
                process = processes.get(run_id)
                if not process or process.poll() is not None:
                    return {"error": "Run is no longer active", "status_code": 409}
                job["status"] = "cancelled"
                store.write_job(run_id, job)
                process.terminate()
            folder = store.runs / run_id
            if action == "artifact":
                name = payload["artifact"]
                if name not in {"report.md", "results.json", "plan.json"}:
                    raise ValueError("Unknown artifact")
                return {"content": (folder / name).read_text()}
            if action == "series":
                experiment = payload["experiment"]
                if not re.fullmatch(r"[a-z][a-z0-9_]{0,39}", experiment):
                    raise ValueError("Invalid experiment ID")
                with (folder / "experiments" / experiment / "returns.csv").open() as stream:
                    rows = []
                    for row in csv.DictReader(stream):
                        item = {"date": row["date"]}
                        for key in ("gross_return", "estimated_net_return", "turnover"):
                            value = float(row.get(key, "nan"))
                            item[key] = value if math.isfinite(value) else None
                        rows.append(item)
                    return {"rows": rows}
            if action not in {"start", "snapshot", "cancel"}:
                raise ValueError("Unknown action")
            return store.snapshot(run_id)
    except (ValueError, KeyError) as error:
        return {"error": str(error), "status_code": 400}
    except FileNotFoundError:
        return {"error": "Artifact not available", "status_code": 404}


def main():
    configure_openai()
    app.run(host="0.0.0.0")


if __name__ == "__main__":
    main()
