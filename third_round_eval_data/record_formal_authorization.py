"""Record a later explicit authorization without starting the formal run."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date
from pathlib import Path


BASE = Path(__file__).resolve().parent
MANIFEST = BASE / "manifest.json"
AUTHORIZATION = BASE / "formal_authorization_record.json"
READINESS = BASE / "formal_readiness_20260912_01.json"
ACKNOWLEDGEMENT = "START FORMAL PAID RUN"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def write_json(path: Path, payload: dict) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--authorized-by", required=True)
    parser.add_argument("--authorized-on", default=date.today().isoformat())
    parser.add_argument("--acknowledge", required=True)
    args = parser.parse_args()
    if args.acknowledge != ACKNOWLEDGEMENT:
        raise SystemExit(f"--acknowledge must equal {ACKNOWLEDGEMENT!r}")
    readiness = json.loads(READINESS.read_text(encoding="utf-8"))
    if readiness.get("status") != "ready_for_authorization":
        raise SystemExit("formal readiness is not complete")
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if manifest.get("review", {}).get("formal_run_authorized") is True:
        raise SystemExit("formal run is already authorized")
    config = json.loads((BASE / "formal_run_config.json").read_text(encoding="utf-8"))
    for run_id in (
        config["main_collection"]["run_id"],
        config["reliability_collection"]["run_id"],
    ):
        if (BASE / "runs" / run_id).exists():
            raise SystemExit(f"formal run directory already exists: {run_id}")

    authorization = json.loads(AUTHORIZATION.read_text(encoding="utf-8"))
    authorization.update({
        "status": "authorized",
        "authorized_by": args.authorized_by,
        "authorized_on": args.authorized_on,
        "acknowledgement": ACKNOWLEDGEMENT,
        "note": "Explicit authorization recorded; this command did not start execution.",
    })
    write_json(AUTHORIZATION, authorization)
    manifest["status"] = "formal_authorized_not_started"
    manifest["review"]["formal_run_authorized"] = True
    manifest["review"]["formal_authorized_by"] = args.authorized_by
    manifest["review"]["formal_authorized_on"] = args.authorized_on
    manifest["review"]["authorization_record"] = AUTHORIZATION.name
    manifest["artifact_hashes"][AUTHORIZATION.name] = sha256(AUTHORIZATION)
    manifest["updated_on"] = args.authorized_on
    write_json(MANIFEST, manifest)
    print(json.dumps({
        "status": manifest["status"],
        "formal_run_authorized": True,
        "formal_run_started": False,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
