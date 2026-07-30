from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class AiReportCache:
    def __init__(self, path: Path):
        self.path = Path(path)

    def _read(self) -> dict[str, Any]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"reports": {}}
        except json.JSONDecodeError:
            return {"reports": {}}

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def put(self, report_id: str, report: dict[str, Any]) -> None:
        data = self._read()
        reports = data.setdefault("reports", {})
        reports[report_id] = report
        if len(reports) > 80:
            for key in sorted(reports.keys())[:-80]:
                reports.pop(key, None)
        self._write(data)

    def get(self, report_id: str) -> dict[str, Any] | None:
        return self._read().get("reports", {}).get(report_id)
