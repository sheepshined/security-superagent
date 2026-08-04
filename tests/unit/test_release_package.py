from scripts.build_release_package import _path_violation, _sha256


def test_release_path_gate_rejects_secrets_runtime_and_models() -> None:
    assert _path_violation(".env") == "forbidden tracked file"
    assert _path_violation("data/runtime/jobs.sqlite3") is not None
    assert _path_violation("models/embedding.onnx") is not None
    assert _path_violation("frontend/node_modules/package/index.js") is not None
    assert _path_violation("backend/app/main.py") is None
    assert _path_violation(".env.example") is None


def test_release_checksum_is_stable() -> None:
    assert _sha256(b"security-superagent") == (
        "c01d7b0c325315ba95c4633ed3de398170e699a5d6aad9e5e7fee9be899cadf4"
    )
