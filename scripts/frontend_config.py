"""Generate public runtime configuration from Terraform outputs (no secrets)."""

import json
from pathlib import Path
import sys


def main():
    outputs = json.loads(Path(sys.argv[1]).read_text())
    names = {
        "apiUrl": "api_url",
        "websocketUrl": "websocket_url",
        "cognitoDomain": "cognito_domain",
        "clientId": "cognito_client_id",
    }
    config = {k: outputs[v]["value"] for k, v in names.items()}
    config["local"] = False
    Path(sys.argv[2]).write_text(json.dumps(config))


if __name__ == "__main__":
    main()
