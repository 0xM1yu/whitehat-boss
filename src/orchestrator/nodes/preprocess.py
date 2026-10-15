"""0단계 Preprocessor — 대상 파일을 식별하고 artifact hash를 부여한다.

architecture.md §3: "파일 타입 분류, 바이너리·소스·설정 식별, artifact hash 부여".
이 단계에는 **차단 조건이 없다** — 분류하지 못한 파일은 버리지 않고 `kind=unknown`으로
남긴다. 분류 실패 자체가 0단계의 관측치이고, 그 관측은 trace `preprocess_classified`에
싣는다(종류별 개수 + `unknown`의 확장자 분포). 이벤트가 없으면 「분류 실패가 몇 건인지」는
`state.db`의 artifact 행을 세어야만 알 수 있었다.
"""

import json
from collections import Counter
from pathlib import Path

from src.ids import sha256_digest
from src.orchestrator.context import RunContext
from src.orchestrator.stages import Stage
from src.orchestrator.trace import EventKind
from src.schemas.artifact import Artifact
from src.schemas.enums import ArtifactKind

# 확장자 → 종류. `file(1)` 대신 쓰는 최소 분류다.
#
# **1차 분석 대상은 C/C++ 소스 저장소이고 바이너리·펌웨어는 2차다** (ADR 0010 §5,
# 2026-09-09 승인 — 확정된 슬라이서가 소스 기반 CPG라 펌웨어를 1급 입력으로 두면
# 1단계가 성립하지 않는다). 아래 바이너리·펌웨어 확장자는 **분류를 위해 남는다** —
# 지우면 그 파일이 `unknown`이 되고, 2차에 다시 만들어야 한다.
#
# ⚠ **분류는 대상 선정과 다르고, 대상 선정은 후보 생성과 또 다르다.**
# `runner._ANALYZABLE`이 binary·firmware를 포함해 그 artifact가 `_plan_targets`까지는
# 간다. 그런데 **인덱스가 있는 프로필에서는 거기서 후보가 안 나온다** — 인덱서가
# 바이너리를 안 받으므로 `locations`가 비고 `no_symbol_artifacts`로 세고 `continue`한다
# (`runner.py`의 그 분기). 후보가 되는 것은 인덱스가 없는 폴백 경로뿐이다.
#
# ⚠ **「후보 0건」을 「아무 일도 안 한다」로 읽지 않는다.** `continue`는 `symbols_for()`
# 뒤이고 그 앞에서 둘이 이미 일어난다 — `analyzable_artifacts` 분모에 들어가고,
# 2단계 도구 중 **`semgrep`이 실제로 돈다**. `clang-tidy`·`build`는 `_require_sources`가
# `compile_sources`가 빈 것을 보고 **프로세스를 띄우기 전에 거절한다**.
# `.h`가 같은 두 줄을 지나는 것과 같은 자리다 — 이 파일의 소관이 아니다.
#
# 2차로 펌웨어 추출(binwalk)이 들어오면 별도 추출기가 앞에 붙고 추출된 파일은
# `parent_id`로 원본 이미지와 연결된다. 그 필드는 그때를 위해 스키마에 남아 있다.
_KIND_BY_SUFFIX: dict[str, ArtifactKind] = {
    ".c": ArtifactKind.SOURCE,
    ".h": ArtifactKind.SOURCE,
    ".cc": ArtifactKind.SOURCE,
    ".cpp": ArtifactKind.SOURCE,
    # C++의 다른 철자. 빠지면 `unknown`이 되고 그 파일을 가리키는 슬라이스 타깃이 조용히
    # 버려진다(`runner`의 `unmatched_slices`). `.h`와 같은 분류다 — 헤더도 SOURCE.
    # 어느 도구에 넘어가는지는 이 표가 아니라 `container_tools`의 목록 셋이 정한다(헤더·`.tcc`는
    # clang에 안 가고, 인덱서는 자기 `_GRAMMARS`에 있는 확장자만 받는다).
    ".cxx": ArtifactKind.SOURCE,
    ".c++": ArtifactKind.SOURCE,
    ".cp": ArtifactKind.SOURCE,
    ".hpp": ArtifactKind.SOURCE,
    ".hh": ArtifactKind.SOURCE,
    ".hxx": ArtifactKind.SOURCE,
    ".tcc": ArtifactKind.SOURCE,
    # `.inc`는 `#include`로 펼쳐지는 본문 조각이라 확장자만 보면 부수적이지만,
    # 실측 샘플에서는 `target.c`가 28바이트짜리 `#include` 한 줄이고 실제 코드
    # 17KB가 전부 `.inc`에 있었다. UNKNOWN으로 두면 `runner._ANALYZABLE`이 걸러
    # 그 17KB가 어느 단계에도 들어가지 않는데, **에러가 아니므로 "그 파일에
    # 취약점이 없다"와 구별되지 않는다.**
    ".inc": ArtifactKind.SOURCE,
    ".py": ArtifactKind.SOURCE,
    ".conf": ArtifactKind.CONFIG,
    ".cfg": ArtifactKind.CONFIG,
    ".ini": ArtifactKind.CONFIG,
    ".yaml": ArtifactKind.CONFIG,
    ".yml": ArtifactKind.CONFIG,
    ".json": ArtifactKind.CONFIG,
    ".bin": ArtifactKind.BINARY,
    ".elf": ArtifactKind.BINARY,
    ".so": ArtifactKind.BINARY,
    ".o": ArtifactKind.BINARY,
    ".img": ArtifactKind.FIRMWARE,
    ".fw": ArtifactKind.FIRMWARE,
}


def classify(path: Path) -> ArtifactKind:
    return _KIND_BY_SUFFIX.get(path.suffix.lower(), ArtifactKind.UNKNOWN)


def run(ctx: RunContext, *, source_root: Path) -> list[Artifact]:
    """대상 루트의 파일을 `Artifact`로 만들어 저장한다.

    파일 순서를 정렬하는 것이 요점이다 — `rglob` 순서는 파일시스템마다 달라 ID 발급
    순서가 흔들리고, 그러면 두 실행의 trace를 대조할 수 없다(engineering-guide.md §5).
    `path`는 루트 기준 상대경로다. 절대경로는 호스트마다 달라 재현 비교를 깬다.
    """
    root = Path(source_root)
    with ctx.stage(Stage.PREPROCESS):
        files = sorted(path for path in root.rglob("*") if path.is_file())
        artifacts = [_artifact(ctx, path, root) for path in files]
        for artifact in artifacts:
            ctx.store.put(artifact)
        _record_classification(ctx, artifacts)
    return artifacts


# `unknown_suffixes`에 싣는 확장자 수의 상한. 이벤트 한 줄이 저장소 크기에 비례해 커지지 않게 한다.
_UNKNOWN_SUFFIXES_CAP = 10


def _record_classification(ctx: RunContext, artifacts: list[Artifact]) -> None:
    """종류별 개수 + `unknown`의 확장자 분포를 남긴다. 키는 `ArtifactKind` 값 전부(0 포함)다.

    `unknown_suffixes`는 JSON 객체 문자열(`TraceValue`가 스칼라라서다)이고 많은 순·이름 순으로
    `_UNKNOWN_SUFFIXES_CAP`개까지다. 확장자 없는 파일은 `""` 키로 센다. 상한에 잘렸으면
    `unknown_suffixes_omitted`가 잘린 **확장자 종류** 수를 싣는다(파일 수가 아니다).
    """
    counts = Counter(artifact.kind for artifact in artifacts)
    suffixes = Counter(
        Path(artifact.path).suffix.lower()
        for artifact in artifacts
        if artifact.kind is ArtifactKind.UNKNOWN
    )
    ranked = sorted(suffixes.items(), key=lambda item: (-item[1], item[0]))
    kept = dict(ranked[:_UNKNOWN_SUFFIXES_CAP])
    ctx.trace.emit(
        EventKind.PREPROCESS_CLASSIFIED,
        stage=Stage.PREPROCESS,
        files=len(artifacts),
        **{kind.value: counts[kind] for kind in ArtifactKind},
        unknown_suffixes=json.dumps(kept, ensure_ascii=True, sort_keys=False),
        unknown_suffixes_omitted=len(ranked) - len(kept),
    )


def _artifact(ctx: RunContext, path: Path, root: Path) -> Artifact:
    return Artifact(
        artifact_id=ctx.ids.next("artifact"),
        run_id=ctx.run_id,
        kind=classify(path),
        path=path.relative_to(root).as_posix(),
        digest=sha256_digest(path.read_bytes()),
        media_type=path.suffix.lower() or None,
    )
