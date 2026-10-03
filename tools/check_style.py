"""Dependency-free checks for source hygiene in the solo pilot repository."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TARGETS = [ROOT / "pilot_engine", ROOT / "tests", ROOT / "tools"]


def main() -> None:
    issues = []
    for directory in TARGETS:
        for path in directory.rglob("*.py"):
            for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if line.rstrip() != line or "\t" in line:
                    issues.append(f"{path.relative_to(ROOT)}:{line_number}: trailing whitespace or tab")
    if issues:
        raise SystemExit("\n".join(issues))


if __name__ == "__main__":
    main()
