"""Run the explicit synthetic acceptance case locally."""

import json
from dataclasses import asdict
from pathlib import Path
from time import monotonic

from pilot_engine.config import AppConfig, Environment
from pilot_engine.observability import ErrorCode, Event, Outcome, new_correlation_id
from pilot_engine.workflow import PilotValidationError, run_case


def main(config: AppConfig | None = None, fixture: Path | None = None) -> None:
    config = config if config is not None else AppConfig.from_env()
    if config.environment is Environment.PILOT:
        raise RuntimeError("The synthetic CLI is unavailable in the pilot environment")
    fixture = fixture if fixture is not None else Path(__file__).parent / "fixtures" / "732690_de_synthetic.json"
    observer = config.open_observer() if config.data_dir is not None else None
    correlation_id = new_correlation_id()
    started = monotonic()
    try:
        result = run_case(json.loads(fixture.read_text(encoding="utf-8")), config.scope)
    except (PilotValidationError, json.JSONDecodeError, OSError) as exc:
        if observer is not None:
            observer.emit(Event.SYNTHETIC_RUN, Outcome.FAILED, correlation_id,
                          error_code=(ErrorCode.IO_ERROR if isinstance(exc, OSError)
                                      else ErrorCode.VALIDATION_ERROR),
                          duration_ms=int((monotonic() - started) * 1000))
        raise
    if observer is not None:
        observer.emit(Event.SYNTHETIC_RUN, Outcome.SUCCESS, correlation_id,
                      duration_ms=int((monotonic() - started) * 1000))
    print(json.dumps(asdict(result), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
