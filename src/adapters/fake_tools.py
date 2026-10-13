"""FakeToolRunner — Docker 없이 도구 신호를 만드는 결정론적 ToolRunner.

실제 도구를 흉내 내되 **파일을 실제로 읽는다.** 고정된 Evidence를 돌려주면 0단계에서
바뀐 대상이 2단계 신호에 반영되지 않아, 파이프라인이 입력과 무관하게 같은 결과를 낸다.

engineering-guide.md §8.3과 architecture.md §3 2단계("실패해도 진행"):
**실패해도 예외를 던지지 않고 `Evidence(ok=False, error=...)`를 반환한다.**
샌드박스 자체를 띄우지 못하는 경우에만 예외다 — Fake에는 그 경로가 없다.
"""

import contextlib
import errno
import hashlib
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from src.adapters.index_graph import source_lines
from src.ids import IdFactory, sha256_text
from src.interfaces.tool_runner import SandboxSpec, ToolArgs
from src.paths import PathEscape, safe_join
from src.schemas.common import Location
from src.schemas.enums import EvidenceKind
from src.schemas.evidence import Evidence
from src.schemas.run import CompileFlags

# 도구 이름 → 이 도구가 만드는 Evidence 종류. 여기 없는 도구는 실행이 거부된다
# (tool_policy 밖 호출을 조용히 성공시키지 않는다).
TOOL_KINDS: dict[str, EvidenceKind] = {
    "semgrep": EvidenceKind.STATIC,
    "clang-tidy": EvidenceKind.STATIC,
    "build": EvidenceKind.BUILD,
    "asan": EvidenceKind.SANITIZER,
    "gdb": EvidenceKind.CRASH,
    "poc": EvidenceKind.POC_RUN,
    "index": EvidenceKind.GRAPH,
}

# semgrep/clang-tidy가 잡았다고 볼 패턴. 실제 룰셋이 아니라 신호 유무를 만드는 최소 규칙이다.
_STATIC_PATTERNS = (
    re.compile(r"\bmemcpy\s*\([^,]+,[^,]+,\s*[a-zA-Z_]"),  # 길이가 상수가 아님
    re.compile(r"\b(gets|strcpy|sprintf)\s*\("),
)

# `sources()`가 훑을 확장자. **후보를 열거하는 목록이고 도구별로 좁히는 곳이 아니다** —
# `container_tools._plan_for()`가 `_COMPILE_SUFFIXES`/`_INDEX_SUFFIXES`로 다시 거른다.
# 그래서 `.inc`가 여기 있다고 clang에 넘어가지는 않는다. 반대로 여기서 빠지면 좁히는
# 쪽이 고를 후보 자체가 없어져, 인덱서가 볼 수 없는 파일이 조용히 생긴다.
_SOURCE_SUFFIXES = (".c", ".h", ".cc", ".cpp", ".inc", ".py")

# 도구 요청에 데이터로 싣는 입력 파일(`ToolArgs.input_bytes`)의 확장자. **분류되는 확장자가
# 아니어야 한다**(`preprocess._KIND_BY_SUFFIX`에 없다) — `--target`이 저장소 루트라
# `artifacts/`가 대상 트리 안에 있으면 `.bin`(→ BINARY) 같은 확장자는 이전 run의 입력이 다음
# run의 분석 대상이 된다(`orchestrator/poc_log.py` 모듈 docstring의 「이름」 절 — 같은 이유로
# `.poc`다).
INPUT_SUFFIX = ".poc"


def write_input(directory: Path, data: bytes) -> Path:
    """`data`를 `directory/input-<sha256 앞 16자>.poc`에 **새 파일로, 바이트로** 쓰고 경로를 준다.

    텍스트로 쓰면 Windows에서 개행이 번역된다(`poc_log` 모듈 docstring) — 되읽어 대조하고, 다르면
    파일을 지우고 `OSError`다(틀린 입력 파일은 없는 파일보다 나쁘다). **말단 링크를 따라가지
    않는다** — scratch는 RW 마운트이고 대상 코드가 거기서 돈다: 예측되는 이름에 링크를 심으면
    `write_bytes`가 마운트 밖 파일을 덮는다. 그래서 있던 항목을 `unlink`하고 `xb`(`O_EXCL`)로
    만든다 — 그사이 심긴 것은 남의 것이라 안 지우고 `FileExistsError`다(상위 디렉터리 링크는 못
    막는다). ⚠ `poc_log.PocLog.write`는 같은 선례가 **아니다** — 그쪽은 `unlink`를 앞세우지 않고
    연 뒤 신원 대조로 막는다(#565). 여기서 Windows의 끊어진 링크가 안 걸리는 것은 `O_EXCL` 덕이
    아니라 `unlink` 선행 덕이다. 예외 문구에 경로를 싣지 않는다 — 어댑터가 Evidence로
    올리고 관측으로 모델에 간다. 컨테이너 어댑터도 이 함수를 쓴다 — 쓰기 규칙이 갈라지지 않게.
    """
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"input-{hashlib.sha256(data).hexdigest()[:16]}{INPUT_SUFFIX}"
    path.unlink(missing_ok=True)
    handle = open(path, "xb")  # 그새 심겼으면 FileExistsError — 남의 것이라 안 지우고 올린다
    try:
        with handle:
            handle.write(data)
        if path.read_bytes() != data:
            raise OSError(errno.EIO, "되읽은 바이트가 쓴 입력과 다르다")
    except BaseException:
        with contextlib.suppress(OSError):
            path.unlink(missing_ok=True)
        raise
    return path


class SandboxUnavailable(RuntimeError):
    """Evidence조차 만들 수 없는 경우. Fake에서는 발생하지 않는다.

    `container_tools.ContainerToolRunner`가 런타임 부재에 이 예외를 쓴다 — 두 어댑터가
    같은 계약을 지키므로 예외 타입도 하나여야 호출부가 런타임별로 갈라지지 않는다.
    """


class FakeToolRunner:
    """`ToolRunner` 구현. 같은 workdir·argv에는 항상 같은 Evidence를 낸다."""

    def __init__(
        self,
        *,
        ids: IdFactory,
        run_id: str,
        root: Path,
        observed_at: datetime | None = None,
    ) -> None:
        self.ids = ids
        self.run_id = run_id
        # 샌드박스에 마운트되는 대상 루트. `ToolArgs.workdir`는 이 아래의 상대경로다
        # (engineering-guide.md §7 읽기 전용 마운트). 밖을 가리키면 실행하지 않는다.
        self.root = Path(root)
        # 시각을 주입받는다 — 재현 실행에서 같은 값이 나와야 trace를 대조할 수 있다.
        self.observed_at = observed_at or datetime(2026, 9, 1, 0, 0, 0, tzinfo=UTC)

    def run(self, tool: str, args: ToolArgs, sandbox: SandboxSpec) -> Evidence:
        kind = TOOL_KINDS.get(tool)
        if kind is None:
            return self.make_evidence(
                tool,
                EvidenceKind.BUILD,
                args,
                ok=False,
                exit_code=127,
                error=f"tool_policy 밖 도구: {tool!r}. 허용: {sorted(TOOL_KINDS)}",
            )
        if sandbox.network:
            # disclosure-policy.md §5: 외부 시스템 실행 금지. 조용히 통과시키면 정책이 무의미하다.
            return self.make_evidence(
                tool, kind, args, ok=False, exit_code=1, error="network=True는 허용되지 않는다"
            )
        try:
            workdir = safe_join(self.root, args.workdir)
        except PathEscape as exc:
            return self.make_evidence(tool, kind, args, ok=False, exit_code=1, error=str(exc))
        if args.input_bytes is not None:
            # 쓰고 바로 지운다 — Fake는 아무것도 실행하지 않아 이 파일을 읽는 것이 없다.
            # 컨테이너와 같은 쓰기(`write_input` — 바이트 보존, 실패는 Evidence)를 dev_fake도
            # 지나게 하려는 것이다. **대상 트리에 쓰지 않는다**: 읽기 전용 마운트 정책이고, 쓰면
            # 다음 run의 `preprocess`가 그 파일을 대상으로 센다. 자리표는 안 바꾼다 — Evidence
            # 본문이 argv를 쓰는데 임시 경로가 들어가면 같은 요청이 같은 digest를 내지 못한다.
            try:
                with tempfile.TemporaryDirectory(
                    prefix="harness-input-", ignore_cleanup_errors=True
                ) as scratch:
                    write_input(Path(scratch), args.input_bytes)
            except OSError as exc:
                return self.make_evidence(
                    tool,
                    kind,
                    args,
                    ok=False,
                    exit_code=2,
                    error=f"입력을 쓰지 못했다: {exc.strerror or type(exc).__name__}",
                )
        if tool in _STATIC_TOOLS:
            return self._run_static(tool, kind, args, workdir)
        if tool in _REPRO_TOOLS:
            return self._run_repro(tool, kind, args, workdir)
        return self._run_generic(tool, kind, args, workdir)

    def images(self) -> dict[str, str]:
        """컨테이너를 쓰지 않으므로 기록할 이미지가 없다.

        `factory.build_deps()`가 런타임 종류를 따지지 않고 이 메서드를 부른다. 없는 값을
        비워 두면 `runner.incomplete_fields()`가 "모델 비교표에서 제외한다"를 경고하는데,
        Fake로 돈 run에서는 그것이 옳은 기록이다.
        """
        return {}

    def compile_flags(self) -> CompileFlags:
        """컴파일을 하지 않으므로 기록할 컴파일 조건이 **없다** (#74).

        `images()`와 같은 자리이고 같은 이유로 **빈 값**이다 — 지어낸 `-std`가 들어가면
        Fake run과 컨테이너 run이 비교표에서 같은 컴파일 조건으로 보인다. 빈 값은
        *"기록했으나 컴파일한 도구가 없었다"*이고, `RunRecord.compile_flags`의 `None`
        (*"이 manifest는 남기지 않았다"*, 옛 기록)과 구별된다.
        """
        return CompileFlags(std={}, fixed_include_dirs=[])

    # --- 도구별 동작 ---------------------------------------------------------

    def _run_static(self, tool: str, kind: EvidenceKind, args: ToolArgs, workdir: Path) -> Evidence:
        hits = sum(
            1
            for path in sources(workdir)
            if _matches_any(path.read_text("utf-8", errors="replace"))
        )
        # 신호 없음은 실패가 아니다(architecture.md §3: "신호 부재로 기록").
        # **semgrep만** location을 싣는다 — 컨테이너의 `first_hit_location`이 semgrep 한정이라
        # 그쪽과 맞춘다(clang-tidy는 #70 후속 — 미착수, #507이 선행). 그러지 않으면 Fake가
        # clang-tidy에도 값을 실어
        # 두 어댑터의 location 계약이 갈린다.
        return self.make_evidence(
            tool,
            kind,
            args,
            ok=True,
            exit_code=1 if hits else 0,
            hits=hits,
            location=self._first_static_location(workdir) if tool == "semgrep" else None,
        )

    def _first_static_location(self, workdir: Path) -> Location | None:
        """첫 정적 매치의 위치 (#70 초석 — 컨테이너 `first_hit_location`의 Fake 짝).

        실제 도구가 없으므로 `_STATIC_PATTERNS`가 처음 걸린 **줄 번호**를 위치로 삼는다.
        파일은 `self.root` 기준 상대 POSIX 경로라 `Candidate.location.file`(artifact 경로)과
        같은 좌표계다. `hits`가 0이면 매치가 없어 자연히 `None`이다 — 게이트 등급은 이 값과
        무관하다(`p1 = static_ok or location_ok`, STATIC이 이미 `static_ok`).

        ⚠ **`workdir`은 `safe_join`이 resolve한 절대경로**인데 `self.root`는 resolve 전이라,
        `relative_to`가 그대로면 두 경로 형태가 어긋나 `ValueError`가 난다(상대 `--target`에서
        실측). 도구는 예외를 던지지 않는 계약이므로 root를 resolve하고, 그래도 밖이면 `None`.
        """
        root = self.root.resolve()
        for path in sources(workdir):
            text = path.read_text("utf-8", errors="replace")
            for lineno, line in enumerate(source_lines(text), start=1):
                if _matches_any(line):
                    try:
                        rel = path.resolve().relative_to(root).as_posix()
                    except ValueError:
                        return None
                    return Location(file=rel, line_start=lineno)
        return None

    def _run_repro(self, tool: str, kind: EvidenceKind, args: ToolArgs, workdir: Path) -> Evidence:
        """재현 실행. Fake에는 실행할 바이너리가 없으므로 대상 존재로 대체한다.

        ⚠ **`expected_signal`을 산출물과 대조하는 코드는 없다** — 8단계 노드가 그 문자열로
        하는 일은 도구 이름을 고르는 것뿐이다(`validate.tool_for()`). 게이트는 런타임 Evidence의
        종료 코드가 0도 `None`도 아닐 것을 요구하는데(#232) **여기서 내는 `exit_code=1`은 측정이
        아니라 상수다** — 그래서 `ok=True`가 그대로 E4가 된다.
        **「소스가 있다」가 `proven`이 된다.** 🔴 **fake는 입력 바이트를 대상에 안 먹인다** —
        `ok`·`exit_code`는 입력과 무관한 상수다(이 입력 배선은 관측만 바꾼다. 움직이면 E4의 뜻이
        움직인다). ⏵ **2026-10-09 (ADR 0014) — 컨테이너와 갈렸다**: 컨테이너의 `poc`는 과제
        `-vul` 이미지에 PoC를 먹여 **관측이 PoC의 함수**인데 fake는 Docker를 안 띄우므로 그럴 수
        없다. **그래서 `dev_fake` e2e는 그 사실을 재지 못한다** — 재는 것은
        `tests/test_poc_tool_feeds_the_input.py`이고, 이 비대칭 자체를 무는 것은 그 파일의
        `test_the_fake_runner_makes_the_same_claim_as_the_container`다.

        ⏵ 2026-10-13(#232) — **`Evidence.crashed`는 `None`(안 쟀다)으로 둔다.** 돌린 로그가 없으니
        배너를 찾을 수 없고, 상수 `exit_code=1`에서 「크래시했다」를 지어내면 안 잰 값이 잰 값처럼
        보인다(CLAUDE.md §0).
        """
        if not sources(workdir):
            return self.make_evidence(
                tool, kind, args, ok=False, exit_code=2, error="재현 대상 소스가 없다"
            )
        return self.make_evidence(tool, kind, args, ok=True, exit_code=1)

    def _run_generic(
        self, tool: str, kind: EvidenceKind, args: ToolArgs, workdir: Path
    ) -> Evidence:
        """build·index처럼 대상 존재만 확인하면 되는 도구."""
        if not workdir.exists():
            return self.make_evidence(
                tool, kind, args, ok=False, exit_code=2, error=f"workdir가 없다: {args.workdir}"
            )
        return self.make_evidence(tool, kind, args, ok=True, exit_code=0)

    def make_evidence(
        self,
        tool: str,
        kind: EvidenceKind,
        args: ToolArgs,
        *,
        ok: bool,
        exit_code: int,
        error: str | None = None,
        hits: int | None = None,
        location: Location | None = None,
    ) -> Evidence:
        evidence_id = self.ids.next("evidence")
        body = f"{tool} {' '.join(args.argv)} exit={exit_code} hits={hits}"
        return Evidence(
            evidence_id=evidence_id,
            run_id=self.run_id,
            kind=kind,
            source=tool,
            artifact_id=args.workdir,
            location=location,
            payload_ref=f"artifacts/{self.run_id}/{tool}/{evidence_id}.log",
            digest=sha256_text(body),
            observed_at=self.observed_at,
            ok=ok,
            exit_code=exit_code,
            error=error,
        )


def _matches_any(text: str) -> bool:
    return any(pattern.search(text) for pattern in _STATIC_PATTERNS)


def sources(workdir: Path) -> list[Path]:
    """정렬된 목록을 돌려준다 — rglob 순서는 파일시스템마다 달라 재현성을 깬다."""
    if workdir.is_file():
        return [workdir]
    if not workdir.exists():
        return []
    return sorted(p for p in workdir.rglob("*") if p.is_file() and p.suffix in _SOURCE_SUFFIXES)


_STATIC_TOOLS = frozenset({"semgrep", "clang-tidy"})
_REPRO_TOOLS = frozenset({"asan", "gdb", "poc"})
