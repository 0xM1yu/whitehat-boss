"""렌더된 프롬프트를 run 산출물로 남긴다 (open-questions.md D19).

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
  `_canonical`을 `data` 키까지 확장할 것인가는 D14가 아직 열어 둔 판단이고, 여기서
  답을 만들어 버리면 안 된다.
- 실측 기준 콜당 **약 4.4 KB**다(2026-08-22 ATOM run: input 4,389토큰 / 4콜).
  `trace.jsonl`의 한 줄이 그만큼 부푼다.

## `prompt_ref`에 `run_id`를 넣지 않는다

돌려주는 참조는 **run_dir 상대**(`prompts/0001_scout.md`)다. `TraceEvent.run_id`가 이미
canonical 표현에 들어 있으므로 trace의 run 의존성 자체는 기존 성질이고, 이 변경이 거기
**새로 얹는 run 의존 문자열은 없다.**

## 남기는 것과 남기지 않는 것

- **prefix + body 전체**를 남긴다. 사실 블록만 남기면 D17이 지적한 산문 자리표시자
  (`{{TARGET}}`·`{{CAPSULE_SECTIONS}}`·`{{SOURCE}}`)가 기록에서 사라지는데, **정확히 그
  블록이 W1이 채운 자리**다.
- **응답은 남기지 않는다.** `Candidate`·`Finding`·`Decision`이 이미 StateStore에 통째로
  들어가고 usage·`gpu_ms`는 `model_call`에 있다. 사본을 만들면 정본을 물어야 한다.
- **호출이 실패해도 남는다.** 호출자가 모델을 부르기 **전에** 쓴다 — 실패 분기가
  "스키마 파싱 실패율"의 분자인데(`context.call_model`), 그 입력이 없으면 왜 실패했는지
  조사할 수 없다.

## 크기

콜당 프롬프트 크기는 **캡슐 예산과 함께 는다.** 실측 두 건:

| 어디 | 콜당 |
| --- | --- |
| ATOM `single_gpu_12g` (2026-08-22, input 4,389토큰 / 4콜) | ≈ **4.4 KB** |
| 로컬 `dev_docker` 스모크 (픽스처 대상, 6콜) | **2.4 ~ 4.8 KB** |

**상한은 재지 않았다.** 캡슐 예산은 scout 4,000 / auditor 24,000토큰인데
(`src/tokens.CHARS_PER_TOKEN`=4) 프롬프트는 그보다 크다 — 같은 내용이 산문 블록과 사실
블록에 **두 번** 실리기 때문이다(D17). 후보가 수백 건인 실대상에서 얼마가 되는지는
그 run을 돌려야 나온다(D15가 `tool_seconds`에 대해 같은 상태다).
`artifacts/`는 저장소 밖이므로 커밋 오염은 없다.

**prefix가 역할마다 중복 기록된다.** 역할당 한 번만 쓰고 참조하면 크게 줄지만, 그
절약이 필요하다는 **측정이 아직 없다.** 근거 없는 값으로 구조를 정하지 않는 것이 이
저장소의 규칙이다(D15: 장난감 대상의 측정에서 상한을 유도하면 지어낸 근거가 된다).
필요해지면 그때가 업그레이드 지점이다.
"""

from pathlib import Path

from src.ids import sha256_text
from src.interfaces.model_client import Prompt

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


def render(*, role: str, stage: int, prompt: Prompt) -> str:
    """파일에 쓸 텍스트. 순번을 담지 않으므로 같은 프롬프트면 같은 문자열이다."""
    return "\n".join(
        (
            HEADER.format(role=role, stage=stage, version=prompt.prompt_version),
            PREFIX_MARK,
            prompt.prefix,
            BODY_MARK,
            prompt.body,
            "",
        )
    )


class PromptLog:
    """`{run_dir}/prompts/`에 호출당 파일 하나.

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

    def write(self, *, role: str, stage: int, prompt: Prompt) -> tuple[str, str]:
        """파일을 쓰고 `(run_dir 상대 경로, 내용 sha256)`을 돌려준다.

        **개행을 LF로 고정한다.** 기본값으로 두면 Windows에서 CRLF가 섞여, 같은 입력의
        두 실행이 장비에 따라 다른 `prompt_sha256`을 남긴다.
        """
        self._seq += 1
        name = f"{self._seq:0{SEQ_WIDTH}d}_{role}.md"
        text = render(role=role, stage=stage, prompt=prompt)
        self._dir.mkdir(parents=True, exist_ok=True)
        (self._dir / name).write_text(text, encoding="utf-8", newline="\n")
        return f"{DIR_NAME}/{name}", sha256_text(text)
