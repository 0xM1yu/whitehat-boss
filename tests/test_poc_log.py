"""PoC 사이드카 — 루프가 선 자리의 **마지막 `gen` PoC**가 run 산출물에 파일로 남고, 그 파일과
trace가 서로를 가리킨다 (#355).

`test_prompt_log.py`의 핀을 미러한다: ref는 run_dir 상대이고 `run_id`를 안 품으며, digest는
**그 파일 내용**의 것이고, dry-run·예산 소진에서는 디렉터리조차 생기지 않는다.

## 이 파일이 문 것 — 한 번 살아남았던 변이 열한 개

리뷰어 둘이 새 코드에 한 줄짜리 변이를 걸었고 **열한 개가 스위트 전체를 통과했다.** 원인은 셋이다.

- **픽스처가 바이트를 못 흔든다.** `dev_fake`의 PoC는 `0x41`×64 + `EF BE AD DE`라 `0x0A`·`0x0D`가
  하나도 없다 — 개행 번역 변이가 파일을 바꿔도 digest(메모리 버퍼의 해시)와 대조값이 같이
  움직이지 않았다. 아래 바이트 표(`_BYTE_PATTERNS`)가 그 구멍을 막는다.
- **한 경로만 걸었다.** 새 테스트 일곱이 전부 `gen → submit`만 지났다. max_hop·repeat·hop1 실패
  종단과 쓰기 순번(파일 둘), 배선 가드의 `run_dir is not None` 절반은 비어 있었다.
- **이름·형식에 핀이 없었다.** 디렉터리가 도구 슬롯과 겹치는지, 확장자가 전처리기에 분류되는지,
  순번 폭, 남는 파일 목록 — 전부 무방비였다.

기대값은 `assemble_poc`·스크립트한 `PovSpec`에서 뽑는다(`test_nodes.test_run_pov_generates_and_
records_the_poc_byte_size`와 같은 방식) — 파일에서 다시 해시를 계산하는 검사는 동어반복이라
"digest가 파일과 맞나"만 보고 "파일이 조립 결과와 맞나"는 안 본다.
"""

import builtins
import dataclasses
import hashlib
import io
import os
import shutil
from pathlib import Path

import pytest

from src.adapters.container_tools import _REJECTED_SLOT, scratch_dir
from src.adapters.factory import build_deps
from src.adapters.fake_retriever import read_symbol_source
from src.adapters.fake_tools import TOOL_KINDS
from src.config import load_config
from src.ids import sha256_digest
from src.interfaces.model_client import ModelGatewayError, SchemaParseError, Usage
from src.orchestrator import poc_log, prompt_log, runner
from src.orchestrator.budget import BudgetExhausted, BudgetLedger
from src.orchestrator.context import RunContext
from src.orchestrator.nodes import pov, preprocess
from src.orchestrator.poc_assembly import assemble_poc
from src.orchestrator.poc_log import DIR_NAME, SUFFIX, PocLog, PocWriteError
from src.orchestrator.stages import Stage
from src.orchestrator.trace import EventKind, TraceWriter, canonical_line
from src.schemas.enums import ArtifactKind, PovStopReason
from src.schemas.pov import PovAction, PovSegment, PovSpec
from src.schemas.run import Budget

# `ctx`는 dev_fake로 배선된 픽스처, `_pov_state`는 finding+캡슐을 담은 state 헬퍼,
# `_run`은 파이프라인 한 번(run_dir·trace — `before`로 run_dir을 미리 만진다)이다 —
# test_pov_loop이 test_nodes에서 헬퍼를 끌어 쓰는 것과 같은 방식.
from tests.test_nodes import _pov_state, _root_of, ctx  # noqa: F401
from tests.test_pipeline_e2e import CONDITION, STARTED_AT, TARGET, _events, _run
from tests.test_pov_loop import _gen, _hops, _scripted
from tests.test_prompt_log import RUN_ID

_SUBMIT = PovAction(action="submit", rationale="done")
_INSPECT = PovAction(action="inspect", tool="asan", rationale="look")

# 디스크로 가는 길에서 **바뀔 수 있는** 바이트. 개행 번역(Windows 텍스트 모드 · #387)과 EOF/NUL
# 처리가 이 값들에서 갈린다. `dev_fake`의 PoC에는 이 중 어느 것도 없었다.
_BYTE_PATTERNS = {
    "lf": b"\n",
    "crlf": b"\r\n",
    "cr": b"\r",
    "cr-cr-lf": b"\r\r\n",
    "ctrl-z": b"\x1a",
    "nul": b"\x00",
    "ff": b"\xff",
    "mixed": b"A\nB\r\nC\x1a\x00\xff",
    "all-256": bytes(range(256)),
    "1-MiB": bytes(range(256)) * 4096,
}


def _wired(ctx: RunContext, run_dir: Path) -> RunContext:  # noqa: F811
    """사이드카만 배선한 문맥. 러너의 주입 자리(`run_dir`·`dry_run` 가드)는 파이프라인 검사가
    본다 — 여기서 손으로 주입하면 그 배선이 끊겨도 초록이기 때문이다."""
    return dataclasses.replace(ctx, pocs=PocLog(run_dir))


def _terminals(ctx: RunContext) -> list:  # noqa: F811
    """루프 종단 이벤트들(`POV_GENERATED` 또는 `POV_SKIPPED`)."""
    return [
        e for e in ctx.trace.events if e.kind in (EventKind.POV_GENERATED, EventKind.POV_SKIPPED)
    ]


def _terminal(ctx: RunContext):  # noqa: ANN202, F811
    """루프 종단 이벤트 하나."""
    terminals = _terminals(ctx)
    assert len(terminals) == 1, f"종단 이벤트가 하나가 아니다: {len(terminals)}"
    return terminals[0]


def _sidecar_keys(data: dict) -> list[str]:
    return [k for k in data if k.startswith(("poc_ref", "poc_digest", "poc_write"))]


def _listing(directory: Path) -> list[str]:
    """디렉터리에 **실제로 있는 이름 전부**(임시 파일 포함). 목록이 정확히 같아야 고아가 없다."""
    return sorted(p.name for p in directory.iterdir())


def _loop(
    ctx: RunContext,  # noqa: F811
    tmp_path: Path,
    actions: list[object],
    *,
    wired: bool = True,
    prepare=None,
):  # noqa: ANN001, ANN202
    """스크립트한 모델로 PoV 루프를 한 번 돈다 → (돌린 문맥, run_dir, 반환값).

    **트리아지는 실제 fake로 먼저 하고 그 뒤에 스크립트로 바꾼다** — 스크립트한 모델이 Auditor
    역할까지 받으면 `PovAction`을 `Finding`으로 읽으려다 죽는다(`test_pov_loop`이 같은 순서다).
    `prepare(run_dir)`는 루프 전에 run_dir을 만진다(쓰기 실패를 만들 때).
    """
    state, finding = _pov_state(ctx)
    capsule = state.finding_capsules[finding.finding_id]
    run_dir = tmp_path / RUN_ID
    if prepare is not None:
        run_dir.mkdir(parents=True, exist_ok=True)
        prepare(run_dir)
    scripted = _scripted(_wired(ctx, run_dir) if wired else ctx, actions)
    return scripted, run_dir, runner._run_pov_hops(scripted, state, finding, capsule)


# --- PocLog 단독 — 이름·순번·바이트 ------------------------------------------------


def test_files_are_numbered_in_write_order_and_each_keeps_its_own_bytes(tmp_path: Path) -> None:
    """순번이 올라가지 않으면(`self._seq = 1`) 두 번째가 첫 파일을 **덮어** PoC 하나가 사라진다.

    이름 형식도 **리터럴로** 고정한다 — 이 이름은 사람이 쥐는 인터페이스라(채점기 제출은 이 파일이
    아니라 메모리의 바이트를 낸다) 상수로만 검사하면 이름이 바뀌어도 초록이다. `prompt_log`의
    `test_files_are_numbered_in_call_order`가 같은 자리다.
    """
    log = PocLog(tmp_path)

    first, _ = log.write(b"first")
    second, _ = log.write(b"second")

    assert first == "pov_poc/0001.poc"
    assert second == "pov_poc/0002.poc"
    assert (tmp_path / first).read_bytes() == b"first"
    assert (tmp_path / second).read_bytes() == b"second"


def test_the_directory_holds_exactly_the_files_written_and_nothing_else(tmp_path: Path) -> None:
    """고아 파일(임시 파일 `*.tmp` 포함)이 없다 — 이름 목록이 **정확히** 같아야 한다.

    `*.poc`만 글로브하면 남은 `0001.poc.tmp`나 엉뚱한 파일을 못 본다(`test_prompt_log`의
    `*.md` 수 핀이 같은 구멍이다)."""
    log = PocLog(tmp_path)

    log.write(b"first")
    log.write(b"second")

    assert _listing(log.directory) == ["0001.poc", "0002.poc"]


@pytest.mark.parametrize("poc", list(_BYTE_PATTERNS.values()), ids=list(_BYTE_PATTERNS))
def test_every_byte_pattern_reads_back_exactly(tmp_path: Path, poc: bytes) -> None:
    """파일이 **조립한 바이트 그대로**이고 digest가 그 바이트의 것이다.

    텍스트 모드 쓰기(Windows에서 `\\n`→`\\r\\n`)나 명시적 치환(`poc.replace(b"\\n", b"\\r\\n")`)은
    이 표의 `\\n`·`\\r` 항목에서만 갈린다 — `dev_fake` PoC로는 안 보인다. 기대값은 **입력**이다.
    """
    ref, digest = PocLog(tmp_path).write(poc)

    assert (tmp_path / ref).read_bytes() == poc
    assert digest == sha256_digest(poc)


def test_an_empty_poc_is_still_written(tmp_path: Path) -> None:
    """0바이트 PoC도 **파일이다** — 거절하지도 건너뛰지도 않는다.

    `PovSegment`가 `repeat count=0`을 받으므로 0바이트 PoC는 도달 가능하고, libFuzzer 입력으로는
    유효한 답이다. 같은 이벤트가 이미 `poc_bytes=0`·`poc_sha256`을 싣는데 파일만 없으면 이벤트끼리
    어긋난다. 건너뛰는 쪽(`if poc:`)이 변이로 살아남았던 자리다(루프 경로는 아래 검사가 문다).
    """
    empty = assemble_poc(
        PovSpec(segments=[PovSegment(kind="repeat", byte=0x41, count=0)], rationale="empty")
    )
    assert empty == b"", "0바이트 PoC가 도달 불가면 이 검사의 전제가 사라진다"

    ref, digest = PocLog(tmp_path).write(empty)

    assert (tmp_path / ref).is_file()
    assert (tmp_path / ref).stat().st_size == 0
    assert digest == sha256_digest(b"")


def test_the_poc_is_written_to_a_temp_name_opened_exclusively_and_in_binary_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """쓰기용으로 여는 것이 **임시 이름**이고 모드가 정확히 `xb`다 — **배타(`x`)이고 이진(`b`)**.

    - **이진**: 개행 번역은 Windows에서만 일어난다. 그래서 위 바이트 표는 CI(Linux)에서 텍스트 모드
      쓰기를 못 잡는다 — `latin-1` 텍스트 쓰기는 거기서 바이트를 안 바꾼다(등가 변이). 이 검사가
      **모드 자체**를 고정해 그 구멍을 플랫폼과 무관하게 닫는다.
    - **임시 이름**: 쓰는 동안 디렉터리에 보이는 것은 `0001.poc.tmp`뿐이다 — 최종 이름 `0001.poc`는
      `os.replace`로만 나타난다. 최종 이름에 바로 쓰면(원자성 상실) 읽는 쪽이 반쯤 쓰인 PoC를 본다.
      쓰다가 죽는 것을 흉내 내지 않고도 **공개 시점**을 직접 문다.

    `io.open`도 같이 가로채므로 `Path.open`으로 바꿔도 이 검사는 그대로다(읽기 모드는 거른다).
    """
    modes: list[str] = []
    visible: list[list[str]] = []
    real_builtin, real_io = builtins.open, io.open

    def spy(real):  # noqa: ANN001, ANN202
        def opened(file, mode="r", *args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN202
            handle = real(file, mode, *args, **kwargs)
            if str(file).endswith(".tmp") and set(mode) & set("wxa+"):
                modes.append(mode)
                visible.append(sorted(os.listdir(Path(file).parent)))
            return handle

        return opened

    monkeypatch.setattr(builtins, "open", spy(real_builtin))
    monkeypatch.setattr(io, "open", spy(real_io))

    PocLog(tmp_path).write(bytes([0x61, 0x0A, 0x62]))

    monkeypatch.undo()
    assert modes == ["xb"], f"임시 파일을 연 쓰기 모드: {modes}"
    assert visible == [["0001.poc.tmp"]], f"쓰는 동안 보인 이름: {visible}"


# --- PocLog 단독 — 쓰기의 안전 ------------------------------------------------------

_CORRUPTIONS = {
    "lf_to_crlf": lambda data: data.replace(b"\n", b"\r\n"),
    "one_byte_flipped_same_length": lambda data: bytes([data[0] ^ 0xFF]) + data[1:],
    "truncated": lambda data: data[:-1],
}


@pytest.mark.parametrize("corrupt", list(_CORRUPTIONS.values()), ids=list(_CORRUPTIONS))
def test_a_file_that_does_not_read_back_as_the_assembled_bytes_is_never_published(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, corrupt
) -> None:  # noqa: ANN001
    """쓴 파일을 되읽어 **조립한 바이트와 대조**한다 — 다르면 파일도 ref도 내놓지 않는다.

    digest를 파일에서 계산하면(#387의 (b)) digest는 **그 파일에 대해 항상 참**이 되는데, 번역된
    바이트가 「의도된 내용」으로 굳는다 — 이 대조가 그 대가를 막는다. 길이가 같은 변이(한 바이트
    뒤집기)는 길이 비교를 통과하므로 내용을 직접 비교하는지도 여기서 본다. 되읽기는
    `Path.read_bytes`로 하고, 이 검사는 그 읽기를 오염시켜 「디스크가 다른 바이트를 돌려준다」를
    흉내 낸다(플랫폼과 무관하다).
    """
    real = Path.read_bytes
    monkeypatch.setattr(Path, "read_bytes", lambda self: corrupt(real(self)))
    log = PocLog(tmp_path)

    with pytest.raises(PocWriteError):
        log.write(b"a\nbcd")

    monkeypatch.undo()
    assert _listing(log.directory) == [], "실패한 쓰기가 최종 파일이나 임시 파일을 남겼다"


def test_an_existing_temp_path_is_refused_and_left_alone(tmp_path: Path) -> None:
    """임시 경로에 **이미 있는 것**은 덮지도 지우지도 않는다 — `x`(O_CREAT|O_EXCL) 모드다.

    우리가 만든 파일이 아니므로 실패 정리도 건드리지 않는다(정리가 `open` 앞의 예외까지 덮으면
    남의 파일을 지운다).
    """
    log = PocLog(tmp_path)
    log.directory.mkdir(parents=True)
    squatter = log.directory / "0001.poc.tmp"
    squatter.write_bytes(b"not ours")

    with pytest.raises(FileExistsError):
        log.write(b"poc")

    assert squatter.read_bytes() == b"not ours"
    assert _listing(log.directory) == ["0001.poc.tmp"]


def test_a_failed_attempt_takes_its_number_so_a_leftover_temp_file_never_blocks_the_next_write(
    tmp_path: Path,
) -> None:
    """**실패한 시도도 번호를 먹는다** — 그 번호의 임시 파일이 남아도 다음 쓰기가 막히지 않는다
    (`PocLog` docstring이 적은 성질이다). 번호를 **성공 뒤에만** 올리면 다음 쓰기가 같은 임시 자리
    (`0001.poc.tmp`)를 다시 노려 `FileExistsError`를 되풀이하고, 그 run의 이후 PoC는 전부 못 남는다.

    위 검사는 첫 쓰기의 거절만 본다 — 그 **다음** 쓰기는 안 본다. 첫 시도 때문에 번호 하나가
    비는 것(`0001.poc` 없음)은 의도다. 남의 임시 파일은 둘째 쓰기 뒤에도 그대로여야 한다.
    """
    log = PocLog(tmp_path)
    log.directory.mkdir(parents=True)
    leftover = log.directory / "0001.poc.tmp"
    leftover.write_bytes(b"left behind")

    with pytest.raises(FileExistsError):
        log.write(b"first")

    ref, _ = log.write(b"second")

    assert ref == "pov_poc/0002.poc"
    assert (tmp_path / ref).read_bytes() == b"second"
    assert leftover.read_bytes() == b"left behind"
    assert _listing(log.directory) == ["0001.poc.tmp", "0002.poc"]


def test_a_symlink_at_the_temp_path_is_not_followed(tmp_path: Path) -> None:
    """임시 경로의 심링크를 **따라가 대상 파일을 덮지 않는다** — 살아 있는 링크도 끊어진 링크도.

    Linux에서는 `O_EXCL`이 말단 심링크를 거절한다. Windows의 `CREATE_NEW`는 **끊어진** 링크를
    따라가므로 연 뒤 신원 대조(`_refuse_if_followed`)가 막는다(#565 — 수정 전 권한 있는 Windows에서
    이 검사가 둘째 바퀴에서 빨갰다). 심링크 권한이 없는 환경(일반 Windows)에서는 건너뛴다.
    """
    victim = tmp_path / "victim"
    victim.write_bytes(b"ORIGINAL")
    dangling = tmp_path / "does_not_exist"
    log = PocLog(tmp_path / "run")
    log.directory.mkdir(parents=True)
    try:
        os.symlink(victim, log.directory / "0001.poc.tmp")
        os.symlink(dangling, log.directory / "0002.poc.tmp")
    except (OSError, NotImplementedError):
        pytest.skip("심링크를 만들 권한이 없다")

    for _ in range(2):
        with pytest.raises(FileExistsError):
            log.write(b"CLOBBER")

    assert victim.read_bytes() == b"ORIGINAL"
    assert not dangling.exists(), "끊어진 링크의 대상이 만들어졌다 — 링크를 따라갔다"


def test_a_path_whose_identity_differs_from_the_opened_file_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """연 fd(`fstat`)와 경로 말단(`lstat`)의 신원이 다르면 **링크를 따라간 것**이라 거절한다(#565).

    Windows의 `CREATE_NEW`는 끊어진 링크를 따라가는데, 그 갈래는 심링크 권한이 있는 Windows에서만
    위 검사가 실제로 탄다(이 저장소 CI·권한 없는 장비에서는 skip). 그래서 `lstat`을 갈아 끼워 같은
    상황을 세운다 — 신원 대조를 지우는 변이를 이 검사가 잡는다. 말단은 **남의 것**으로 보이므로
    지우지 않는다(위 「남의 것은 안 지운다」와 같은 성질).
    """
    log = PocLog(tmp_path / "run")
    real_lstat = poc_log.os.lstat

    def lstat(path: object) -> os.stat_result:
        found = real_lstat(path)
        if str(path).endswith(".poc.tmp"):
            # 경로 말단이 우리가 연 파일이 아니다 — 링크를 따라갔다. 진짜 stat_result에서 신원만
            # 바꾼다(POSIX `os.path.realpath`가 같은 `lstat`을 불러 `st_mode`를 읽는다).
            fields = list(found[:10])
            fields[1], fields[2] = -1, -1  # st_ino, st_dev
            return os.stat_result(fields)
        return found

    monkeypatch.setattr(poc_log.os, "lstat", lstat)

    with pytest.raises(FileExistsError):
        log.write(b"CLOBBER")

    assert not (log.directory / "0001.poc").exists(), "거절했는데 최종 파일이 생겼다"
    assert (log.directory / "0001.poc.tmp").exists(), "남의 것으로 보이는 말단을 지웠다"


# --- 위치 — 다른 쓰는 쪽·전처리기와 겹치지 않는다 -------------------------------------


def test_the_directory_is_not_one_another_writer_already_owns() -> None:
    """디렉터리가 **도구 슬롯**이면 두 쓰는 쪽이 한 디렉터리를 나눈다.

    `ContainerToolRunner._write`가 `{run_dir}/{tool}/…`에 로그를 쓰고 `make_payload_reader`는
    접미사를 걸러 읽지 않는다 — 예전 이름 `poc/`가 정확히 `TOOL_KINDS`의 한 멤버였다.
    프롬프트 디렉터리와도 겹치지 않는다(그 아래 `*.md` 수가 모델 호출 수와 같다는 핀이 있다).
    """
    owned = {
        *TOOL_KINDS,
        _REJECTED_SLOT,
        scratch_dir(Path("a"), "run", "tool").parent.name,
        prompt_log.DIR_NAME,
    }

    assert DIR_NAME not in owned, f"{DIR_NAME!r}는 이미 다른 쓰는 쪽의 디렉터리다: {sorted(owned)}"


def test_the_suffix_is_one_the_preprocessor_never_analyzes(
    ctx: RunContext,  # noqa: F811
) -> None:
    """확장자가 `_KIND_BY_SUFFIX`에 있으면(예전 `.bin` → BINARY) 이전 run의 PoC가 **다음 run의
    분석 대상**이 된다 — 대상 트리 안에 `artifacts/`가 있으면(`--target`이 저장소 루트일 때) 그
    바이트가 Scout 프롬프트까지 간다(dev_fake로 실측: 후보가 되고 `prompts/…_scout.md`에 실렸다).
    한 run의 답이 다음 run의 입력이 되는 오염이다.

    분류 함수만이 아니라 **실제 스캔**(`preprocess.run`의 `rglob`)과 `_ANALYZABLE`까지 본다.
    """
    root = _root_of(ctx)
    decoy = root / "artifacts" / "run_20260101_0001" / DIR_NAME / f"0001{SUFFIX}"
    decoy.parent.mkdir(parents=True)
    decoy.write_bytes(b"STALE-POC\n")

    assert SUFFIX not in preprocess._KIND_BY_SUFFIX
    artifacts = preprocess.run(ctx, source_root=root)

    (found,) = [a for a in artifacts if a.path.endswith(SUFFIX)]
    assert found.kind is ArtifactKind.UNKNOWN
    assert found.kind not in runner._ANALYZABLE


def test_a_previous_runs_poc_inside_the_target_tree_never_reaches_a_prompt(
    tmp_path: Path,
) -> None:
    """위 검사의 **결과 쪽** — 파이프라인을 실제로 돌려 이전 run의 사이드카가 다음 run의 후보도
    프롬프트도 되지 않는지 본다. 확장자만 `.bin`으로 되돌려도 이 검사가 빨개진다(실측: 후보가 되고
    그 바이트가 `prompts/…_scout.md`에 실린다).
    """
    marker = "STALE-POC-MARKER-7f3a9c"
    target = tmp_path / "target"
    shutil.copytree(TARGET, target)
    decoy = target / "artifacts" / "run_20260101_0001" / DIR_NAME / f"0001{SUFFIX}"
    decoy.parent.mkdir(parents=True)
    decoy.write_bytes(marker.encode() + bytes([0x0A]))

    result, trace_path = _run(tmp_path, target=target)

    assert [c.location.file for c in result.candidates if DIR_NAME in c.location.file] == []
    prompts = sorted((trace_path.parent / "prompts").glob("*.md"))
    assert prompts, "프롬프트 기록이 없으면 이 검사가 아무것도 보지 않는다"
    leaked = [p.name for p in prompts if marker in p.read_text(encoding="utf-8", errors="replace")]
    assert leaked == [], f"이전 run의 PoC가 프롬프트에 실렸다: {leaked}"


# --- 루프 종단 — 모든 갈래가 마지막 gen의 PoC를 남긴다 ---------------------------------

_EXITS = (
    "submitted", "max_hop", "repeat_spec", "repeat_spec_after_another_gen", "absorbed_hop1_failure",
)


def _exit_case(pov_hops: int, name: str) -> tuple[list[object], PovStopReason, bytes]:
    """종단 갈래별 (모델 스크립트, 정지 사유, **마지막 gen이 조립하는 바이트**)."""
    return {
        # gen 둘 → submit: 파일은 **마지막** gen(5바이트)이다. 첫 gen(3바이트)이 아니다.
        "submitted": ([_gen(count=3), _gen(count=5), _SUBMIT], PovStopReason.SUBMITTED, b"A" * 5),
        # 모델이 매 홉 다른 gen만 낸다 — hop `pov_hops`의 gen(count=pov_hops+1)에서 선다.
        "max_hop": (
            [_gen(count=n) for n in range(1, pov_hops + 5)],
            PovStopReason.MAX_HOP,
            b"A" * (pov_hops + 1),
        ),
        "repeat_spec": ([_gen(count=8), _gen(count=8)], PovStopReason.REPEAT_SPEC, b"A" * 8),
        # A→B→A: hop2에서 선다. 파일은 **서게 한 gen**의 바이트(3)이지 직전 gen(5)이 아니다 — 그
        # gen이 「마지막 gen」이다. 끝의 `_gen(count=7)`은 이 경로가 아닐 때 닿는 자리(MAX_HOP)다.
        "repeat_spec_after_another_gen": (
            [_gen(count=3), _gen(count=5), _gen(count=3), _gen(count=7)],
            PovStopReason.REPEAT_SPEC,
            b"A" * 3,
        ),
        "absorbed_hop1_failure": (
            [_gen(count=8), SchemaParseError("bad at hop1")],
            PovStopReason.PARSE_FAILED,
            b"A" * 8,
        ),
    }[name]


@pytest.mark.parametrize("exit_name", _EXITS)
def test_every_exit_that_holds_a_poc_files_the_last_gen_and_names_it_in_the_event(
    ctx: RunContext,  # noqa: F811
    tmp_path: Path,
    exit_name: str,
) -> None:
    """submit만이 아니라 max_hop·repeat·흡수된 홉 실패도 **같은 파일·같은 키**를 낸다.

    「submit 때만 쓴다」·「SKIPPED 이벤트에서 키를 뗀다」는 변이가 새 테스트 일곱을 전부 통과했던
    것은 그 일곱이 `gen → submit` 한 경로만 지났기 때문이다. 종단 사유로 가르지 않는 것이 이
    설계의 요점이다(회수가 목적이다).
    """
    actions, reason, expected = _exit_case(ctx.config.thresholds.pov_tool_hops, exit_name)

    scripted, run_dir, result = _loop(ctx, tmp_path, actions)

    assert result == (expected, reason)  # 반환 모양은 2-튜플 그대로(호출부가 쥐고 있다)
    event = _terminal(scripted)
    submitted = reason is PovStopReason.SUBMITTED
    assert event.kind is (EventKind.POV_GENERATED if submitted else EventKind.POV_SKIPPED)
    data = event.data
    assert data["poc_ref"] == "pov_poc/0001.poc"
    assert (run_dir / data["poc_ref"]).read_bytes() == expected
    assert data["poc_digest"] == sha256_digest(expected)
    assert data["poc_digest"] == f"sha256:{data['poc_sha256']}"
    assert data["poc_bytes"] == len(expected)
    assert _listing(run_dir / DIR_NAME) == ["0001.poc"], "루프당 파일은 정확히 하나다"


_NO_POC_EXITS = {
    "inspect_then_submit": ([_INSPECT, _SUBMIT], PovStopReason.SUBMITTED),
    "hop0_parse_failure": ([SchemaParseError("bad at hop0")], PovStopReason.PARSE_FAILED),
}


@pytest.mark.parametrize("actions, reason", list(_NO_POC_EXITS.values()), ids=list(_NO_POC_EXITS))
def test_an_exit_with_no_gen_writes_no_file_and_no_sidecar_keys(
    ctx: RunContext,  # noqa: F811
    tmp_path: Path,
    actions: list[object],
    reason: PovStopReason,
) -> None:
    """gen이 한 번도 없으면 쓸 바이트가 없다 — 빈 자리표시자 파일도, `None` 값 키도 없다."""
    scripted, run_dir, result = _loop(ctx, tmp_path, actions)

    assert result == (None, reason)
    assert _sidecar_keys(_terminal(scripted).data) == []
    assert not (run_dir / DIR_NAME).exists()


def test_two_loops_through_one_writer_leave_two_intact_files(
    ctx: RunContext,  # noqa: F811
    tmp_path: Path,
) -> None:
    """한 run의 finding들은 **같은 `PocLog`**를 쓴다 — 순번이 안 오르면 둘째가 첫째를 덮는다.

    단위 검사(`test_files_are_numbered_…`)와 별개로 **루프를 거쳐서** 둘을 본다: 종단 이벤트가
    각자의 ref를 싣고, 두 파일이 각자의 바이트를 지킨다.
    """
    state, finding = _pov_state(ctx)
    capsule = state.finding_capsules[finding.finding_id]
    run_dir = tmp_path / RUN_ID
    wired = _wired(ctx, run_dir)

    for count in (3, 5):
        scripted = _scripted(wired, [_gen(count=count), _SUBMIT])
        runner._run_pov_hops(scripted, state, finding, capsule)

    refs = [e.data["poc_ref"] for e in _terminals(wired)]
    assert refs == ["pov_poc/0001.poc", "pov_poc/0002.poc"]
    assert [(run_dir / ref).read_bytes() for ref in refs] == [b"A" * 3, b"A" * 5]
    assert _listing(run_dir / DIR_NAME) == ["0001.poc", "0002.poc"]


def test_a_poc_full_of_newline_bytes_survives_the_whole_loop(
    ctx: RunContext,  # noqa: F811
    tmp_path: Path,
) -> None:
    """바이트 표(`_BYTE_PATTERNS`)는 `PocLog` 단독이다 — 이쪽은 **루프를 통과한** 같은 핀이다.

    스펙의 `literal` 세그먼트로 `0A 0D 0A 1A 00 FF 0A`를 낸다. 파일·digest·이벤트의
    `poc_sha256`이 **같은 바이트**를 가리켜야 한다.
    """
    nasty = PovAction(
        action="gen",
        spec=PovSpec(segments=[PovSegment(kind="literal", hex="0a0d0a1a00ff0a")], rationale="nl"),
        rationale="newline bytes",
    )

    scripted, run_dir, (poc, _) = _loop(ctx, tmp_path, [nasty, _SUBMIT])

    assert poc == bytes.fromhex("0a0d0a1a00ff0a")
    data = _terminal(scripted).data
    assert (run_dir / data["poc_ref"]).read_bytes() == poc
    assert data["poc_digest"] == f"sha256:{hashlib.sha256(poc).hexdigest()}"


def test_a_zero_byte_poc_is_filed_when_the_model_submits_it(
    ctx: RunContext,  # noqa: F811
    tmp_path: Path,
) -> None:
    """`PovSegment(repeat, count=0)`로 낸 빈 PoC가 submit되면 **0바이트 파일**과 ref가 남는다.

    `if poc:`(참값 검사)로 쓰면 빈 PoC만 조용히 빠져 이벤트는 `poc_bytes=0`을 싣는데 파일은 없다.
    """
    empty = PovAction(
        action="gen",
        spec=PovSpec(segments=[PovSegment(kind="repeat", byte=0x41, count=0)], rationale="empty"),
        rationale="empty input",
    )

    scripted, run_dir, result = _loop(ctx, tmp_path, [empty, _SUBMIT])

    assert result == (b"", PovStopReason.SUBMITTED)
    data = _terminal(scripted).data
    assert data["poc_bytes"] == 0
    assert (run_dir / data["poc_ref"]).stat().st_size == 0
    assert data["poc_digest"] == sha256_digest(b"")
    # gen 홉 이벤트도 빈 PoC의 해시를 싣는다 — 참값 검사(`if poc:`)면 이 홉만 키가 빠진다.
    assert _hops(scripted)[0].data["poc_sha256"] == hashlib.sha256(b"").hexdigest()


# --- 배선이 없을 때 — 키를 늘리지 않는다 ------------------------------------------------


@pytest.mark.parametrize(
    "actions",
    [[_gen(count=8), _SUBMIT], [_gen(count=8), _gen(count=8)]],
    ids=["generated", "skipped_with_partial_poc"],
)
def test_an_unwired_run_adds_none_of_the_sidecar_keys(
    ctx: RunContext,  # noqa: F811
    tmp_path: Path,
    actions: list[object],
) -> None:
    """`ctx.pocs`가 없으면(단위 테스트·`--dry-run`) 종단 이벤트에 **키 자체가 없다** — `None` 값
    키도 아니다. `test_prompt_log.test_unwired_context_leaves_model_call_untouched`가 같은 규칙이다:
    `poc_ref=None`을 늘 실으면 「기록 장치가 없던 run」과 「PoC를 못 쓴 run」이 구별되지 않는다.
    원래 키(`poc_bytes`·`poc_sha256`)는 그대로 있다."""
    scripted, run_dir, _ = _loop(ctx, tmp_path, actions, wired=False)

    data = _terminal(scripted).data
    assert {"poc_bytes", "poc_sha256"} <= set(data)
    assert _sidecar_keys(data) == []
    assert not run_dir.exists()


# --- 쓰기 실패 — run을 죽이지 않는다 -----------------------------------------------------


def _block_the_directory(run_dir: Path) -> None:
    """`pov_poc/` 자리를 **파일**이 막게 한다 — `mkdir`이 `FileExistsError`(OSError)로 죽는다.
    모킹 없이 만드는 가장 싼 실제 쓰기 실패다."""
    (run_dir / DIR_NAME).write_bytes(b"in the way")


def test_a_failed_sidecar_write_is_absorbed_and_recorded_on_the_terminal_event(
    ctx: RunContext,  # noqa: F811
    tmp_path: Path,
) -> None:
    """PoV는 **추가형**이다 — 사이드카는 부가 산출물이라 쓰기 실패가 finding(=run)을 죽이면 안 된다
    (「캡슐 미스는 finding을 죽이지 않는다」가 같은 선례다).

    반환값(`(poc, reason)`)·종단 이벤트의 **종류**·`poc_bytes`/`poc_sha256`은 그대로이고, 실패는
    `poc_write_error`로 이벤트에 남는다. `poc_ref`는 **없다**(안 쓴 파일을 가리키지 않는다).
    """
    scripted, run_dir, result = _loop(
        ctx, tmp_path, [_gen(count=8), _SUBMIT], prepare=_block_the_directory
    )

    assert result == (b"A" * 8, PovStopReason.SUBMITTED)
    event = _terminal(scripted)
    assert event.kind is EventKind.POV_GENERATED  # 종단 사유는 파일 쓰기에 흔들리지 않는다
    assert event.data["poc_write_error"] == "FileExistsError"
    detail = event.data["poc_write_error_detail"]
    assert detail, "사유 문구가 비었다"
    # `str(exc)`는 파일의 **절대경로**를 싣는다 — trace는 경로를 안 싣는다(재현성 비교 단위).
    assert str(tmp_path) not in detail and tmp_path.name not in detail, f"경로가 샌다: {detail!r}"
    assert "poc_ref" not in event.data and "poc_digest" not in event.data
    assert event.data["poc_bytes"] == 8
    assert event.data["poc_sha256"] == hashlib.sha256(b"A" * 8).hexdigest()
    assert (run_dir / DIR_NAME).read_bytes() == b"in the way", "막고 있던 것을 건드렸다"


def test_a_corrupted_write_is_absorbed_like_any_other_write_failure(
    ctx: RunContext,  # noqa: F811
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """되읽기 대조 실패(`PocWriteError`)도 같은 길로 흡수된다 — 틀린 파일은 안 내놓고 run은 간다."""
    real = Path.read_bytes
    monkeypatch.setattr(Path, "read_bytes", lambda self: real(self) + b"!")

    scripted, run_dir, result = _loop(ctx, tmp_path, [_gen(count=8), _SUBMIT])

    monkeypatch.undo()
    assert result == (b"A" * 8, PovStopReason.SUBMITTED)
    data = _terminal(scripted).data
    assert data["poc_write_error"] == "PocWriteError"
    assert data["poc_write_error_detail"], "사유 문구가 비었다"
    assert "poc_ref" not in data
    assert _listing(run_dir / DIR_NAME) == []


def test_pov_finish_hands_back_the_ref_and_digest_it_wrote(
    ctx: RunContext,  # noqa: F811
    tmp_path: Path,
) -> None:
    """`_pov_finish`는 `(poc, reason, ref, digest)`를 낸다 — 파일 참조가 필요해질 때 **디렉터리를
    글로브하지 않고** 쓰게(지금 읽는 곳은 없다). 배선이 없거나 바이트가 없거나 쓰기가 실패하면
    ref·digest는 `None`이다."""
    _, finding = _pov_state(ctx)
    poc = b"\x00\n\xff"
    wired = _wired(ctx, tmp_path / RUN_ID)

    written = runner._pov_finish(wired, finding, PovStopReason.SUBMITTED, poc)
    assert written == (poc, PovStopReason.SUBMITTED, "pov_poc/0001.poc", sha256_digest(poc))

    unwired = runner._pov_finish(ctx, finding, PovStopReason.SUBMITTED, poc)
    assert unwired == (poc, PovStopReason.SUBMITTED, None, None)
    no_poc = runner._pov_finish(wired, finding, PovStopReason.MAX_HOP, None)
    assert no_poc == (None, PovStopReason.MAX_HOP, None, None)
    blocked = tmp_path / "blocked"
    blocked.mkdir()
    _block_the_directory(blocked)
    failed = runner._pov_finish(_wired(ctx, blocked), finding, PovStopReason.MAX_HOP, poc)
    assert failed == (poc, PovStopReason.MAX_HOP, None, None)


def test_only_oserror_is_absorbed_a_bug_in_the_writer_still_surfaces(
    ctx: RunContext,  # noqa: F811
) -> None:
    """흡수하는 것은 **`OSError`뿐**이다. 디스크가 못 쓴 것은 부가 산출물의 실패이지만 코드 결함
    (`TypeError`·`RuntimeError` …)을 삼키면 사이드카 버그가 「PoC가 안 남는 run」으로 조용히 바뀐다
    — `except Exception`으로 넓힌 변이가 살아남지 않게 한다."""

    class Broken:
        def write(self, poc: bytes) -> tuple[str, str]:
            raise RuntimeError("bug in the writer")

    _, finding = _pov_state(ctx)
    broken = dataclasses.replace(ctx, pocs=Broken())

    with pytest.raises(RuntimeError, match="bug in the writer"):
        runner._pov_finish(broken, finding, PovStopReason.SUBMITTED, b"A")


def test_an_interrupt_during_the_write_leaves_no_temp_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """정리는 `except BaseException`이다 — Ctrl-C(`KeyboardInterrupt`)도 임시 파일을 남기지 않는다.
    `Exception`으로 좁히면 중단된 run의 `pov_poc/`에 `0001.poc.tmp`가 고아로 남는다."""

    def interrupted(self: Path) -> bytes:
        raise KeyboardInterrupt

    monkeypatch.setattr(Path, "read_bytes", interrupted)
    log = PocLog(tmp_path)

    with pytest.raises(KeyboardInterrupt):
        log.write(b"poc")

    monkeypatch.undo()
    assert _listing(log.directory) == []


@pytest.mark.parametrize("cause", ["ctrl_c", "readback_mismatch"])
def test_a_failing_cleanup_never_masks_the_exception_that_caused_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cause: str
) -> None:
    """정리(`unlink`)가 `OSError`로 실패해도 호출자가 보는 것은 **쓰기를 깬 원래 예외**다.

    정리의 흡수(`contextlib.suppress(OSError)`)를 빼면 정리의 `PermissionError`가 원래 예외를
    **대체**한다. Ctrl-C(`KeyboardInterrupt`)가 `OSError`로 바뀌면 `_pov_finish`의
    `except OSError`가 그것을 쓰기 실패로 흡수해 중단이 삼켜진다 — 위 검사는 정리가 **성공하는**
    경우만 봐서 이 흡수를 못 물었다. 정리가 실제로 시도됐는지(`attempts`)도 본다: 시도가 없으면
    이 검사는 아무것도 안 본다.
    """
    real = Path.read_bytes
    attempts: list[str] = []

    def interrupted(self: Path) -> bytes:
        raise KeyboardInterrupt

    def mismatched(self: Path) -> bytes:
        return real(self) + b"!"

    def refused(self: Path, missing_ok: bool = False) -> None:
        attempts.append(self.name)
        raise PermissionError("cleanup refused")

    expected = KeyboardInterrupt if cause == "ctrl_c" else PocWriteError
    monkeypatch.setattr(Path, "read_bytes", interrupted if cause == "ctrl_c" else mismatched)
    monkeypatch.setattr(Path, "unlink", refused)

    with pytest.raises(expected):
        PocLog(tmp_path).write(b"poc")

    monkeypatch.undo()
    assert attempts == ["0001.poc.tmp"], f"정리가 시도되지 않았다: {attempts}"


# --- 중단 경로 — 파일은 없고, 해시 흔적은 gen 홉의 이벤트가 남긴다(#394) ---------------


def test_gen_hop_events_carry_the_poc_sha256_and_other_hops_do_not(
    ctx: RunContext,  # noqa: F811
    tmp_path: Path,
) -> None:
    """`pov_hop`(gen)이 그 홉에서 조립한 PoC의 **맨 hex sha256**을 싣는다 — 종단 이벤트의
    `poc_sha256`과 같은 형식이라 바로 대조된다. inspect·submit 홉에는 PoC가 없으므로 키가 없다."""
    scripted, _, _ = _loop(ctx, tmp_path, [_gen(count=3), _INSPECT, _gen(count=5), _SUBMIT])

    hops = _hops(scripted)
    assert [h.data["action"] for h in hops] == ["gen", "inspect", "gen", "submit"]
    assert [h.data.get("poc_sha256") for h in hops] == [
        hashlib.sha256(b"A" * 3).hexdigest(),
        None,
        hashlib.sha256(b"A" * 5).hexdigest(),
        None,
    ]
    assert "poc_sha256" not in hops[1].data and "poc_sha256" not in hops[3].data


@pytest.mark.parametrize(
    "exit_name", ["max_hop", "repeat_spec", "repeat_spec_after_another_gen"]
)
def test_the_stopping_gen_hop_event_also_carries_the_poc_sha256(
    ctx: RunContext,  # noqa: F811
    tmp_path: Path,
    exit_name: str,
) -> None:
    """루프를 **세우는** gen 홉(max_hop·repeat)의 이벤트에도 해시가 실린다 — 정지 이벤트만 따로
    빼먹기 쉬운 자리다. 종단 이벤트의 `poc_sha256`과 같아야 한다 — A→B→A로 서는 repeat도 서게 한
    gen(A)의 해시이지 직전 gen(B)의 것이 아니다."""
    actions, reason, expected = _exit_case(ctx.config.thresholds.pov_tool_hops, exit_name)

    scripted, _, _ = _loop(ctx, tmp_path, actions)

    last = _hops(scripted)[-1]
    assert last.data["action"] == "gen" and last.data["stop_reason"] == reason.value
    assert last.data["poc_sha256"] == hashlib.sha256(expected).hexdigest()
    assert last.data["poc_sha256"] == _terminal(scripted).data["poc_sha256"]


def _tight_budget(ctx: RunContext, tokens: int = 1) -> RunContext:  # noqa: F811
    """`tokens`번째 모델 호출(`_ScriptedModel`의 usage 홉당 1토큰)이 입력 토큰 상한에 닿는 원장 —
    기본 1이면 hop0, 2면 hop1의 도구 호출이 `ensure`에서 끊긴다."""
    ledger = BudgetLedger(Budget(input_tokens=tokens, output_tokens=0, gpu_seconds=0), ctx.trace)
    return dataclasses.replace(ctx, ledger=ledger)


_LOST = hashlib.sha256(b"A" * 8).hexdigest()


def _tool_calls(ctx: RunContext) -> list:  # noqa: F811
    """`tool_call` 이벤트들 — 픽스처 준비(build 등)가 낸 것도 든다. 그래서 전후로 견준다."""
    return [e for e in ctx.trace.events if e.kind is EventKind.TOOL_CALL]


def test_a_gateway_abort_at_the_next_hop_files_nothing_but_the_hop_event_names_the_lost_poc(
    ctx: RunContext,  # noqa: F811
    tmp_path: Path,
) -> None:
    """**알려진 한계를 고정한다.** 서버 쪽 게이트웨이 오류(status 없음·5xx)는 `_pov_finish`를
    건너뛰고 run으로 올라가므로 그 루프의 PoC는 **파일도 종단 이벤트도 없다**(`poc_log` 모듈
    docstring의 「한계」). 이 경로에서는 gen 홉의 이벤트가 이미 나간 뒤라 그 `poc_sha256`이
    잃은 PoC를 특정한다.

    이 한계를 **없애는** 변경이면(중단 경로에서도 쓴다) 첫 단언을 뒤집고 docstring을 같이 고친다
    — 실패 메시지가 그 안내다.
    """
    state, finding = _pov_state(ctx)
    capsule = state.finding_capsules[finding.finding_id]
    run_dir = tmp_path / RUN_ID
    down = ModelGatewayError("server down", status=None)
    scripted = _scripted(_wired(ctx, run_dir), [_gen(count=8), down])

    with pytest.raises(ModelGatewayError):
        runner._run_pov_hops(scripted, state, finding, capsule)

    assert not (run_dir / DIR_NAME).exists(), (
        "중단 경로가 PoC를 남기기 시작했다 — 한계가 풀렸으면 poc_log docstring의 「한계」와 "
        "이 검사를 같이 고친다"
    )
    # 종단 이벤트가 없다 — 오라클이 없는 이 픽스처에선 hop 이벤트가 유일한 흔적이다
    assert not _terminals(scripted)
    (gen_hop,) = [h for h in _hops(scripted) if h.data["action"] == "gen"]
    assert gen_hop.data["poc_sha256"] == _LOST


def test_a_budget_abort_inside_the_gen_hops_own_tool_call_still_names_the_lost_poc(
    ctx: RunContext,  # noqa: F811
    tmp_path: Path,
) -> None:
    """**#394 — 위 경로보다 흔한 쪽도 해시를 남긴다.** 토큰 예산은 **그 홉 자신의 모델 호출**이
    소진시키는 것이 보통이고, 그러면 같은 홉의 `call_tool`이 `ensure`에서 `BudgetExhausted`를
    낸다 — gen PoC를 조립한 **뒤**, 홉 이벤트를 내기 **전**이다. 루프가 `run_tool` 둘레에서 그것을
    받아 그 홉의 `pov_hop`(`poc_sha256`·`error`)을 내고 **같은 예외를** 다시 던진다.

    전에는 이 검사가 「이벤트 0건 · 해시 부재」를 고정했다(알려진 구멍). 파일은 여전히 없다 —
    종단(`_pov_finish`)을 안 부르는 것은 그대로다(`poc_log` 모듈 docstring 「한계」).
    원장은 그대로다 — `ensure`가 막은 호출은 도구를 안 돌렸으므로 `tool_call`도 청구도 없다.
    """
    state, finding = _pov_state(ctx)
    capsule = state.finding_capsules[finding.finding_id]
    run_dir = tmp_path / RUN_ID
    scripted = _scripted(_wired(_tight_budget(ctx), run_dir), [_gen(count=8), _SUBMIT])
    calls_before = _tool_calls(scripted)
    tool_ms_before = scripted.ledger.tool_ms

    with pytest.raises(BudgetExhausted):
        runner._run_pov_hops(scripted, state, finding, capsule)

    assert not (run_dir / DIR_NAME).exists()
    assert not _terminals(scripted)
    (hop,) = _hops(scripted)
    assert hop.data["hop"] == 0 and hop.data["action"] == "gen"
    assert hop.data["poc_sha256"] == _LOST
    assert hop.data["error"] == "BudgetExhausted"
    assert hop.data["schema_ok"] is True  # 모델 응답은 파싱됐다 — 끊긴 것은 도구 호출이다
    assert "stop_reason" not in hop.data  # `PovStopReason`에는 예산 소진 값이 없다(올려 보낸다)
    assert "tool" not in hop.data  # 도구는 안 돌았다
    # 도구가 안 돌았고 원장은 hop0 모델 호출의 1토큰만 들었다 — 이중 청구·청구 누락 없음.
    assert _tool_calls(scripted) == calls_before
    assert scripted.ledger.input_tokens == 1  # 원장은 이 검사가 새로 만든 것이다(`_tight_budget`)
    assert scripted.ledger.tool_ms == tool_ms_before
    # 순서 — 원장의 **차단** 기록이 먼저, 그 홉 이벤트가 뒤다(이벤트는 예외를 받은 뒤 낸다).
    # 첫 BUDGET은 픽스처 triage의 charge라 `blocked`로 가른다.
    events = scripted.trace.events
    blocked = next(i for i, e in enumerate(events) if e.data.get("event") == "blocked")
    assert blocked < events.index(hop)
    # hop0 이벤트는 정상 hop0처럼 진입점 조회 결과(`fuzz_*`)를 싣는다.
    assert "fuzz_found" in hop.data
    # 경과는 휘발 값이라 `data`가 아니라 최상위 `wall_ms`다(D14).
    assert "latency_s" not in hop.data and hop.wall_ms is not None


def test_a_budget_abort_at_hop1_names_hop1s_poc_and_joins_on_hop1(
    ctx: RunContext,  # noqa: F811
    tmp_path: Path,
) -> None:
    """**hop≥1 gen의 도구 호출이 끊기면 그 홉의 PoC 해시를 싣는다** — 앞 홉 것이 아니다. 「이 홉의
    PoC」와 「앞 홉의 PoC」가 갈리는 유일한 자리라, hop0 검사만으로는 앞 gen의 해시(`last_poc`)를
    싣거나 `hop`을 0으로 박거나 해시를 빼는 결함이 모두 살아남는다. `fuzz_*`는 hop0 전용이다."""
    state, finding = _pov_state(ctx)
    capsule = state.finding_capsules[finding.finding_id]
    scripted = _scripted(_tight_budget(ctx, 2), [_gen(count=8), _gen(count=9), _SUBMIT])

    with pytest.raises(BudgetExhausted):
        runner._run_pov_hops(scripted, state, finding, capsule)

    first, cut = _hops(scripted)
    assert first.data["poc_sha256"] == _LOST and "error" not in first.data
    assert cut.data["hop"] == 1 and cut.data["action"] == "gen"  # hop0이 아니다 - 조인 키
    assert cut.data["poc_sha256"] == hashlib.sha256(b"A" * 9).hexdigest()
    assert cut.data["poc_sha256"] != _LOST
    assert cut.data["error"] == "BudgetExhausted"
    assert "fuzz_found" not in cut.data  # 진입점 조회는 hop0만의 것이다
    assert not _terminals(scripted)


def test_an_abort_of_any_other_type_inside_the_tool_call_leaves_no_hop_event(
    ctx: RunContext,  # noqa: F811
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**알려진 구멍을 고정한다**(`poc_log` 모듈 docstring 「한계」) — 도구 호출 둘레의 `except`는
    `BudgetExhausted`만 받는다. `BudgetExhausted` 밖의 형은 그 홉의 이벤트 없이 그대로 올라간다.
    넓히면(예: `Exception`) 이 검사가 빨개진다 - 그때는 docstring과 같이 고친다."""
    state, finding = _pov_state(ctx)
    capsule = state.finding_capsules[finding.finding_id]
    scripted = _scripted(_wired(ctx, tmp_path / RUN_ID), [_gen(count=8), _SUBMIT])

    def broken(*args: object, **kwargs: object) -> None:
        raise PermissionError("denied")

    monkeypatch.setattr(pov, "run_tool", broken)

    with pytest.raises(PermissionError):
        runner._run_pov_hops(scripted, state, finding, capsule)

    assert not _hops(scripted)


def _truncated_hop_event(ctx: RunContext, state, finding, step: int) -> str:  # noqa: F811
    """시계가 `step`ms씩 가는 문맥(눈금만 다른 두 run을 흉내낸다)으로 hop0 절단을 **새 trace**에서
    한 번 돌려 그 `pov_hop`의 canonical 줄을 낸다. 상태(`finding`)는 호출자가 하나를 공유한다."""
    ticks = iter(range(0, 10**9, step))
    trace = TraceWriter(RUN_ID)
    ledger = BudgetLedger(Budget(input_tokens=1, output_tokens=0, gpu_seconds=0), trace)
    timed = dataclasses.replace(ctx, monotonic_ms=lambda: next(ticks), trace=trace, ledger=ledger)
    scripted = _scripted(timed, [_gen(count=8), _SUBMIT])
    with pytest.raises(BudgetExhausted):
        runner._run_pov_hops(scripted, state, finding, state.finding_capsules[finding.finding_id])
    (hop,) = _hops(scripted)
    return canonical_line(hop)


def test_the_truncated_hop_event_is_byte_identical_when_only_the_clock_differs(
    ctx: RunContext,  # noqa: F811
) -> None:
    """**Fake 경로의 바이트 동일 계약**(D14 — 비교에서 빼는 것은 최상위 `ts`·`wall_ms`뿐): 시계
    눈금만 다른 두 절단 run의 `pov_hop`은 canonical 줄이 같다. 경과를 `data`에 실으면(`latency_s`)
    이 둘이 갈린다. 시계를 0으로 고정한 `ctx` 그대로면 이 성질이 안 보인다(`test_nodes` 픽스처)."""
    state, finding = _pov_state(ctx)

    assert _truncated_hop_event(ctx, state, finding, 1) == _truncated_hop_event(
        ctx, state, finding, 7
    )


@pytest.mark.parametrize("after_gen", [False, True], ids=["inspect_at_hop0", "inspect_after_gen"])
def test_a_budget_abort_inside_an_inspect_hops_tool_call_also_emits_its_hop(
    ctx: RunContext,  # noqa: F811
    tmp_path: Path,
    after_gen: bool,
) -> None:
    """inspect 홉의 도구 호출이 끊겨도 같은 자리라 그 홉의 이벤트가 나간다 — 그 홉이 조립한 PoC가
    없으니 `poc_sha256`은 없다(gen 홉에만 싣는다는 위 검사와 같은 규칙). inspect가 도구에 싣는
    것은 **직전 gen의** PoC이지만 그 해시는 직전 gen 홉 이벤트에 이미 있다 — 여기 또 실으면
    「이 홉이 조립했다」로 읽힌다(`after_gen`이 그 변이를 문다).

    `after_gen`이면 예산이 hop1의 모델 호출에서 닿도록 상한을 2토큰으로 둔다(홉당 usage 1토큰).
    """
    state, finding = _pov_state(ctx)
    capsule = state.finding_capsules[finding.finding_id]
    run_dir = tmp_path / RUN_ID
    tokens = 2 if after_gen else 1
    ledger = BudgetLedger(Budget(input_tokens=tokens, output_tokens=0, gpu_seconds=0), ctx.trace)
    actions = [_gen(count=8), _INSPECT, _SUBMIT] if after_gen else [_INSPECT, _SUBMIT]
    scripted = _scripted(_wired(dataclasses.replace(ctx, ledger=ledger), run_dir), actions)

    with pytest.raises(BudgetExhausted):
        runner._run_pov_hops(scripted, state, finding, capsule)

    *before, hop = _hops(scripted)
    assert [h.data["action"] for h in before] == (["gen"] if after_gen else [])
    assert hop.data["action"] == "inspect" and hop.data["error"] == "BudgetExhausted"
    assert hop.data["hop"] == (1 if after_gen else 0)  # 조인 키
    assert "poc_sha256" not in hop.data
    if after_gen:
        assert before[0].data["poc_sha256"] == _LOST and "error" not in before[0].data
    assert not _terminals(scripted)


def test_a_failing_hop_event_does_not_mask_the_budget_abort(
    ctx: RunContext,  # noqa: F811
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """이벤트 쓰기가 던져도 **올라가는 것은 원래의 `BudgetExhausted`다** — run이 멈추는 사유가
    바뀌면 `run_pipeline`의 예산 갈래(경고·보고서)를 건너뛴다. 이벤트 실패는 노트로 붙는다."""
    state, finding = _pov_state(ctx)
    capsule = state.finding_capsules[finding.finding_id]
    scripted = _scripted(_tight_budget(ctx), [_gen(count=8), _SUBMIT])

    def broken(*args: object, **kwargs: object) -> None:
        raise OSError("trace disk full")

    monkeypatch.setattr(runner, "_emit_pov_hop", broken)

    with pytest.raises(BudgetExhausted) as caught:
        runner._run_pov_hops(scripted, state, finding, capsule)

    assert any("pov_hop을 내지 못했다: OSError" in note for note in caught.value.__notes__)


# --- 이미 있던 핀 — 예산·dry-run -----------------------------------------------------


def test_budget_exhaustion_leaves_no_poc_directory(
    ctx: RunContext,  # noqa: F811
    tmp_path: Path,
) -> None:
    """예산 소진은 루프를 뚫고 올라간다(추가형 계약) — 디렉터리조차 안 생겨야 한다.

    `test_prompt_log.test_budget_exhaustion_leaves_no_orphan_file`과 같은 자리다. 여기서는 루프
    **진입 전에** 원장이 이미 소진이라 PoC를 조립할 일조차 없다(위 두 검사는 조립 뒤다).
    """
    state, finding = _pov_state(ctx)
    capsule = state.finding_capsules[finding.finding_id]
    run_dir = tmp_path / RUN_ID
    # **예산을 프로필에서 빌리지 않는다.** 전에는 `config.budget.input_tokens + 1`을 청구해
    # 소진을 만들었는데, 그것은 「값과 무관」해 보여도 **상한이 양수여야** 성립한다 —
    # `dev_fake`의 상한이 0(= 상한 없음. `BudgetLedger._overrun`은 0을 검사하지 않는다)으로
    # 내려가자 이 검사가 소진을 못 만들어 빨개졌다. 소진 경로를 보는 검사는 자기 예산을
    # 주입한다(`test_pipeline_e2e`의 `tiny`가 그 모양이다).
    tiny = BudgetLedger(Budget(input_tokens=1, output_tokens=1, gpu_seconds=0), ctx.trace)
    wired = dataclasses.replace(_wired(ctx, run_dir), ledger=tiny)
    tiny.charge(Usage(input_tokens=2, output_tokens=0, cached_tokens=0), stage=Stage.VALIDATE)

    with pytest.raises(BudgetExhausted):
        runner._run_pov_hops(wired, state, finding, capsule)

    assert not (run_dir / DIR_NAME).exists()


def test_dry_run_leaves_no_poc_artifacts(tmp_path: Path) -> None:
    """`write_manifest`·`PromptLog`와 같은 가드다 — 매니페스트 없는 run에 PoC만 남지 않는다.

    ⚠ **이 검사는 가드 줄을 물지 않는다.** dry-run은 `DRY_RUN_LAST_STAGE`(=RETRIEVAL)에서
    서므로 8단계 pov에 애초에 못 간다 — 가드를 지워도 디렉터리는 안 생긴다(오염으로 확인:
    가드를 통째로 지운 전수 실행이 그대로 초록이었다). 여기서 고정하는 것은 **관측 가능한
    명제**(dry-run 산출물에 PoC가 없다)이고, 가드 줄은 아래 검사가 문다.
    """
    _, trace_path = _run(tmp_path, dry_run=True)

    assert not (trace_path.parent / DIR_NAME).exists()


def _run_without_a_run_dir(tmp_path: Path) -> None:
    """`run_dir=None`으로 파이프라인을 돈다(`cli.execute`는 늘 run_dir을 넘기므로 직접 부른다)."""
    config = load_config("dev_fake")
    deps = build_deps(
        config, run_id=RUN_ID, source_root=TARGET, artifacts_root=tmp_path / "artifacts"
    )
    runner.run_pipeline(
        runner.RunSpec(condition=CONDITION, target=TARGET, started_at=STARTED_AT),
        config=config,
        deps=deps,
        read_source=lambda target: read_symbol_source(TARGET, target),
        trace=TraceWriter(RUN_ID),
        run_dir=None,
    )


def test_the_sidecar_is_wired_only_for_a_real_run_that_has_a_run_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """주입 가드(`run_dir` 있음 **그리고** dry-run 아님)를 **생성 여부로** 문다 — 세 갈래 전부.

    디렉터리 부재만 보는 검사로는 가드가 사라져도 빨개지지 않는다. dry-run은 0개, `run_dir`이 없는
    run도 0개(가드의 **이 절반**이 `not spec.dry_run`만 남긴 변이로 살아남았다), 실제 run은 1개.
    한쪽만 보면 "아무 때도 안 만든다"가 통과한다.
    """
    built: list[Path | None] = []
    real = runner.PocLog

    def spy(run_dir: Path) -> PocLog:
        built.append(run_dir)
        return real(run_dir)

    monkeypatch.setattr(runner, "PocLog", spy)

    _run(tmp_path / "dry", dry_run=True)
    assert built == [], f"dry-run이 사이드카를 만들었다: {built}"

    _run_without_a_run_dir(tmp_path / "nodir")
    assert built == [], f"run_dir 없는 run이 사이드카를 만들었다: {built}"

    _run(tmp_path / "live")
    assert len(built) == 1, f"실제 run이 사이드카를 안 만들었다: {built}"


# --- 파이프라인 — 배선이 실제로 돈다 ---------------------------------------------------


def test_pipeline_writes_the_poc_sidecar_for_dev_fake(tmp_path: Path) -> None:
    """실제 run에서 **배선이 돈다.** 위 단위 검사는 사이드카를 손으로 주입하므로 러너의 주입
    줄을 지워도 초록이다 — 이 검사가 그 줄을 문다(`test_pipeline_e2e`의 pov 배선 검사와 같은 결).
    디렉터리의 파일 목록이 이벤트가 가리킨 ref와 **정확히** 같다(고아 파일 없음).
    """
    _, trace_path = _run(tmp_path)
    run_dir = trace_path.parent

    generated = [e["data"] for e in _events(trace_path) if e["kind"] == "pov_generated"]
    assert generated, "dev_fake가 pov를 선언하는데 pov_generated가 0건 — 라우팅이 끊겼다"
    for data in generated:
        path = run_dir / data["poc_ref"]
        assert path.is_file()
        assert data["poc_digest"] == sha256_digest(path.read_bytes())
        assert data["poc_bytes"] == path.stat().st_size
    assert _listing(run_dir / DIR_NAME) == sorted(Path(d["poc_ref"]).name for d in generated)


def test_pipeline_survives_a_sidecar_write_failure_and_still_reaches_the_manifest(
    tmp_path: Path,
) -> None:
    """**run이 매니페스트까지 간다.** 쓰기 실패가 파이프라인을 뚫으면 run은 `run_manifest.json` 없이
    끝나고(`StageSpan`이 `stage_end ok=false`만 남기고 전파한다) 그 사유가 산출물 어디에도 없다.

    `before`가 run_dir에 `pov_poc`라는 **파일**을 미리 놓아 `mkdir`이 실패하게 한다.
    """
    result, trace_path = _run(tmp_path, before=_block_the_directory)
    run_dir = trace_path.parent

    assert (run_dir / "run_manifest.json").is_file()
    assert result.record.finished_at is not None
    generated = [e["data"] for e in _events(trace_path) if e["kind"] == "pov_generated"]
    assert generated
    assert all(d["poc_write_error"] == "FileExistsError" and "poc_ref" not in d for d in generated)
    assert all(d["poc_bytes"] > 0 for d in generated), "원래 키는 쓰기 실패와 무관하게 남는다"


# --- dev_fake 한 건 — 기대값을 조립에서 뽑는다 ---------------------------------------


def test_the_hop0_gen_of_dev_fake_is_the_poc_the_sidecar_holds(
    ctx: RunContext,  # noqa: F811
    tmp_path: Path,
) -> None:
    """파일 바이트 == `assemble_poc` 결과 — 기대값을 **조립에서 뽑는다**(하드코딩 아님). 결정론
    fake라 hop0 gen의 spec을 그대로 조립하면 루프가 만든 PoC와 같다. ref는 run_dir 상대이고
    `run_id`를 안 품는다(품으면 trace의 비교 단위가 run마다 달라진다)."""
    state, finding = _pov_state(ctx)
    capsule = state.finding_capsules[finding.finding_id]
    hop0 = pov.open_hop(ctx, finding=finding, capsule=capsule)
    assert hop0.action.action == "gen" and hop0.action.spec is not None
    expected = assemble_poc(hop0.action.spec)
    run_dir = tmp_path / RUN_ID
    wired = _wired(ctx, run_dir)

    runner._run_pov(wired, state, finding)

    data = _terminal(wired).data
    assert RUN_ID not in data["poc_ref"]
    assert not Path(data["poc_ref"]).is_absolute()
    assert (run_dir / data["poc_ref"]).read_bytes() == expected
    assert data["poc_digest"] == sha256_digest(expected)
