from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from worldcup_brazil.calibration import evaluate_calibration, load_resolved_calibration_records


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate empirical calibration for World Cup probability forecasts.")
    parser.add_argument("--input", required=True, help="JSON file with [{predicted_pct, outcome}, ...].")
    parser.add_argument("--bins", type=int, default=5)
    parser.add_argument("--target-ece", type=float, default=0.05)
    args = parser.parse_args()

    resolved, pending_count, input_exists, source_records = load_resolved_calibration_records(Path(args.input))
    report = evaluate_calibration(resolved, bins=args.bins, target_ece=args.target_ece)
    report["input_exists"] = input_exists
    report["source_records"] = source_records
    report["pending_predictions"] = pending_count
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
