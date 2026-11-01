import json
import base64
import threading
from uuid import uuid4

import runtime
from artifacts import RunStore


def test_run_ownership_is_checked_before_artifact_access(tmp_path, monkeypatch):
    store = RunStore(tmp_path)
    monkeypatch.setattr(runtime, "store", store)
    run_id = str(uuid4())
    (store.runs / run_id).mkdir()
    (store.runs / run_id / "report.md").write_text("private report")
    store.write_job(run_id, {"owner": "alice", "status": "completed"})
    request = {"run_id": run_id, "action": "artifact", "artifact": "report.md"}
    assert runtime.invoke(request | {"owner": "bob"})["status_code"] == 404
    assert runtime.invoke(request | {"owner": "alice"}) == {"content": "private report"}
    assert runtime.invoke(request | {"owner": "alice", "artifact": "../../secret"})["status_code"] == 400


def test_expired_session_does_not_claim_success(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "store", RunStore(tmp_path))
    assert runtime.invoke({"run_id": str(uuid4()), "owner": "alice"})["status_code"] == 404


def test_duplicate_start_does_not_execute_again(tmp_path, monkeypatch):
    store = RunStore(tmp_path)
    monkeypatch.setattr(runtime, "store", store)
    run_id = str(uuid4()); (store.runs / run_id).mkdir()
    store.write_job(run_id, {"owner": "alice", "status": "completed"})
    monkeypatch.setattr(runtime.subprocess, "Popen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("duplicate launch")))
    assert runtime.invoke({"run_id": run_id, "owner": "alice", "action": "start"})["status"] == "completed"


def test_async_start_and_cancel_stop_the_child(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "store", RunStore(tmp_path))
    stopped = threading.Event()
    task_finished = threading.Event()
    class Process:
        def poll(self):
            return -15 if stopped.is_set() else None
        def wait(self, timeout=None):
            assert stopped.wait(timeout=3)
            return -15
        def terminate(self):
            stopped.set()
    monkeypatch.setattr(runtime.subprocess, "Popen", lambda *args, **kwargs: Process())
    monkeypatch.setattr(runtime.app, "add_async_task", lambda name: 17)
    monkeypatch.setattr(runtime.app, "complete_async_task", lambda task: task_finished.set())
    request = {"run_id": str(uuid4()), "owner": "alice"}
    result = runtime.invoke(request | {"action": "start", "filename": "paper.pdf",
                            "paper": base64.b64encode(b"%PDF-test-fixture").decode()})
    assert result["status"] == "running"
    assert runtime.invoke(request | {"action": "cancel"})["status"] == "cancelled"
    assert stopped.is_set()
    assert task_finished.wait(timeout=3)
    assert runtime.invoke(request)["status"] == "cancelled"
