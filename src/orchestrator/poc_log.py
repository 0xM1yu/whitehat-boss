"""PoV 루프가 선 자리의 **마지막 `gen` PoC**를 run 산출물에 파일로 남긴다 (#355).

`PromptLog`와 같은 모양의 주입 객체다 — run_dir을 아는 것은 러너이고 `RunContext`도 노드도
그것을 몰라야 한다(`context.py`의 `prompts` 필드 주석). `None`이면 아무것도 쓰지 않는다.

## 무엇을 남기나 — 루프당 **최대 한 파일**

`runner._pov_finish`(루프 종단)가 한 번 쓴다. 쓰는 것은 그 루프의 **마지막 `gen`이 조립한 PoC**
뿐이다 — 중간 `gen`들의 PoC는 파일로 안 남고(그 해시는 `pov_hop`(gen)의 `poc_sha256`에 있다),
조립한 PoC가 하나도 없는 루프(gen 없는 submit·hop0 실패)는 파일도 `poc_ref`도 없다. submit 못 한
부분 PoC(max_hop·repeat·흡수된 홉 실패)도 남긴다 — 회수가 목적이라 종단 사유로 가르지 않는다.

## 왜 필요한가 — 되살리기가 번거롭거나, 어떤 경우엔 불가능했다

루프는 조립한 바이트를 반복 판정에 쓰고 trace에는 길이와 sha256만 남겼다. 조립이 결정론이라
**spec이 남아 있으면 같은 바이트를 다시 만들 수 있다.** SUBMITTED 루프의 마지막 `gen` spec은 다음
홉의 프롬프트 기록(`prompts/…_hopK.md`의 `history speaker=assistant` 턴)에 실리고, 그 spec을
`assemble_poc`에 넣으면 `poc_sha256`이 일치한다(dev_fake 실측). 그래도 프롬프트 기록을 파싱해야
하고, **그 응답이 어느 프롬프트 history에도 안 실린 경우는 정말로 방법이 없었다** — MAX_HOP으로
끝난 루프의 마지막 `gen`(그 응답을 history로 받을 다음 홉이 없다 — dev_fake 실측)과 예산 소진으로
끊긴 루프(`ledger.ensure`가 막은 호출은 프롬프트도 안 쓴다). 이 파일은 앞의 것을 닫고 나머지는
파싱 없이 쓰게 한다. 뒤의 것은 아래 「한계」로 남는다.

## 한계 — 중단 경로는 파일이 없다

`BudgetExhausted`와 서버 쪽 게이트웨이 오류(status 없음·5xx)는 흡수하지 않고 올려 보내므로
(`_run_pov_hops`의 추가형 계약) `_pov_finish`를 건너뛴다 — 그 루프의 PoC는 **파일도 종단 이벤트도
없다.** 잃는 것을 되돌리는 구조 변경은 하지 않았다. 대신 gen 홉의 `pov_hop`이 그 홉 PoC의
`poc_sha256`을 싣는다. **그것이 잡는 것은 그 홉의 이벤트가 나간 뒤에 끊긴 경우뿐이다**(다음 홉의
모델 호출이 서버 오류로 실패). **그 홉 자신의 도구 호출에서 끊기면 해시도 없다** — 토큰 예산은
그 홉의 모델 호출이 소진시키는 것이 보통이고 `call_tool`도 `ensure`를 하므로, PoC를 조립한 뒤·홉
이벤트를 내기 전에 끊기는 이쪽이 예산 소진의 흔한 경로다. 메우려면 `run_tool` 둘레에서 예외를 받아
홉 이벤트를 내고 다시 던져야 하는데 루프 모양을 바꾸는 일이다. `tests/test_poc_log.py`가 두 경로를
고정한다 — 없애는 변경이면 그 검사를 뒤집고 이 절을 고친다.

## 쓰기

**바이트로 쓴다.** `open(…, "xb")`는 개행을 번역하지 않는다. 텍스트로 쓰면 Windows에서 파일은
CRLF인데 digest는 LF 본문의 해시가 되어 digest가 그 파일과 안 맞는다 — `_write`
(`src/adapters/container_tools.py`)가 그 상태였고(#387) 지금은 바이트 쓰기로 고쳐졌다.
`os.open`을 직접 쓰면 Windows에서 `O_BINARY`를 따로 주지 않는 한 같은 번역이 난다
(2026-10-02 이 장비에서 LF가 CRLF로, CRLF가 CR·CR·LF로 바뀌는 것을 실측했다) — 그래서 `open`이다.

**digest는 파일에서 계산하고, 되읽은 바이트를 조립한 바이트와 대조한다.** 파일에서만 계산하면
digest는 그 파일에 대해 항상 참인데 번역된 바이트가 「의도된 내용」으로 굳는다(#387이 그 방안의
대가로 적은 것이다). 다르면 `PocWriteError`이고 파일은 내놓지 않는다 — 틀린 PoC 파일은 없는 파일보다
나쁘다. 대조를 통과하면 파일에서 센 digest와 메모리에서 센 digest는 같은 값이다.

**원자적으로 쓴다.** 임시 파일(`NNNN.poc.tmp`)에 쓰고 `os.replace`한다(`heartbeat`·
`tools/monitor`의 관용구) — 읽는 쪽이 반쯤 쓰인 PoC를 못 본다. `fsync`는 하지 않는다: 전원
차단 내구성은 보장하지 않는다(`trace`의 flush와 같은 선, #208).

**하드 크래시(`SIGKILL`·전원 차단)는 정리가 못 덮는다** — `except BaseException` 정리는
파이썬이 예외를 올릴 수 있을 때만 돈다. `open`과 `os.replace` 사이에서 죽으면 `NNNN.poc.tmp`가
남는다. 오늘은 무해하다: run_dir은 run마다 새로 잡히고(`cli._claim_run_dir`가 `exist_ok=False`로
선점) `src`에 resume이 없어 그 디렉터리를 다시 쓰는 `PocLog`가 없다. **resume이 생기면**
`_seq`가 0에서 다시 시작해 남은 `.tmp`와 앞선 `.poc`(`os.replace`가 최종 이름을 덮는다)에
부딪힌다 — 그때 번호 부여를 같이 고친다.

**임시 경로는 새로 만든다(`x` = `O_CREAT|O_EXCL`).** 이미 있는 것은 심링크여도 따라가지 않고
`FileExistsError`다 — WSL Ubuntu(Python 3.12.3)에서 살아 있는 링크·끊어진 링크 둘 다 거절하고 대상이
안 만들어지는 것을 실측했다. `O_NOFOLLOW`는 안 건다: 말단 하나만 막는데 `O_EXCL`이 이미 그것을
하고, Windows에는 그 플래그가 없다. **막지 못하는 것** — `pov_poc/`나 그 상위가 디렉터리
심링크·정션으로 바뀐 경우(WSL에서 상위 심링크는 따라가는 것을 실측했다), Windows의 `CREATE_NEW`가
심링크 말단을 거절하는지(이 장비는 심링크 권한이 없어 못 쟀다). run_dir을 쓸 수 있는 쪽은 이미
신뢰 경계 안이라는 전제다.

**쓰기 실패(`OSError`)는 여기서는 올린다.** 흡수하는 곳은 `_pov_finish`다 — PoV는 추가형이라
사이드카는 부가 산출물이고 실패가 run을 죽이면 안 된다(「캡슐 미스는 finding을 죽이지 않는다」가
같은 선례). 종단 이벤트의 `poc_write_error`로 남는다.

## 이름 — 디렉터리 `pov_poc/` · 확장자 `.poc` · 순번

**디렉터리가 `poc/`이면** `ContainerToolRunner._write`가 `poc` 도구의 로그를 쓰는 슬롯
(`TOOL_KINDS`의 멤버)과 한 디렉터리가 되고 `make_payload_reader`는 접미사를 안 걸러 읽는다.
**확장자가 `.bin`이면** `preprocess._KIND_BY_SUFFIX`가 BINARY로 분류해 `_ANALYZABLE`에 든다 —
`--target`이 저장소 루트라 `artifacts/`가 대상 트리 안에 있으면 **이전 run의 PoC가 다음 run의 분석
대상이 되어 그 바이트가 Scout 프롬프트에 실린다**(dev_fake 실측: 후보가 되고 `prompts/…_scout.md`에
들어간다 — `.poc`는 후보도 안 된다). 한 run의 답이 다음 run의 입력이 되는 오염이다. 둘 다
`tests/test_poc_log.py`가 고정한다.

**파일명은 순번뿐이다.** `PromptLog`와 같은 모양이다. `finding_id`를 안 쓰는 이유로 예전에 적은
「모델 출력에서 올 수 있다」는 **#279 이전의 사실이다** — 지금 `finding_id`는 하네스가 발급해 도장
찍는다(`RunContext.stamp_issued`). 그래서 ID를 못 쓸 이유는 사라졌고, 안 쓰는 이유는 쓰는 쪽이 ID
형식에 기대지 않는 것과 finding과의 연결을 trace 이벤트(`subject_id` + `poc_ref`)가 드는 것이다.

## 하지 않는 것

**Evidence로 승격하지 않는다 — 기록·회수만 한다.** 게이트가 `Evidence.ok`만 보므로(#232)
`POC_RUN` kind의 Evidence를 달면 크래시 없이 exit 0으로 끝난 실행에서도 E4가 열린다. 승격은 #232가
풀린 뒤의 일이고, 그때까지 이 모듈은 **우회로이지 해결이 아니다.**

**누출 스캔에 넣지 않는다.** `eval/leakage`의 `RUN_GLOBS`(`*/trace.jsonl`·`*/prompts/*.md`)와
`TEXT_SUFFIXES`가 이 디렉터리·확장자를 안 부르므로 스캐너는 이것을 안 본다. 그리고 PoC는 대상
코드에서 흘러든 입력이 아니라 **우리가 조립한 출력**이라, 그 스캐너가 지키는 것(모델 입력에 과제
식별자가 섞이는 것)의 매개가 아니다. 넣어야 할 근거가 생기면 그때 glob을 더한다(멤버십만 보는
핀이라 추가는 안전하다). 예전 설명의 「이진을 `errors="replace"`로 읽으면 거짓 양성이 난다」는
재 본 적 없는 주장이라 뺐다(그 읽기는 `eval/leakage/id_masking.py`에 있다).

**digest 형식이 trace의 `poc_sha256`과 다르다.** 이쪽은 `sha256_digest`라 `sha256:` 접두가
붙고, 같은 이벤트의 `poc_sha256`은 **접두 없는 맨 hex**다(핀 `tests/test_nodes.py`). 둘을
「통일」하면 그 핀이 깨진다 — 다르게 두는 것이 의도다.
"""

import contextlib
import errno
import os
from pathlib import Path

from src.ids import sha256_digest

# run_dir 아래 디렉터리 이름. **도구 슬롯(`TOOL_KINDS`)·`prompts/`·`scratch/`·`rejected/`가 아니다**
# — 이름 풀이는 모듈 docstring의 「이름」 절이다.
DIR_NAME = "pov_poc"

# 파일 확장자. **`preprocess._KIND_BY_SUFFIX`에 없어야 한다** — 있으면 이전 run의 PoC가 다음 run의
# 분석 대상이 된다.
SUFFIX = ".poc"

# 파일명 순번 폭. `prompt_log.SEQ_WIDTH`와 같은 값이지만 한쪽을 바꿀 이유가 다른 쪽에 번지지
# 않게 따로 둔다(그쪽 주석과 같은 이유).
SEQ_WIDTH = 4


class PocWriteError(OSError):
    """쓴 파일을 되읽었더니 조립한 바이트와 다르다 — 그 파일은 내놓지 않는다.

    `OSError`의 하위 클래스인 것은 `_pov_finish`가 쓰기 실패를 한 갈래(`except OSError`)로
    흡수하게 하려는 것이다.
    """


class PocLog:
    """`{run_dir}/pov_poc/`에 루프 하나당 PoC 파일 하나(`0001.poc`, `0002.poc`, …).

    **순번은 이 객체가 센다** — `PromptLog`와 같은 이유로 trace의 `seq`도 `IdFactory`도 쓰지
    않는다. 쓰기를 **시도할 때** 오르므로 실패한 시도도 번호를 먹는다(그 번호의 임시 파일이
    남아도 다음 쓰기가 막히지 않는다). v1 전제는 순차 실행이고, 단계를 동시에 돌리게 되면 여기
    카운터에도 락이 필요하다.

    크기 상한은 이쪽이 아니라 `schemas/pov.py`의 `_MAX_POC_BYTES`가 조립 전에 건다 — 검증을
    통과한 `PovSpec`만 `assemble_poc`에 들어가므로 여기 오는 바이트는 이미 그 아래다.
    """

    def __init__(self, run_dir: Path) -> None:
        self._dir = Path(run_dir) / DIR_NAME
        self._seq = 0

    @property
    def directory(self) -> Path:
        return self._dir

    def write(self, poc: bytes) -> tuple[str, str]:
        """파일을 쓰고 `(run_dir 상대 경로, 그 파일 바이트의 sha256 digest)`를 돌려준다.

        ref에 `run_id`를 넣지 않는다 — 넣으면 trace의 비교 단위가 run마다 달라진다.

        실패하면 `OSError`(되읽은 바이트가 다르면 `PocWriteError`)이고, 최종 파일도 **우리가 만든**
        임시 파일도 남지 않는다. 임시 경로에 **남이 만든** 것이 있으면 `FileExistsError`이고 그것은
        건드리지 않는다 — 정리는 우리가 `open`으로 만든 뒤에만 한다.
        """
        self._seq += 1
        final = self._dir / f"{self._seq:0{SEQ_WIDTH}d}{SUFFIX}"
        tmp = final.with_name(final.name + ".tmp")
        self._dir.mkdir(parents=True, exist_ok=True)
        handle = open(tmp, "xb")  # FileExistsError면 남의 것이라 여기서 그대로 올린다
        try:
            with handle:
                handle.write(poc)
            written = tmp.read_bytes()
            if written != poc:
                raise PocWriteError(errno.EIO, "되읽은 바이트가 조립한 PoC와 다르다")
            os.replace(tmp, final)
        except BaseException:
            with contextlib.suppress(OSError):
                tmp.unlink(missing_ok=True)
            raise
        return f"{DIR_NAME}/{final.name}", sha256_digest(written)
