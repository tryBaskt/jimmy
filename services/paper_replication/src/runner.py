"""Execute the graph in a child process so cancellation stops all graph work."""

import json
import os
import sys
from datetime import date
from pathlib import Path

from agents import ResearchAgents
from reporting import save_json
from workflow import Runtime, create_workflow
from settings import configure_openai


def main():
    run_id, options = sys.argv[1], json.loads(sys.argv[2])
    root = Path(os.getenv("JIMMY_WORKSPACE", "/tmp/jimmy"))
    folder = root / "runs" / run_id
    try:
        model = configure_openai()
        overrides = {"symbols": [s.strip().upper() for s in options["symbols"].split(",") if s.strip()]}
        runtime = Runtime(ResearchAgents(model, model, folder), folder, date.today().isoformat(), overrides,
                          options["data_attempts"], options["extrapolations"], False)
        create_workflow(runtime).invoke(
            {"paper_path": str(root / "uploads" / f"{run_id}.pdf")},
            config={"max_concurrency": options["workers"], "recursion_limit": 80},
        )
    except Exception as error:
        save_json(folder / "failure.json", {"error": str(error), "type": type(error).__name__})
        raise


if __name__ == "__main__":
    main()
