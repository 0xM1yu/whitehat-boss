"""`tools/model_checksum.py` — checksum 산출 규칙을 고정한다.

여기서 지키는 것은 **같은 스냅샷이면 언제 어디서 재도 같은 값**이라는 성질이다. 그
성질이 깨지면 registry의 checksum은 모델이 아니라 "누가 어느 기계에서 쟀는가"를
가리키게 되고, 재현성 기록으로서 없느니만 못하다.

기대값을 손으로 적지 않고 **정의대로 다시 계산해** 대조한다. 스크립트의 함수를 그대로
불러 비교하면 검사가 공허해지므로, 이 파일은 hashlib으로 직접 조립한다.
"""

import ast
import hashlib
import sys
from pathlib import Path

import pytest

from tools import model_checksum

FILES = {
    "config.json": b'{"quantization": "nvfp4"}',
    "model.safetensors": b"\x00\x01weights",
    "tokenizer.json": b'{"vocab": []}',
}


def _snapshot(root: Path, files: dict[str, bytes] | None = None) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for name, blob in (files or FILES).items():
        (root / name).write_bytes(blob)
    return root


def _expected(files: dict[str, bytes]) -> str:
    """정의를 다시 적는다: 경로 정렬 → `<sha256>  <경로>\\n` → 그 텍스트의 sha256."""
    manifest = "".join(
        f"{hashlib.sha256(files[name]).hexdigest()}  {name}\n" for name in sorted(files)
    )
    return "sha256:" + hashlib.sha256(manifest.encode("utf-8")).hexdigest()


def test_checksum_matches_the_definition(tmp_path: Path) -> None:
    root = _snapshot(tmp_path / "snap")

    checksum, manifest, _ = model_checksum.compute(root)

    assert checksum == _expected(FILES)
    assert len(checksum) == len("sha256:") + 64
    # sha256sum 호환: `<64hex>  <경로>`. 깨지면 `sha256sum -c`로 검증할 수 없다.
    assert all(len(line.split("  ")[0]) == 64 for line in manifest.splitlines())


def test_creation_order_does_not_change_the_value(tmp_path: Path) -> None:
    """파일을 만든 순서가 값에 섞이면 같은 모델이 장비마다 다른 checksum을 갖는다.

    `rglob`의 반환 순서는 파일시스템에 달려 있다 — 정렬을 빼먹어도 한 기계에서는
    통과하고 다른 기계에서 깨진다. 그 실패를 여기서 잡는다.
    """
    forward = _snapshot(tmp_path / "a", dict(FILES))
    backward = _snapshot(tmp_path / "b", dict(reversed(list(FILES.items()))))

    assert model_checksum.compute(forward)[0] == model_checksum.compute(backward)[0]


def test_denylisted_files_do_not_change_the_value(tmp_path: Path) -> None:
    """README가 바뀌었다고 모델이 바뀐 것은 아니다."""
    root = _snapshot(tmp_path / "snap")
    before = model_checksum.compute(root)[0]

    (root / "README.md").write_text("모델 카드", encoding="utf-8")
    (root / "LICENSE").write_text("Apache-2.0", encoding="utf-8")
    checksum, _, skipped = model_checksum.compute(root)

    assert checksum == before
    assert any("README.md" in reason for reason in skipped), "제외한 사실이 사유에 없다"


def test_a_new_weight_file_changes_the_value(tmp_path: Path) -> None:
    """denylist를 쓰는 이유가 이것이다 — 새 가중치가 조용히 빠지면 안 된다.

    allowlist였다면 목록에 없는 확장자가 통째로 무시되고, 모델이 바뀌었는데 checksum이
    그대로인 상태가 된다. 그 실패는 값을 비교하기 전까지 드러나지 않는다.
    """
    root = _snapshot(tmp_path / "snap")
    before = model_checksum.compute(root)[0]

    (root / "model-00002-of-00002.gguf").write_bytes(b"more weights")

    assert model_checksum.compute(root)[0] != before


def test_content_change_changes_the_value(tmp_path: Path) -> None:
    root = _snapshot(tmp_path / "snap")
    before = model_checksum.compute(root)[0]

    (root / "config.json").write_bytes(b'{"quantization": "fp8"}')

    assert model_checksum.compute(root)[0] != before


def test_an_empty_snapshot_is_refused(tmp_path: Path) -> None:
    """빈 디렉터리에서도 sha256은 나온다.

    그 값을 registry에 넣으면 **모든 빈 스냅샷이 같은 checksum**을 갖게 되므로 끊는다.
    """
    empty = tmp_path / "empty"
    empty.mkdir()

    with pytest.raises(model_checksum.ChecksumError):
        model_checksum.compute(empty)


def test_a_missing_directory_is_refused(tmp_path: Path) -> None:
    with pytest.raises(model_checksum.ChecksumError):
        model_checksum.compute(tmp_path / "없는-경로")


def test_an_empty_snapshot_path_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """빈 경로는 **현재 디렉터리**가 된다 — 그러면 저장소를 해시한다 (#134).

    `Path("").resolve()`가 cwd이고 `compute()`의 유일한 가드(`if not root.is_dir()`)를
    디렉터리라서 통과한다. 2026-09-03 ATOM에서 셸이 `SNAP=` 대입에 실패해 빈 문자열이
    넘어갔고, 도구는 **저장소 파일 165개를 해시해 형식이 완벽한 checksum을 냈다.**

    **모양이 완벽한 틀린 값이라 사람이 못 알아챈다** — CLAUDE.md §0이 겨냥하는 그것이다.
    """
    exit_code = model_checksum.main([""])

    assert exit_code == 2
    assert "비어" in capsys.readouterr().err


def test_an_empty_snapshot_path_writes_no_manifest(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """거부는 **쓰기 전에** 일어나야 한다 (#134).

    실제 사고에서 나쁜 쪽은 checksum이 stdout에 찍힌 것이 아니라 추적되는
    `config/checksums/<key>.txt`가 저장소 파일 목록으로 **덮어써진** 것이었다.
    """
    out = tmp_path / "manifest.txt"

    exit_code = model_checksum.main(["", "--manifest-out", str(out)])
    capsys.readouterr()

    assert exit_code == 2
    assert not out.exists(), "거부해 놓고 manifest를 썼다"


def test_the_hashed_root_is_printed_even_with_a_source(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """어떤 용법에서도 **무엇을 해시했는지** 보여야 한다 (#134).

    그전에는 루트를 찍는 유일한 줄이 `print(f'source: "{args.source or root.as_uri()}"')`
    였고, `--source`를 주면 사라졌다. 그런데 `--source`를 주는 것이 이 도구의 docstring이
    규정한 **정규 용법**이다 — 정상적으로 쓰면 잘못된 루트를 볼 창구가 닫혀 있었다.
    """
    root = _snapshot(tmp_path / "snap")

    exit_code = model_checksum.main(
        [str(root), "--source", "hf://org/repo@rev", "--manifest-out", str(tmp_path / "m.txt")]
    )
    out = capsys.readouterr().out

    assert exit_code == 0
    assert str(root.resolve()) in out, "해시한 루트가 출력에 없다"
    assert 'source: "hf://org/repo@rev"' in out, "--source는 종전대로 나와야 한다"


def test_manifest_is_written_with_lf_regardless_of_platform(tmp_path: Path) -> None:
    """개행이 번역되면 파일 내용과 aggregate의 입력이 어긋난다.

    그러면 `sha256sum -c`도, 이 스크립트로 다시 재는 것도 맞지 않는다 — Windows에서 만든
    manifest만 조용히 다른 값이 되는, 가장 찾기 어려운 종류의 어긋남이다.
    """
    root = _snapshot(tmp_path / "snap")
    out = tmp_path / "manifest.txt"

    exit_code = model_checksum.main([str(root), "--manifest-out", str(out)])

    assert exit_code == 0
    written = out.read_bytes()
    assert b"\r\n" not in written
    assert model_checksum.aggregate(written.decode("utf-8")) == model_checksum.compute(root)[0]


def _can_symlink(tmp_path: Path) -> bool:
    """이 환경에서 심볼릭 링크를 만들 수 있는가 (Windows는 권한이 필요하다)."""
    target = tmp_path / "probe-target"
    target.write_bytes(b"x")
    try:
        (tmp_path / "probe-link").symlink_to(target)
    except OSError:
        return False
    return True


def test_file_symlinks_are_followed_like_a_hugging_face_cache(tmp_path: Path) -> None:
    """HF 캐시의 스냅샷은 `../../blobs/<sha256>`을 가리키는 **링크 모음**이다.

    따라가지 않으면 해시할 내용 자체가 없어 이 스크립트가 실서빙 경로에서 쓸모없어진다.
    (Windows에서는 링크 생성에 권한이 필요해 건너뛴다 — CI(ubuntu)에서 실제로 돈다.)
    """
    if not _can_symlink(tmp_path):
        pytest.skip("이 환경에서 심볼릭 링크를 만들 수 없다")
    blobs = tmp_path / "blobs"
    blobs.mkdir()
    snapshot = tmp_path / "snapshots" / "abc123"
    snapshot.mkdir(parents=True)
    for name, blob in FILES.items():
        (blobs / name).write_bytes(blob)
        (snapshot / name).symlink_to(blobs / name)

    checksum, manifest, _ = model_checksum.compute(snapshot)

    # 값은 링크 대상의 **내용**으로 결정된다 — 실체 파일로 만든 스냅샷과 같아야 한다.
    assert checksum == _expected(FILES)
    assert "model.safetensors" in manifest


def test_directory_symlinks_are_not_followed(tmp_path: Path) -> None:
    """디렉터리 링크를 따라가면 순환하거나 스냅샷 밖 트리를 통째로 끌어들인다.

    그렇게 들어온 파일은 이 모델의 일부가 아니므로 checksum을 바꾸면 안 되고, 무시했다는
    사실은 사유로 남아야 한다(조용히 빠지면 그게 더 나쁘다).
    """
    if not _can_symlink(tmp_path):
        pytest.skip("이 환경에서 심볼릭 링크를 만들 수 없다")
    root = _snapshot(tmp_path / "snap")
    before = model_checksum.compute(root)[0]
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "stray.bin").write_bytes(b"not part of this model")

    (root / "linked").symlink_to(outside, target_is_directory=True)
    checksum, manifest, skipped = model_checksum.compute(root)

    assert checksum == before
    assert "stray.bin" not in manifest
    assert any("linked" in reason for reason in skipped)


def test_the_tool_stays_stdlib_only() -> None:
    """모델을 받은 장비에서 저장소 의존 없이 돌아야 한다 (`tools/probe/`와 같은 제약).

    `tests/test_probe.py`가 프로브에 대해 같은 것을 강제한다. 이쪽은 파일 하나라 import
    그래프를 따라갈 필요 없이 자기 모듈만 본다.
    """
    source = Path(model_checksum.__file__).read_text(encoding="utf-8")
    imported: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imported.add(node.module.split(".")[0])

    assert not (imported - sys.stdlib_module_names), f"stdlib 밖의 import: {imported}"
