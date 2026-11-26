# Shared packages

One Python distribution, `jimmy-shared`, provides two importable packages:

```text
packages/
  pyproject.toml
  http_utils/
    src/            -> http_utils    Shared FastAPI app setup
    tests/
  security/
    src/            -> security      Authentication and token validation
    tests/
```

Each package keeps its tests beside its `src/` directory.

The source-directory mappings are defined in `pyproject.toml`. `src` is a filesystem
directory, not part of the import name:

```python
from http_utils.app import create_app
from security.workflow_auth import current_user
```

Install from the repository root:

```bash
python -m pip install -e 'packages[test]'
python -m pytest packages
```

Workflows depend on `jimmy-shared` and install it alongside their service in Docker.
Business routes, input schemas and execution graphs belong in the workflow service.
This library imports no workflow implementation and does not provision AWS resources.
See [adding a workflow](../docs/adding-a-workflow.md).
