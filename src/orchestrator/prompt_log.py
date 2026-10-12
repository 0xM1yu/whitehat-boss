"""렌더된 프롬프트를 run 산출물로 남긴다 (open-questions.md 해소).

`engineering-guide.md` §6의 trace 필수 항목 표 마지막 행이 **"모델 입력 context 전체 —
누출 검증"**이고, `evaluation.md` §4 누출 방지 표의 `Prompt Logging` 행이 같은 것을
요구한다. **새 요구가 아니라 미구현 요구였다** — 그전까지 run_dir에 남는 것은
`run_manifest.json`·`trace.jsonl`·`report.md`뿐이었고 그 어느 것도 프롬프트를 담지
않았다. 그래서 "모델이 무엇을 보고 판단했는가"에 사후에 답할 수 없었고, W1(캡슐 섹션
본문이 프롬프트에 실린다)의 증거가 픽스처와 Fake 모델 덤프뿐이었다.

## trace `data`가 아니라 사이드카 파일인 이유

`Evidence.payload_ref`가 도구 출력에 대해 **이미 같은 모양**을 쓴다
(`adapters/container_tools.py`: `{artifacts_root}/{run_id}/{slot}/{evidence_id}.log`).
그쪽과 다르게 할 이유가 없고, trace에 본문을 실으면 둘이 깨진다:

- `trace.canonical_line()`은 `VOLATILE_FIELDS`(`ts`·`wall_ms`)만 뺀다. 본문을 `data`에
  넣으면 **재현성 비교의 단위가 이벤트 메타에서 모델 입력 전체로 조용히 바뀐다** —
  비교에서 빼는 것을 `data` 키로 넓히지 않는다(#64 결정, 2026-10-10) — 그래서 `data`에
  실은 본문은 빠지지 않고 그대로 비교된다.
- 실측 기준 콜당 **약 4.4 KB**다(2026-08-22 ATOM run: input 4,389토큰 / 4콜).
  `trace.jsonl`의 한 줄이 그만큼 부푼다.

## `prompt_ref`에 `run_id`를 넣지 않는다

돌려주는 참조는 **run_dir 상대**(`prompts/0001_scout.md`)다. `TraceEvent.run_id`가 이미
canonical 표현에 들어 있으므로 trace의 run 의존성 자체는 기존 성질이고, 이 변경이 거기
**새로 얹는 run 의존 문자열은 없다.**

## 남기는 것과 남기지 않는 것

- **prefix + body 전체**를 남긴다. need_evidence 후속 턴(hop>0)이면 그 사이에 **history 내용**도
  남긴다 — 서버에 간 것이 `[system]+history+[이번 turn]`이라서다.
  사실 블록만 남기면 #67이 지적한 산문 자리표시자
  (`{{TARGET}}`·`{{CAPSULE_SECTIONS}}`·`{{SOURCE}}`)가 기록에서 사라지는데, **정확히 그
  블록이 W1이 채운 자리**다.
- **응답은 남기지 않는다**(⏵ 2026-10-11 — 예외: 스키마 소진·게이트웨이 거절로 끝난 호출의 스키마
  실패 시도 원문은 `write_response`가 `…_response[_retryK].txt`로 남긴다. 재시도가 성공한 호출의 앞
  시도 원문은 안 남았다, #603. `content`가 `null`인 시도는 받은 문자열이 없어
  파일도 없다 — 호출자가 그 칸을 `write_response`로 부르지 않는다. ⏵ 2026-10-12 — 재시도가
  성공한 호출의 버려진 앞 시도 원문도 같은 이름으로 남고(#618), `RunContext.keep_responses`
  (`run --keep-responses`)에 든 역할은 성공한 최종 응답도 마지막 칸으로 남긴다(#612). 둘 다
  아니면 성공 응답은 여전히 안 남는다).
  `Candidate`·`Finding`·`Decision`이 이미 StateStore에 통째로 들어가고 usage·`gpu_ms`는
  `model_call`에 있다. 성공 응답은 사본을 만들면 정본을 물어야 해서 안 남긴다 — 실패한 응답에는
  정본(파싱된 값)이 없어 그 이유가 적용되지 않는다.
  또 하나 — hop>0 파일의 history에는 앞 홉의 응답 원문(assistant 턴)이 실린다. 응답을
  기록하려는 것이 아니라 그 홉에 **입력으로** 간 바이트라서다.
- **호출이 실패해도 남는다.** 호출자가 모델을 부르기 **전에** 쓴다 — 실패 분기가
  "스키마 파싱 실패율"의 분자(시도 단위 `attempts`, 정본은 `trace.py`)에 드는데
  (`context.call_model`), 그 입력이 없으면 왜 실패했는지 조사할 수 없다.

## 크기

콜당 프롬프트 크기는 **캡슐 예산과 함께 는다.** 실측 두 건:

| 어디 | 콜당 |
| --- | --- |
| ATOM `single_gpu_12g` (2026-08-22, input 4,389토큰 / 4콜) | ≈ **4.4 KB** |
| 로컬 `dev_docker` 스모크 (픽스처 대상, 6콜) | **2.4 ~ 4.8 KB** |

**상한은 재지 않았다.** 캡슐 예산은 scout 4,000 / auditor 24,000토큰인데
(`src/tokens.CHARS_PER_TOKEN`=4) 프롬프트는 그보다 크다 — 같은 내용이 산문 블록과 사실
블록에 **두 번** 실리기 때문이다(#67). 후보가 수백 건인 실대상에서 얼마가 되는지는
그 run을 돌려야 나온다(#65가 `tool_seconds`에 대해 같은 상태다).
`artifacts/`는 저장소 밖이므로 커밋 오염은 없다.

**prefix가 역할마다 중복 기록된다.** 역할당 한 번만 쓰고 참조하면 크게 줄지만, 그
절약이 필요하다는 **측정이 아직 없다.** 근거 없는 값으로 구조를 정하지 않는 것이 이
저장소의 규칙이다(#65: 장난감 대상의 측정에서 상한을 유도하면 지어낸 근거가 된다).
필요해지면 그때가 업그레이드 지점이다.
"""

from collections.abc import Sequence
from pathlib import Path

from src.ids import sha256_digest, sha256_text
from src.interfaces.model_client import ChatTurn, Prompt

# run_dir 아래 디렉터리 이름. `eval/leakage/__main__.py`의 스캔 glob이 이 이름을 안다.
DIR_NAME = "prompts"

# 파일명 순번 폭. `src/ids.SEQ_WIDTH`와 같은 값이지만 저쪽은 객체 ID의 폭이고 이쪽은
# 파일명이라 따로 둔다 — 한쪽을 바꿀 이유가 다른 쪽에 번지지 않게 한다.
SEQ_WIDTH = 4

# 기계가 되읽을 수 있는 경계. 마크다운 제목을 쓰지 않는 것은 **프롬프트 본문 자체가
# 마크다운**이라 `## body` 같은 줄이 내용에 이미 있을 수 있기 때문이다.
HEADER = "<!-- harness-prompt role={role} stage={stage} prompt_version={version} -->"
PREFIX_MARK = "<!-- prefix -->"
BODY_MARK = "<!-- body -->"
HISTORY_MARK = "<!-- history speaker={speaker} -->"


def render(
    *, role: str, stage: int, prompt: Prompt, history: Sequence[ChatTurn] = ()
) -> str:
    """파일에 쓸 텍스트. 순번을 담지 않으므로 같은 프롬프트면 같은 문자열이다.

    need_evidence 후속 턴(hop>0)은 prefix와 body 사이에 history **내용**을 턴마다 싣는다 —
    서버에 간 요청이 `[system]+history+[이번 turn]`이라, 빠지면 `eval/leakage`가 그 바이트를
    못 보고 #80의 「그 파일이 곧 실제 요청」이 거짓이 된다. hop0 로그 **참조**는 싣지 않는다
    — 참조는 순번을 들여 같은 요청의 해시가 실행 순서를 따라가지만, 내용은 그러지 않는다.
    history가 비면(hop0) 지금까지와 같은 문자열이다.
    """
    turns = [
        line
        for turn in history
        for line in (HISTORY_MARK.format(speaker=turn.speaker), turn.content)
    ]
    return "\n".join(
        (
            HEADER.format(role=role, stage=stage, version=prompt.prompt_version),
            PREFIX_MARK,
            prompt.prefix,
            *turns,
            BODY_MARK,
            prompt.body,
            "",
        )
    )


class PromptLog:
    """`{run_dir}/prompts/`에 호출당 1차 파일 하나. 재시도가 있으면 그 회차마다
    `_retryK`가 붙은 파일이 **같은 순번으로** 하나씩 더 붙는다(`write_retry`, #80) —
    실제로 서버에 간 요청을 재현하기 위해서다. 그래서 `*.md` 파일 수는 호출 수 이상이고,
    호출당 하나인 것은 `_retry`가 없는 **1차** 파일이다.

    **순번은 이 객체가 센다.** trace의 `seq`를 쓰지 않는 것은 그 값이 프롬프트와 무관한
    이벤트(전이·게이트·예산)까지 세기 때문이고, `IdFactory`를 쓰지 않는 것은 그쪽에
    종류를 하나 늘리면 `issued()`가 바뀌어 "종류별 발급 수" 대조가 달라지기 때문이다.

    v1 전제는 **순차 실행**이다(`trace.TraceWriter`·`RunContext.stage()`가 이미 같은
    전제 위에 있다). 단계를 동시에 돌리게 되면 여기 카운터에도 락이 필요하다.
    """

    def __init__(self, run_dir: Path) -> None:
        self._dir = Path(run_dir) / DIR_NAME
        self._seq = 0

    @property
    def directory(self) -> Path:
        return self._dir

    def write(
        self,
        *,
        role: str,
        stage: int,
        prompt: Prompt,
        hop: int = 0,
        history: Sequence[ChatTurn] = (),
    ) -> tuple[str, str]:
        """파일을 쓰고 `(run_dir 상대 경로, 내용 sha256)`을 돌려준다.

        **개행을 LF로 고정한다.** 기본값으로 두면 Windows에서 CRLF가 섞여, 같은 입력의
        두 실행이 장비에 따라 다른 `prompt_sha256`을 남긴다.

        hop>0이면 파일명에 `_hop{k}`가 붙는다 — `_retryK`와 같은 자리에 나란히 쌓인다
        (`…_hop1_retry1.md`). hop=0(기본값)은 지금까지의 이름·내용 그대로다.
        """
        self._seq += 1
        suffix = f"_hop{hop}" if hop else ""
        name = f"{self._seq:0{SEQ_WIDTH}d}_{role}{suffix}.md"
        text = render(role=role, stage=stage, prompt=prompt, history=history)
        self._dir.mkdir(parents=True, exist_ok=True)
        (self._dir / name).write_text(text, encoding="utf-8", newline="\n")
        return f"{DIR_NAME}/{name}", sha256_text(text)

    def write_retry(
        self,
        *,
        role: str,
        stage: int,
        prompt: Prompt,
        index: int,
        hop: int = 0,
        history: Sequence[ChatTurn] = (),
    ) -> tuple[str, str]:
        """재시도 회차(`index`=1..)의 프롬프트를 **직전 `write`와 같은 순번**으로 남긴다.

        `000N_role.md`(1차)와 `000N_role_retry1.md`·`…retry2.md`가 한 순번으로 묶여, 어느
        재시도가 어느 1차의 것인지 파일명만으로 붙는다. **카운터를 올리지 않는다** — 재시도가
        순번을 먹으면 다음 호출의 1차 번호가 재시도 수만큼 건너뛰어, 순번이 호출 수와
        어긋난다.

        같은 `prompts/` 아래이므로 `eval/leakage`의 스캔이 이 파일도 본다 — **실제로 서버에
        간 요청**이 누출 검증 대상이 되는 것이 (a)의 목적이다(#80). LF 고정은 `write`와 같은
        이유다(장비별 해시 흔들림 방지). hop 접미는 `write`와 같은 자리이고 `_retryK`보다
        앞에 온다.
        """
        suffix = f"_hop{hop}" if hop else ""
        name = f"{self._seq:0{SEQ_WIDTH}d}_{role}{suffix}_retry{index}.md"
        text = render(role=role, stage=stage, prompt=prompt, history=history)
        self._dir.mkdir(parents=True, exist_ok=True)
        (self._dir / name).write_text(text, encoding="utf-8", newline="\n")
        return f"{DIR_NAME}/{name}", sha256_text(text)

    def write_response(
        self, *, role: str, stage: int, index: int, hop: int = 0, text: str
    ) -> tuple[str, str]:
        """**실패한** 시도(`index`=0 1차, 1.. 재시도)의 응답 원문을 직전 `write`의 순번으로 남긴다.

        `000N_role[_hopK]_response.txt` · `…_response_retry{index}.txt`. 카운터를 안 올린다
        (`write_retry`와 같은 이유). 원문은 **바이트 그대로**다 — 개행을 바꾸지 않고, 모델이 낸
        외톨이 서로게이트도 `surrogatepass`로 써서 `UnicodeEncodeError`가 원 예외를 덮지 않게 한다.
        `stage`는 파일명에 안 쓰이고 `write_retry`와 호출 모양만 맞춘다. 호출자는 받은 문자열이 없는
        칸(`content: null`)을 부르지 않는다 — 그 칸은 파일이 없다.

        **누출 스캔에 넣지 않는다.** `eval/leakage`의 `RUN_GLOBS`(`*/prompts/*.md`)가 `.txt`를 안
        부르고, 이것은 모델 입력이 아니라 모델 출력이라 그 스캐너가 지키는 것의 매개가 아니다.
        """
        suffix = f"_hop{hop}" if hop else ""
        retry = f"_retry{index}" if index else ""
        name = f"{self._seq:0{SEQ_WIDTH}d}_{role}{suffix}_response{retry}.txt"
        data = text.encode("utf-8", errors="surrogatepass")
        self._dir.mkdir(parents=True, exist_ok=True)
        (self._dir / name).write_bytes(data)
        return f"{DIR_NAME}/{name}", sha256_digest(data)
