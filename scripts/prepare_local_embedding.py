#!/usr/bin/env python3
"""Explicitly provision pinned MIT E5 model files for the optional local profile.

This script is never imported or invoked by normal djobs startup. It does not
read cloud credentials, use authenticated Hugging Face clients, install packages
or modify a shared model cache. Failed downloads leave no usable partial model.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
import urllib.request
from pathlib import Path

MODEL_ID = "Xenova/multilingual-e5-small"
MODEL_REVISION = "761b726dd34fb83930e26aab4e9ac3899aa1fa78"
BASE_REVISION = "614241f622f53c4eeff9890bdc4f31cfecc418b3"
FILES = {
    "config.json": (658, "cb99455288675345e1a4f411438d5d0adbba5fbd3a67ea4fb03c015433b996c1"),
    "tokenizer.json": (
        17082730,
        "0b44a9d7b51c3c62626640cda0e2c2f70fdacdc25bbbd68038369d14ebdf4c39",
    ),
    "tokenizer_config.json": (
        443,
        "a1d6bc8734a6f635dc158508bef000f8e2e5a759c7d92f984b2c86e5ff53425b",
    ),
    "special_tokens_map.json": (
        167,
        "d05497f1da52c5e09554c0cd874037a083e1dc1b9cfd48034d1c717f1afc07a7",
    ),
    "onnx/model_quantized.onnx": (
        118308185,
        "f80102d3f2a1229f387d3c81909990d8945513e347b0eab049f7de3c6f98c193",
    ),
}


def prepare(destination: Path) -> dict[str, object]:
    destination = destination.expanduser().absolute()
    if destination.exists() or destination.is_symlink():
        raise ValueError("destination must not already exist")
    destination.parent.mkdir(parents=True, exist_ok=True)
    parent = destination.parent.resolve(strict=True)
    destination = parent / destination.name
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    manifest: dict[str, object] = {
        "model_id": MODEL_ID,
        "model_revision": MODEL_REVISION,
        "base_model": "intfloat/multilingual-e5-small",
        "base_revision": BASE_REVISION,
        "base_license": "MIT",
        "files": {},
        "purpose": "explicit local benchmark/provider; no default runtime download",
    }
    with tempfile.TemporaryDirectory(prefix=".djobs-model-", dir=parent) as temporary:
        staging = Path(temporary) / "model"
        staging.mkdir()
        files = {}
        for name, (expected_size, expected_hash) in FILES.items():
            url = f"https://huggingface.co/{MODEL_ID}/resolve/{MODEL_REVISION}/{name}"
            target = staging / name
            target.parent.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256()
            size = 0
            request = urllib.request.Request(
                url, headers={"User-Agent": "djobs-local-model-setup"}
            )
            with opener.open(request, timeout=60) as response, target.open("wb") as output:
                while chunk := response.read(1024 * 1024):
                    size += len(chunk)
                    if size > expected_size:
                        raise ValueError("download exceeds the pinned model size")
                    output.write(chunk)
                    digest.update(chunk)
            if size != expected_size or digest.hexdigest() != expected_hash:
                raise ValueError("download does not match the pinned model digest")
            files[name] = {"bytes": size, "sha256": expected_hash}
        manifest["files"] = files
        (staging / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        if destination.exists():
            raise ValueError("destination was created during download")
        staging.rename(destination)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--confirm-download", action="store_true", required=True)
    args = parser.parse_args()
    try:
        manifest = prepare(args.destination)
    except Exception:
        print(json.dumps({"ok": False, "error": "local_model_setup_failed"}))
        return 1
    print(
        json.dumps(
            {
                "ok": True,
                "model_id": manifest["model_id"],
                "model_revision": manifest["model_revision"],
            },
            ensure_ascii=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
