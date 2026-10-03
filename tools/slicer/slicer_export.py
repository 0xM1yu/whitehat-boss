"""Rebuild shareable source artifacts from a completed slice-v2 result."""

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path


def atomic_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent, prefix=".slices-", delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def write_json(path: Path, value: object) -> None:
    atomic_bytes(
        path,
        (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8"),
    )


def export_sources(directory: Path, result: dict, sources: dict) -> None:
    """Publish the manifest last; its hashes detect any interrupted export.

    The full candidate key disambiguates even identical basenames/call names.
    Consumers use the manifest, not a glob that could include an older export.
    """
    files = {}
    mapping = []

    def publish(name: str, content: str) -> None:
        data = content.encode("utf-8")
        atomic_bytes(directory / name, data)
        files[name] = hashlib.sha256(data).hexdigest()

    for item in result["slices"]:
        entry = item["entry"]
        symbol = re.sub(r"[^A-Za-z0-9_-]", "_", entry["symbol"])[:48]
        stem = re.sub(r"[^A-Za-z0-9_-]", "_", Path(entry["file"]).stem)[:48]
        name = (
            f"{item['case']}{item['id'][6:]}_{symbol}_{stem}_{entry['line']}_"
            f"{item['candidate_key']}"
        )
        publish(f"raw/{name}.c", item["code"] + "\n")
        numbered = (
            "\n".join(
                f"{p['file']}:{p['line']} | {sources[p['file']][p['line'] - 1]}"
                for p in item["lines"]
            )
            + "\n"
        )
        publish(f"numbered/{name}.txt", numbered)
        mapping.append(
            {
                "id": item["id"],
                "members": item["members"],
                "raw": f"raw/{name}.c",
                "numbered": f"numbered/{name}.txt",
            }
        )
    publish("slices.json", json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    publish(
        "candidate-manifest.json",
        json.dumps(result["candidate_manifest"], ensure_ascii=False, indent=2) + "\n",
    )
    manifest = {
        "slice_version": result["slice_version"],
        "status": result["status"],
        "run_signature": result.get("run_signature"),
        "slices": mapping,
        "files": files,
    }
    publish("MANIFEST.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    atomic_bytes(
        directory / "SHA256SUMS",
        "".join(f"{checksum}  {name}\n" for name, checksum in sorted(files.items())).encode(
            "utf-8"
        ),
    )
