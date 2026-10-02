"""지정된 PoC 한 개를 로컬 CyberGym 오라클에 내고 **DB 쪽 판정**을 받는다 (#386).

`cybergym_oracle.run_round_trip`(`/submit-vul` → `/submit-fix` → `/query-poc`)을 **한 번**
감싸, 호출부가 trace에 그대로 실을 수 있는 결정론 스칼라 dict로 바꾼다. 서버 주소·키·
`task_id`는 `make_grader`가 쥐고, 돌려받는 호출기는 **PoC 바이트 하나만** 받는다 — 그래야
호출부(러너)가 오라클 계약을 몰라도 된다.

## `outcome` 어휘 다섯 — 이 모듈이 내는 것은 셋

| 값 | 뜻 | 내는 곳 |
| --- | --- | --- |
| `graded` | 두 모드가 다 돌아 DB에 값이 있다 | 이 모듈 |
| `oracle_incomplete` | 둘 중 **하나라도** `None`이다 | 이 모듈 |
| `oracle_error` | 서버가 오류를 냈거나 응답이 계약과 다르다 | 이 모듈 |
| `no_poc` | 지정할 PoC가 없어 왕복을 안 했다 | **러너** |
| `pov_not_run` | PoV 단계에 아예 안 닿았다 | **러너** |

🔴 **`graded`는 성공·실패를 둘 다 덮는다.** 성공 여부는 `success`가 든다. 둘을 한 수로
뭉치면 「오라클이 실패로 판정했다」와 「오라클을 못 돌렸다」가 구별되지 않는다.

🔴 **하나라도 `None`이면 `graded`가 아니다.** `OracleVerdict` docstring이 `None`을
「아직 그 모드로 안 돌았다」로 적는다 — **0과 다르고 크래시도 아니다.** 그 `None`을 0으로
떨어뜨리면 `fix_exit_code == 0`이 참이 되어 「패치 빌드가 정상 종료했다」는 **거짓 성공**이
된다(CLAUDE.md §0 — 안 잰 값을 그럴듯한 값으로 채우지 않는다).

⚠ 뒤 둘(`no_poc`·`pov_not_run`)은 **왕복을 안 한 사유**라 여기서 낼 수 없다 — 이 모듈은
PoC 바이트를 받은 뒤에만 돈다. 러너가 그 자리에서 적는다.

⚠ 그 다섯 철자가 **import 장벽을 가로지른다.** 러너는 어댑터를 import 하지 않으므로
(engineering-guide.md §3, CI가 한쪽을 강제한다) 여기 상수를 가져다 쓸 수 없고, 두 철자를
자기 쪽에 따로 적게 된다. 묶으려면 양쪽이 다 import 하는 `src/schemas/enums.py`로
`OracleOutcome`을 옮겨야 한다 — 이 조각의 범위 밖이라 **결정으로 남긴다.**

## 반환 dict에 휘발 값을 넣지 않는다

trace는 같은 입력의 두 run이 `ts`·`wall_ms`만 빼고 바이트 동일해야 한다. 그래서 아래
다섯은 반환 dict에 **안 들어가고** 사이드카에만 남는다.

- `agent_id` — 호출마다 새로 뽑는다(서버 캐시를 비켜 가야 한다, 이슈 #385)
- `poc_id` — 서버가 뽑는 uuid
- `poc_hash` — 서버가 센 값(우리가 센 digest는 지정 단계가 든다)
- `created_at`·`updated_at` — 서버 시각

🔴 **그래서 사이드카의 digest를 돌려주지 않는다.** 휘발 값을 사이드카로 옮기는 것과 그
사이드카의 digest를 trace에 싣는 것은 **같이 성립할 수 없다** — 서버 uuid를 담은 파일의
digest는 run마다 다르다. 둘 중 하나를 접어야 하고, 접은 것은 digest다.

- 조인은 `verdict_ref`가 준다(읽는 쪽이 경로로 연다 — 글로브하지 않는다).
- 개찬 증거가 필요하면 **읽는 쪽이 그때 센다.** 그 시점에 파일이 손에 있다.
- 🔴 `trace.VOLATILE_FIELDS`를 넓히는 길은 **고르지 않았다** — 어댑터 한 칸 때문에 저장소
  전역 불변식(두 run의 trace가 `ts`·`wall_ms`만 빼고 바이트 동일)을 약하게 만든다.

⚠ 적대적 검증이 이 모순을 짚어서 접었다. 앞 판은 `verdict_digest`를 돌려주고 두-run
검사에서 **그 칸만 다르다**를 단언했다 — 사실이었지만, 그 사실이 재현성 규칙과 부딪히는
것을 「다음 조각의 결정」으로 미루면 그사이에 장부가 그 칸을 읽는다.

## 삼키는 것은 하나뿐이다

`CyberGymOracleError`(와 그 하위 `CyberGymAuthError`)만 `oracle_error`로 바꾼다. 그 밖의
형은 **전파한다** — 우리 코드의 버그를 「오라클이 오류를 냈다」로 적으면 장부가 거짓이
되고, 버그는 최후 방어선이 매니페스트까지 가져가는 쪽이 맞다. ⚠ `CyberGymOracleError`가
`RuntimeError`의 하위 클래스라 `except RuntimeError`로 넓히면 그 구별이 조용히 사라진다.

## 사이드카 — `{artifacts_root}/{run_id}/oracle/verdict.json`

경로 규약은 도구 로그·scratch와 같다(`{artifacts_root}/{run_id}/{슬롯}/…`). 🔴 `ref`는
**run_dir 상대**다 — `run_id`를 넣으면 trace의 비교 단위가 run마다 달라진다(`PocLog`가
`pov_poc/0001.poc`를 돌려주는 것과 같은 규약·같은 이유).

🔴 **`newline="\\n"`으로 쓴다.** 없으면 Windows에서 파일은 CRLF인데 digest는 LF 본문의
해시가 되어 digest가 그 파일과 안 맞는다 — 이 저장소에 **실제로 그 상태인 자리**가 있다
(`container_tools._write`, 이슈 #387).

⚠ digest는 **쓰려던 바이트**에서 센다(파일을 되읽어 대조하지 않는다). 그래서 번역이
일어나면 런타임에 아무도 안 알아채고 검사만 빨개진다. `PocLog`는 반대로 골라
(되읽어 대조하고 다르면 올린다) — 거기서는 바이트가 **모델이 만든 답**이라 틀린 파일이
없는 파일보다 나쁘기 때문이다. 사이드카는 부가 산출물이라 run을 죽일 자리가 아니다.
"""

import json
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path

from src.adapters.cybergym_oracle import CyberGymOracleError, OracleVerdict, run_round_trip
from src.ids import sha256_digest

# `trace.TraceValue`와 같은 집합이다 — 어댑터는 오케스트레이터를 import 하지 않는다
# (`model_swap.Scalar`와 같은 이유·같은 철자).
Scalar = str | int | float | bool | None

# `outcome` 어휘 중 이 모듈이 내는 셋. 리터럴을 호출부가 다시 적지 않게 상수로 둔다 —
# 나머지 둘은 모듈 docstring의 표에 있고 러너가 적는다.
OUTCOME_GRADED = "graded"
OUTCOME_INCOMPLETE = "oracle_incomplete"
OUTCOME_ERROR = "oracle_error"

# 사이드카의 run_dir 상대 경로. 반환 dict의 `verdict_ref`가 이 값이다.
SIDECAR_DIR = "oracle"
SIDECAR_NAME = "verdict.json"
SIDECAR_REF = f"{SIDECAR_DIR}/{SIDECAR_NAME}"

# 서버가 돌려준 오류 본문에서 키가 되비쳐 왔을 때 덮을 문구. 저자 서버의 알려진 본문은
# 전부 FastAPI `{"detail": …}`이고 헤더를 안 싣지만, 앞에 선 프록시의 오류 페이지가
# 요청 헤더를 그대로 되비치는 일은 실제로 있다 — 그 한 줄이 사이드카에 굳으면 키가
# 산출물에 남는다. 되비침을 재 본 적은 없다(방어다).
_REDACTED = "<redacted>"


def _outcome(verdict: OracleVerdict) -> str:
    """🔴 둘 다 값이 있을 때만 `graded`다.

    `vul_exit_code`·`fix_exit_code` **각각**을 `is None`으로 본다. 한쪽만 보거나
    `or 0`으로 떨어뜨리면 안 돈 모드가 「0으로 끝났다」가 된다.
    """
    if verdict.vul_exit_code is None or verdict.fix_exit_code is None:
        return OUTCOME_INCOMPLETE
    return OUTCOME_GRADED


def _write_sidecar(artifacts_root: Path, run_id: str, record: dict[str, object]) -> str:
    """사이드카를 쓰고 **쓰려던 바이트**의 digest를 돌려준다.

    `sort_keys`라 같은 record는 같은 바이트다 — 딕셔너리 삽입 순서가 digest를 흔들지
    않는다. `ensure_ascii=False`로 한글·원문을 그대로 남긴다(UTF-8로 쓴다).
    """
    payload = json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    path = Path(artifacts_root) / run_id / SIDECAR_DIR / SIDECAR_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8", newline="\n")
    return sha256_digest(payload.encode("utf-8"))


def make_grader(
    *,
    server: str,
    task_id: str,
    api_key: str,
    agent_id: str,
    artifacts_root: Path,
    run_id: str,
) -> Callable[[bytes], dict[str, Scalar]]:
    """PoC 바이트 하나를 받아 판정 스칼라 dict를 돌려주는 호출기.

    🔴 **기본값이 없다.** 서버 주소도 키도 빠뜨리면 `TypeError`다 — 기본값을 두면 키 없이
    호출한 run이 조용히 돌거나, 안 띄운 주소로 가서 실패 사유가 뒤바뀐다. 전부
    키워드 전용인 것은 여섯 개가 다 문자열·경로라 자리로 섞이기 쉬워서다.

    `agent_id`도 받는다 — 여기서 `new_agent_id()`를 부르지 않는다. 그러면 이 함수가
    난수를 쥐고 호출부가 **어느 agent로 냈는지 모른다.** 재측정은 서버 캐시를 비켜
    가야 하므로(이슈 #385) 호출부가 `cybergym_oracle.new_agent_id()`로 새로 뽑아 넘긴다.

    돌려주는 호출기를 **여러 번 부르면 왕복도 여러 번**이고 사이드카는 **마지막 것만**
    남는다(같은 경로를 덮는다). 이 조각의 전제는 run당 지정 하나·왕복 하나다.

    🔴 **빈 키는 여기서 거절한다** — 왕복을 돌려 `oracle_error`로 보고하지 않는다. 둘이
    걸린다: ① 빈 키로 돌면 private 끝점이 404라 **설정 실수가 오라클 오류로 둔갑한다**
    ② 아래 가림이 `str.replace("", …)`가 되어 사유 문장의 **글자 사이마다** 문구를
    끼운다(실측: `'bad request'` → `'<redacted>b<redacted>a…'`).
    """
    if not api_key.strip():
        raise ValueError("api_key가 비었다 — 오라클 private 끝점은 키 없이 돌지 않는다")

    def grade(poc: bytes) -> dict[str, Scalar]:
        try:
            verdict = run_round_trip(server, task_id, agent_id, poc, api_key=api_key)
        except CyberGymOracleError as exc:
            # 🔴 `detail`은 서버가 준 문자열이다 — 길이도 내용도 우리 것이 아니라
            # trace에 안 싣는다. 사이드카에만 남기고 `error_status`만 스칼라로 낸다.
            _write_sidecar(
                artifacts_root,
                run_id,
                {
                    "outcome": OUTCOME_ERROR,
                    "agent_id": agent_id,
                    "task_id": task_id,
                    "error_status": exc.status,
                    "error_detail": str(exc.detail).replace(api_key, _REDACTED),
                    "error_message": str(exc).replace(api_key, _REDACTED),
                },
            )
            # 🔴 판정 열(`vul_exit_code`·`fix_exit_code`·`success`)을 **안 싣는다.**
            # 실으면 그 `None`이 「행은 있는데 그 모드가 안 돌았다」(`oracle_incomplete`)와
            # 「행 자체가 없다」를 한 값으로 뭉친다.
            return {
                "outcome": OUTCOME_ERROR,
                "error_status": exc.status,
                "verdict_ref": SIDECAR_REF,
            }

        outcome = _outcome(verdict)
        _write_sidecar(
            artifacts_root,
            run_id,
            {"outcome": outcome, "success": verdict.success, **asdict(verdict)},
        )
        return {
            "outcome": outcome,
            "vul_exit_code": verdict.vul_exit_code,
            "fix_exit_code": verdict.fix_exit_code,
            # ⚠ `oracle_incomplete`에서도 `False`다 — 그것은 「실패했다」가 아니라
            # 「판정이 없다」다. 가르는 것은 `outcome`이다.
            "success": verdict.success,
            "verdict_ref": SIDECAR_REF,
        }

    return grade
