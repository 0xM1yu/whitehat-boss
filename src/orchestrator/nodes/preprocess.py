"""0단계 Preprocessor — 대상 파일을 식별하고 artifact hash를 부여한다.

architecture.md §3: "파일 타입 분류, 바이너리·소스·설정 식별, artifact hash 부여".
통과 조건은 "파일 타입 분류 성공"이고, 분류하지 못한 파일은 버리지 않고
`kind=unknown`으로 남긴다 — 분류 실패 자체가 0단계의 관측치다.
"""

from pathlib import Path

from src.ids import sha256_digest
from src.orchestrator.context import RunContext
from src.orchestrator.stages import Stage
from src.schemas.artifact import Artifact
from src.schemas.enums import ArtifactKind

# 확장자 → 종류. `file(1)` 대신 쓰는 최소 분류다.
#
# **1차 분석 대상은 C/C++ 소스 저장소이고 바이너리·펌웨어는 2차다** (ADR 0010 §5,
# 2026-09-09 승인 — 확정된 슬라이서가 소스 기반 CPG라 펌웨어를 1급 입력으로 두면
# 1단계가 성립하지 않는다). 아래 바이너리·펌웨어 확장자는 **분류를 위해 남는다** —
# 지우면 그 파일이 `unknown`이 되고, 2차에 다시 만들어야 한다.
#
# ⚠ **분류는 대상 선정과 다르다.** `runner._ANALYZABLE`이 binary·firmware를 포함하므로
# 그 artifact도 `_plan_targets`에 들어간다. 「2차다」는 구현 순서이지 러너의 현재 동작이
# 아니다 — 동작을 바꾸는 것은 별건이다.
#
# 2차로 펌웨어 추출(binwalk)이 들어오면 별도 추출기가 앞에 붙고 추출된 파일은
# `parent_id`로 원본 이미지와 연결된다. 그 필드는 그때를 위해 스키마에 남아 있다.
_KIND_BY_SUFFIX: dict[str, ArtifactKind] = {
    ".c": ArtifactKind.SOURCE,
    ".h": ArtifactKind.SOURCE,
    ".cc": ArtifactKind.SOURCE,
    ".cpp": ArtifactKind.SOURCE,
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
    return artifacts


def _artifact(ctx: RunContext, path: Path, root: Path) -> Artifact:
    return Artifact(
        artifact_id=ctx.ids.next("artifact"),
        run_id=ctx.run_id,
        kind=classify(path),
        path=path.relative_to(root).as_posix(),
        digest=sha256_digest(path.read_bytes()),
        media_type=path.suffix.lower() or None,
    )
