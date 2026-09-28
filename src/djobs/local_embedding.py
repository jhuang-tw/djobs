"""Optional pinned E5 ONNX CPU provider. No download, account or global cache.

Only explicitly selected local model files are opened. Optional libraries are
imported when constructing the provider, never during default djobs startup.
"""

from __future__ import annotations

import hashlib
import importlib
import json
from collections.abc import Sequence
from pathlib import Path

from djobs.embedding import EmbeddingIdentity, Purpose
from djobs.privacy import redact_text


class LocalE5Provider:
    def __init__(self, model_directory: str | Path) -> None:
        root = Path(model_directory).expanduser().resolve(strict=True)
        manifest_path = root / "manifest.json"
        if (
            not manifest_path.resolve().is_relative_to(root)
            or manifest_path.stat().st_size > 32768
        ):
            raise ValueError("invalid local model manifest")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("model_id") != "Xenova/multilingual-e5-small":
            raise ValueError("unsupported local model architecture")
        if manifest.get("base_license") != "MIT":
            raise ValueError("local model license is not declared")
        files = manifest.get("files", {})
        digests = []
        for name in ("tokenizer.json", "config.json", "onnx/model_quantized.onnx"):
            path = (root / name).resolve(strict=True)
            if not path.is_relative_to(root) or not path.is_file():
                raise ValueError("local model file leaves the selected directory")
            if not 1 <= path.stat().st_size <= 512 * 1024 * 1024:
                raise ValueError("local model file exceeds its bound")
            digest = hashlib.sha256()
            with path.open("rb") as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(chunk)
            expected = files.get(name, {})
            if digest.hexdigest() != expected.get("sha256"):
                raise ValueError("local model file hash mismatch")
            if path.stat().st_size != expected.get("bytes"):
                raise ValueError("local model file size mismatch")
            digests.append(digest.hexdigest())
        self._numpy = importlib.import_module("numpy")
        ort = importlib.import_module("onnxruntime")
        tokenizers = importlib.import_module("tokenizers")
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        self._model = ort.InferenceSession(
            str(root / "onnx/model_quantized.onnx"), options, providers=["CPUExecutionProvider"]
        )
        self._tokenizer = tokenizers.Tokenizer.from_file(str(root / "tokenizer.json"))
        configuration = json.loads((root / "config.json").read_text(encoding="utf-8"))
        pad_id = int(configuration["pad_token_id"])
        self._tokenizer.enable_truncation(max_length=512)
        self._tokenizer.enable_padding(
            pad_id=pad_id, pad_token=self._tokenizer.id_to_token(pad_id), pad_type_id=0
        )
        implementation = hashlib.sha256(
            (
                "e5-prefix-mask-mean-l2-v1:"
                + ":".join(digests)
                + ":"
                + ort.__version__
                + ":"
                + tokenizers.__version__
            ).encode()
        ).hexdigest()
        self._identity = EmbeddingIdentity(
            provider_id="onnx-cpu-e5-v1",
            model_id=manifest["model_id"],
            model_revision=manifest["model_revision"] + ":" + implementation,
            dimension=384,
        )
        self.runtime_versions = {
            "onnxruntime": ort.__version__,
            "tokenizers": tokenizers.__version__,
            "numpy": self._numpy.__version__,
        }

    @property
    def identity(self) -> EmbeddingIdentity:
        return self._identity

    def embed(self, texts: Sequence[str], *, purpose: Purpose) -> Sequence[Sequence[float]]:
        if purpose not in {"query", "passage"} or not 1 <= len(texts) <= 16:
            raise ValueError("invalid E5 batch")
        inputs = [purpose + ": " + redact_text(text)[:2000] for text in texts]
        encoded = self._tokenizer.encode_batch(inputs)
        np = self._numpy
        available = {
            "input_ids": np.asarray([item.ids for item in encoded], dtype=np.int64),
            "attention_mask": np.asarray(
                [item.attention_mask for item in encoded], dtype=np.int64
            ),
            "token_type_ids": np.asarray([item.type_ids for item in encoded], dtype=np.int64),
        }
        names = {item.name for item in self._model.get_inputs()}
        if not names <= available.keys():
            raise ValueError("unsupported E5 input names")
        hidden = self._model.run(None, {name: available[name] for name in names})[0]
        if hidden.ndim != 3 or hidden.shape[2] != self.identity.dimension:
            raise ValueError("unsupported E5 output shape")
        mask = available["attention_mask"].astype(np.float32)[..., None]
        pooled = (hidden * mask).sum(axis=1) / np.maximum(mask.sum(axis=1), 1)
        norm = np.linalg.norm(pooled, axis=1, keepdims=True)
        if not np.all(np.isfinite(pooled)) or np.any(norm <= 1e-12):
            raise ValueError("invalid E5 output")
        return (pooled / norm).tolist()
