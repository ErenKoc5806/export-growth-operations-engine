"""Run the explicit synthetic acceptance case locally."""

import json
from dataclasses import asdict
from pathlib import Path

from pilot_engine.config import AppConfig, Environment
from pilot_engine.workflow import run_case


def main() -> None:
    config = AppConfig.from_env()
    if config.environment is Environment.PILOT:
        raise RuntimeError("The synthetic CLI is unavailable in the pilot environment")
    fixture = Path(__file__).parent / "fixtures" / "732690_de_synthetic.json"
    result = run_case(json.loads(fixture.read_text(encoding="utf-8")), config.scope)
    print(json.dumps(asdict(result), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
