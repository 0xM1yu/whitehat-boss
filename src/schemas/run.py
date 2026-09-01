"""RunRecord 스키마 (agent-contracts.md §3, engineering-guide.md §5).

evaluation.md §6: "기록이 불완전한 run의 결과는 비교표에 넣지 않는다."
따라서 재현성 필수 항목은 전부 **필수 필드**다. 선택으로 두면 누락이 조용히 통과하고,
그 run은 나중에 비교표에서 버려진다 — 실행 비용을 쓰고 나서 알게 되는 것이 최악이다.
"""

from datetime import datetime
from typing import Annotated

from pydantic import Field, model_validator

from src.schemas.common import (
    HarnessModel,
    Identifier,
    NonEmptyStr,
    Sha256Digest,
    UtcModel,
)

# evaluation.md §2.2: 2×2 설계(§2.1)와 파이프라인 조건(§2.2)이 같은 라벨을 다른 축으로
# 쓴다. 어느 정의인지 라벨에 박아 두지 않으면 비교표가 섞인다. 라벨 통합은 #59(open-questions).
# `'`는 파일명·URL·쉘에서 깨지므로 `prime`으로 표기한다.
CONDITION_PATTERN = r"^(B1|B1prime|B3|B3prime)@(2x2|pipeline)$"

# architecture.md §3의 파이프라인 단계 번호는 0~9로 고정이다.
_MAX_STAGE = 9


class Budget(HarnessModel):
    """run 단위 계산 예산 (architecture.md §5 Budget Ledger).

    evaluation.md §2.3의 공정성 규칙 1: 모든 조건은 동일 예산에서 실행한다.
    """

    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    gpu_seconds: int = Field(ge=0)
    # 컨테이너 도구(semgrep·clang-tidy·build·asan)의 실행 시간 상한.
    #
    # **`gpu_seconds`와 나눈 이유**: 2026-08-17까지 도구 시간이 `gpu_seconds`로 청구되고
    # 있었는데 그 네 도구는 GPU를 쓰지 않는다. evaluation.md §5.3의 "validated/proven
    # finding당 GPU-hour"가 그 값을 분자로 쓰므로, 나누지 않으면 핵심 비용 지표가 CPU
    # 시간으로 오염된 채 확정된다.
    #
    # **옵셔널인 것은 기존 manifest 때문이다.** 필수로 넣으면 이 필드가 없던 시절에 쓰인
    # `run_manifest.json`이 `model_validate`에서 통째로 실패한다. `Benchmark.measured_on`·
    # `ModelEntry.source`가 같은 이유로 기본값을 뒀다. 기본값 0은 `BudgetLedger._overrun`의
    # 관용구("상한 0은 검사하지 않는다")와 맞고, 옛 run에 대해서는 **"도구 상한이 없었다"는
    # 사실**이다 — 지어낸 값이 아니다.
    tool_seconds: int = Field(default=0, ge=0)


class Sampling(HarnessModel):
    """샘플링 설정 (engineering-guide.md §5 "random seed와 sampling 설정").

    seed와 달리 이 값들은 모델 게이트웨이 요청 파라미터다. 조건 간 비교에서 고정해야
    하므로 RunRecord에 남긴다.
    """

    temperature: float = Field(ge=0.0)
    top_p: float = Field(gt=0.0, le=1.0)


class ModelSampling(HarnessModel):
    """한 모델이 자기 `generation_config.json`에 **선언한** 샘플링 (#99).

    `Sampling`이 하네스가 요청에 **보내는** 값이라면, 이쪽은 모델 파일이 선언하고
    vLLM이 기동 시 적용하는 값이다 — 하네스는 `temperature`·`top_p`만 보내므로 `top_k`·
    `repetition_penalty`는 **모델 파일에서 와서 요청에도 응답에도 안 실린다.** 그 값이
    모델마다 다르고(예: Qwen3-8B는 `top_k 20`, Foundation-Sec·granite는 없음) 기록에
    안 남으면 모델 비교에서 샘플링 차이가 모델 차이로 섞인다(evaluation.md §2.3).

    **필드가 전부 optional인 것은 모델마다 선언하는 부분집합이 다르기 때문이다.** `None`은
    "그 모델의 `generation_config.json`이 이 키를 선언하지 않았다" = 서버가 vLLM 기본값을
    쓴다는 뜻이지 값이 0이라는 뜻이 아니다. 지어내지 않고 파일에 있는 것만 담는다.
    """

    temperature: float | None = Field(default=None, ge=0.0)
    top_p: float | None = Field(default=None, gt=0.0, le=1.0)
    # **`0`과 `-1`은 스키마 밖 값이 아니라 "비활성"이라는 정상 선언이다.** HF는 `top_k: 0`,
    # vLLM은 `-1`을 그 뜻으로 쓴다. `Sampling`(우리가 **보내는** 값)이었다면 `ge=1`이 맞지만
    # 이쪽은 **모델이 선언한 것을 그대로 받아 적는** 자리라, 정상 선언을 스키마 밖으로 밀어내면
    # 그 모델의 기록이 통째로 사라진다(`read_declared_sampling`이 검증 실패 시 전부 버린다).
    # 그러면 `temperature 0.7 · top_k 0`을 선언한 모델이 "선언 없음"으로 남아, #99가 막으려던
    # 오독을 정확히 반대 방향에서 재생산한다.
    top_k: int | None = Field(default=None, ge=-1)
    repetition_penalty: float | None = Field(default=None, gt=0.0)


class RunRecord(UtcModel):
    """한 번의 실행에 대한 재현성 기록.

    **전이 시 주의**: `finished_at`은 실행이 끝난 뒤 채워지므로 반드시 전이를 탄다.
    `HarnessModel.transition()`을 써야 시간 순서 검증이 실제로 돈다.
    """

    run_id: Identifier
    # 예: "B3prime@pipeline". 어느 정의의 조건인지 라벨에 포함한다.
    condition: Annotated[str, Field(pattern=CONDITION_PATTERN)]
    # 이 run이 **어느 프로필로** 돌았는가 (`--profile`이 고른 `config/profiles.yaml` 항목).
    #
    # evaluation.md §2.3 규칙 2가 "조건 간 차이는 **프로필과 단계 활성화만**"으로 비교를
    # 정의하는데, 그 축의 절반이 산출물 셋(`run_manifest.json`·`trace.jsonl`·`report.md`)
    # 어디에도 없었다 — 프로필 이름이 **조작자가 친 쉘 명령에만** 있었다.
    #
    # 바로 아래 `tool_runner`가 필수인 사유(ADR 0005)가 **"프로필에 적힌 값과 실제 값이
    # 갈릴 수 있다"**인데, **갈림의 기준인 프로필 자체가 없으면 그 예외를 읽을 수 없다.**
    # 예외만 남고 기준이 없던 상태다. `cli bench`는 이미 남기고 있었다(`bench_report.py`).
    #
    # **`model_checksums`로 항상 복원되지는 않는다.** 그쪽은 역할→checksum만 담아
    # `adapters.retriever`(fake ↔ hybrid)를 못 가른다. 그 축은 캡슐의 `related` 섹션 유무와
    # embedding·reranker 호출 여부를 갈라 **모델이 본 것**을 바꾼다 — `related`는
    # `hybrid_retriever.LEVEL_SECTIONS`에만 있다. `production` 플래그·예산 프로필도 같다.
    #
    # **그래프 섹션(`callers`·`callees`)은 이 축으로 갈리지 않는다.** 두 구현이
    # `index_graph.graph_section()`을 공유하고 `LEVEL_SECTIONS`의 그 자리도 같다 —
    # 갈린다고 적었던 초판은 틀렸다(2026-09-01 감사).
    #
    # **`| None`이고 기본값 `None`인 것은 `model_sampling`과 같은 관용구다** —
    # `tool_runner`·`device`의 "옛 manifest는 로드 실패가 올바른 결과다"를 여기 쓰지 않는다.
    # 가르는 기준은 **새 run에서 빌 수 있는가**다. `device`는 `--device`를 안 주면 실제로
    # 비므로 `None`이 "확인 못 함"이라는 값이고, 그래서 기본값을 안 준다. 이 필드는
    # `config.profile_name`(`NonEmptyStr`)에서 무조건 유도되어 **빌 수가 없다.**
    #
    # 필수로 올리지 않는 이유는 하나다 —
    # `tests/fixtures/manifests/run_20260817_0004.json`이 **manifest 하위호환의 유일한 CI
    # 강제 장치**인데(`tests/test_schemas.py`의 그 fixture 주석), 새 run이 어차피 항상
    # 채우는 필드를 위해 그것을 깨는 것은 얻는 것이 없다.
    #
    # **§0(추정을 기록으로 승격)을 이 자리의 근거로 들지 않는다.** 초판이 그렇게 적었는데
    # 그 fixture의 프로필은 `tests/test_schemas.py`가 `dev_fake`로 적어 두었으므로, 하필
    # 지목한 사례에 대해서는 채워 넣는 것이 추정이 아니다 — 가장 센 라벨을 가장 약한 사례에
    # 붙인 문단이었다(2026-09-01 리뷰). 살아남는 논거만 위에 남겼다.
    # 새 run은 config에서 항상 유도되어 반드시 채워지므로 **`None`은 옛 manifest에서만
    # 나온다.** 그래서 `None`은 "확인 못 함"이 아니라 **"이 기록은 남기지 않았다"**다.
    #
    # `incomplete_fields()`에는 넣지 않는다 — `tool_runner`와 같은 사유로, 새 run에서는
    # 빌 수가 없어 경고가 영원히 안 뜬다. 뜨지 않는 경고는 진짜 문제를 묻는다.
    profile: NonEmptyStr | None = None
    # git commit SHA-1 (40 hex). sha256 digest와 형식이 다르므로 별도 제약이다.
    repo_commit: Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
    # 하네스 저장소에 미커밋 변경이 있었는가. `repo_commit`은 HEAD만 읽고 `source_hash`는
    # *분석 대상*만 잡으므로, 그 사이에 "하네스 자신이 dirty했다"는 사실이 빠져 있었다.
    # 설정을 고친 채로 돈 run이 manifest만 보면 재현 가능해 보이던 것이 이 필드의 계기다.
    #
    # `bool`이 아니라 `bool | None`인 것은 의도다. git이 없거나 저장소가 아닐 수 있는데
    # 그 경우를 `False`로 적으면 "깨끗하다"는 적극적인 거짓말이 된다. `None`이 "확인 못 함"이다.
    # 기본값을 두지 않는 것도 의도다 — 이 모듈 docstring대로 재현성 항목은 전부 필수 필드다.
    repo_dirty: bool | None
    # 분석 대상 소스 트리의 해시. repo_commit만으로는 미커밋 변경을 잡지 못한다.
    source_hash: Sha256Digest
    # 역할 → model·tokenizer·quantization checksum
    model_checksums: dict[str, Sha256Digest]
    # 역할 → prompt_version
    prompt_versions: dict[str, NonEmptyStr]
    # 도구 이름 → 컨테이너 이미지 digest
    tool_images: dict[str, Sha256Digest]
    # 그 run이 **실제로 쓴** 도구 런타임 (`fake` | `docker` | `singularity` | `podman`).
    #
    # 닫힌 집합은 `config.AdapterSelection.tool_runner`가 들고 있다. 여기서 `Literal`을
    # 다시 적지 않는 것은 `src/schemas/`가 `src/config.py`를 import 할 수 없기 때문이고
    # (config가 이 모듈을 import 한다), 같은 열거를 두 곳에 두면 갈라진다.
    #
    # 필수 필드인 이유는 두 가지다.
    #
    # 1. `--tool-runner` 오버라이드가 프로필에 적힌 값과 실제 값을 갈라놓을 수 있다
    #    (ADR 0005). evaluation.md §2.3 규칙 2("조건 간 차이는 프로필과 단계 활성화만")에
    #    예외를 여는 것이므로, 기록에 남지 않으면 나중에 비교표에서 이 run들을 구분할 수
    #    없다.
    # 2. **오버라이드가 없어도 필요하다.** `tool_images`의 digest는 런타임 사이에서
    #    비교할 수 없다 — Docker는 이미지 config의 sha256을, Singularity는 SIF 파일의
    #    sha256을 적는다(STATUS.md §10. 같은 이미지가 다른 값으로 잡힌 실측이 있다).
    #    런타임이 함께 기록되면 최소한 digest를 런타임별로 묶어서 볼 수는 있다. §10의
    #    해법은 아니지만 그 전제 조건이다.
    #
    # `incomplete_fields()`에는 넣지 않는다. 값이 config에서 항상 유도되어 빌 수가 없고,
    # `fake`로 돈 run은 이미 `tool_images`가 비어 경고에 걸린다 — 같은 사실을 두 줄로
    # 내면 진짜 문제가 묻힌다(runner.py의 같은 판단 참조).
    #
    # **이 필드를 옵셔널로 낮추지 마라.** 이 필드가 생기기 전(2026-08-14 이전)에 쓰인
    # manifest는 여기서 로드에 실패하는데, **그것이 올바른 결과다.** 읽히게 만드는 유일한
    # 방법은 없는 사실을 지어내 소급 편집하는 것이고, 이 필드가 존재하는 이유가 정확히
    # "`tool_images` digest로 유도할 수 없다"는 것이다(ADR 0005). 옵셔널로 낮추면 그
    # ADR이 무효가 된다 — 오버라이드가 걸린 run과 아닌 run을 사후에 구분할 수 없게 된다.
    tool_runner: NonEmptyStr
    # 이 run이 **어느 장비에서** 돌았는가. `--device`가 준 라벨이고, 없으면 `None`이다.
    #
    # 두 장비가 모두 지원 대상이므로(docs/portability.md) 같은 프로필·같은 조건으로 돈 두 run이
    # 다른 하드웨어의 결과일 수 있다. 그런데 기록에는 그것을 가릴 것이 하나도 없었다 —
    # `model_checksums`는 설계상 장비 불변이고 endpoint는 애초에 남지 않는다. 대학
    # 학생이 Slurm에서 낸 결과와 팀의 ATOM 결과가 비교표에서 섞이는 것을 막는 필드다.
    #
    # **호스트명으로 자동으로 채우지 않는다.** 그러면 한 필드에 "사람이 붙인 장비 라벨"과
    # "하네스 프로세스가 돈 호스트명"이라는 **서로 다른 두 사실**이 들어가고, 기록만 보면
    # 어느 쪽인지 구분할 수 없다. 게다가 그 둘은 자주 어긋난다 — 로그인 노드에서 하네스를
    # 돌리면 GPU 작업이 한 톨도 일어나지 않은 기계 이름이 적힌다. `None`은 `repo_dirty`의
    # `None`과 같은 뜻이다: **확인 못 함.** 값과 섞지 않는다.
    #
    # `tool_runner`와 달리 `incomplete_fields()`에 들어간다. 그쪽은 config에서 항상
    # 유도되어 빌 수가 없지만, 이 값은 사람이 안 주면 실제로 빈다.
    #
    # **`str | None`이지 옵셔널 필드가 아니다.** 기본값을 주지 않는 것이 요점이다 —
    # `None`은 "확인 못 함"이라는 **명시적인 값**이어야 하고, 기본값이 있으면 안 준 것과
    # 모르는 것이 합쳐진다(`repo_dirty`가 `bool | None`인 것과 같은 관용구).
    #
    # **이 필드를 없애거나 기본값을 주지 마라.** 이 필드가 생기기 전(2026-08-15 이전)에
    # 쓰인 manifest는 로드에 실패하는데 **그것이 올바른 결과다.** 그 run들이 어느 장비에서
    # 돌았는지는 아무도 모르고, 지어내면 evaluation.md §2.3 규칙 5("장비가 다른 run은 같은
    # 비교 셀에 넣지 않는다")가 거짓 위에 서게 된다(ADR 0006).
    device: str | None
    budget: Budget
    # 해석된(resolved) 임계값. 예: {"scout_threshold": 0.5}. 값은 config에서 오고
    # 여기에는 그 run에서 실제로 쓰인 값을 박제한다.
    thresholds: dict[str, float]
    seed: int
    # 하네스가 요청에 **보낸** 샘플링 (config `budgets.yaml`의 `sampling`).
    sampling: Sampling
    # 역할 → 그 모델이 `generation_config.json`에 **선언한** 샘플링 (#99). `sampling`이
    # 보낸 값이라면 이쪽은 서버가 모델 파일에서 적용하는 값이다 — `top_k`·`repetition_penalty`는
    # 하네스가 안 보내고 요청·응답에도 안 실려 이 필드로만 기록에 남는다.
    #
    # **`| None`이고 기본값 `None`인 것은 device·repo_dirty와 같은 관용구다.** `None`은
    # "이 manifest는 기록하지 않았다"(이 필드 이전에 쓰인 기록)이고, `{}`는 "기록했으나
    # 선언 샘플링을 읽을 실서빙 모델이 없었다"(Fake 프로필·`source` 없는 모델)로 서로 다르다 —
    # 새 run은 항상 dict(비어도)를 남기므로 `None`은 옛 manifest에서만 나온다. 그래서 이
    # 필드를 늘려도 `test_a_manifest_written_before_tool_seconds_still_loads`가 지키는
    # "옛 manifest는 로드된다"가 깨지지 않는다.
    model_sampling: dict[str, ModelSampling] | None = None
    # KB 스냅샷 버전 태그 (knowledge-base.md §7.4).
    kb_snapshot: NonEmptyStr
    started_at: datetime
    # 아래 둘은 실행 중에는 비어 있다. 완주하면 finished_at만,
    # 예산 소진 등으로 중단되면 stopped_after까지 찬다(architecture.md §5 Budget Ledger).
    finished_at: datetime | None = None
    # 중단된 파이프라인 단계 번호 (architecture.md §3의 0~9).
    stopped_after: int | None = Field(default=None, ge=0, le=_MAX_STAGE)

    @model_validator(mode="after")
    def _finished_after_started(self) -> "RunRecord":
        if self.finished_at is not None and self.finished_at < self.started_at:
            raise ValueError("finished_at이 started_at보다 이르다")
        return self

    @property
    def is_complete(self) -> bool:
        """비교표에 넣을 수 있는 run인가 (evaluation.md §6).

        필수 항목은 스키마가 이미 강제하므로, 남는 조건은 "끝났는가" 하나다.
        중단된 run(`stopped_after`가 있는)은 부분 결과이므로 비교 대상이 아니다.
        """
        return self.finished_at is not None and self.stopped_after is None
