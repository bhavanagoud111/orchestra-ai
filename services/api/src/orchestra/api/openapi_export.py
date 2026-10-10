"""Write the OpenAPI schema to a file: ``python -m orchestra.api.openapi_export <path>``."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from pydantic import SecretStr

from orchestra.api.app import create_app
from orchestra.config import Settings


def main() -> None:
    target = Path(sys.argv[1] if len(sys.argv) > 1 else "openapi.json")
    settings = Settings(_env_file=None, admin_token=SecretStr("openapi-export-placeholder"))  # type: ignore[call-arg]
    app = create_app(settings)
    target.write_text(json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n")
    print(f"wrote {target}")


if __name__ == "__main__":
    main()
