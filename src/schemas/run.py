"""RunRecord 스키마 (agent-contracts.md §3, engineering-guide.md §5).

evaluation.md §6: "기록이 불완전한 run의 결과는 비교표에 넣지 않는다."
따라서 재현성 필수 항목은 전부 **필수 필드**다. 선택으로 두면 누락이 조용히 통과하고,
그 run은 나중에 비교표에서 버려진다 — 실행 비용을 쓰고 나서 알게 되는 것이 최악이다.
"""

from datetime import datetime
from typing import Annotated

from pydantic import Field, JsonValue, model_validator

from src.schemas.common import (
    HarnessModel,
    Identifier,
    NonEmptyStr,
    Sha256Digest,
    UtcModel,
)

# evaluation.md §2.2: 2×2 설계(§2.1)와 파이프라인 조건(§2.2)이 같은 라벨을 다른 축으로
# 썼다. 그 충돌은 축을 하나로 줄이며 소멸했다 — ADR 0011(2026-09-20).
# 2026-09-20: PM 결정으로 2×2 실험이 완전히 폐기되고 Scout 오케스트레이터로 전환되어
# `2x2` 축을 CONDITION_PATTERN에서 제거한다.
# `'`는 파일명·URL·쉘에서 깨지므로 `prime`으로 표기한다.
# `@` 접미사는 남긴다 — 지금은 `pipeline`뿐이지만 축이 다시 늘 때 자리를 미리 지운다.
CONDITION_PATTERN = r"^(B1|B1prime|B3|B3prime)@pipeline$"

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
    #
    # ⚠ **실서빙 프로필의 예산이 이 키를 안 적으면 0이고, `load_config`가 그것을 거부한다**
    # (`_check_real_serving_limits_are_positive`) — 임대 GPU 위에서 「검사 안 함」은 한도 없음이다.
    # 컨테이너 런타임(`tool_runner != fake`)의 0도 같은 가드가 막는다
    # (`check_container_runtime_caps_tool_time` · #436). 이 스키마가 0을 계속 받는 것은 옛
    # manifest와 Fake 런타임 프로필 때문이다.
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

    ⏵ 2026-10-08 — 「`temperature`·`top_p`만 보내므로」(2026-08-29 `7bc68c6`)는 낡았다.
    registry `sampling`이 선언한 모델은 `temperature`·`top_p`·`top_k`·`min_p`·
    `presence_penalty`를 요청에 싣는다(`RequestSampling` -> `extra_params`, `f695dab`
    2026-09-25 #261). `top_k` 선언은 `f59d63c`(2026-09-26)부터 -> `qwen3.8-27b`·
    `feyospace-v1-small` 둘(20). `repetition_penalty`는 여전히 안 보낸다. 응답 쪽 서술은
    재확인 안 함.

    **필드가 전부 optional인 것은 모델마다 선언하는 부분집합이 다르기 때문이다.** `None`은
    "그 모델의 `generation_config.json`이 이 키를 선언하지 않았다" = 서버가 vLLM 기본값을
    쓴다는 뜻이지 값이 0이라는 뜻이 아니다. 지어내지 않고 파일에 있는 것만 담는다.
    """

    temperature: float | None = Field(default=None, ge=0.0)
    top_p: float | None = Field(default=None, gt=0.0, le=1.0)
    # **`0`과 `-1`은 스키마 밖 값이 아니라 "비활성"이라는 정상 선언이다.** HF는 `top_k: 0`,
    # vLLM은 `-1`을 그 뜻으로 쓴다. `Sampling`(우리가 **보내는** 값)이었다면 `ge=1`이 맞지만
    # 이쪽은 **모델이 선언한 것을 그대로 받아 적는** 자리라, 정상 선언을 스키마 밖으로 밀어내면
    # 그 키가 기록에서 사라진다(`read_declared_sampling`은 검증 실패한 키만 버린다). 그러면
    # `temperature 0.7 · top_k 0`이 `top_k` 미선언(`None`)으로 남고, `top_k 0`만 선언한 모델은
    # 빈 `ModelSampling()` = "선언 없음"이 된다 — #99가 막으려던 오독을 반대 방향에서 재생산한다.
    top_k: int | None = Field(default=None, ge=-1)
    repetition_penalty: float | None = Field(default=None, gt=0.0)


class CompileFlags(HarnessModel):
    """그 run의 clang 계열 도구(build·clang-tidy·asan·gdb)가 쓴 컴파일 조건 (#74).

    **왜 남기나.** `container_tools._STD_ARGS` 주석이 자기 자신에 대해 이미 적어 뒀다 —
    *"판정 임계값은 아니지만 **분석 결과를 바꾸므로 조건 간 비교에 영향을 준다**"*.
    `-I`도 같은 성질이고, 없을 때 20건이 `'zlib.h' file not found`로 죽었으므로
    **있고 없고가 신호의 유무를 가른다**(#74의 2026-08-25 실측).

    **층위 둘을 갈라야 한다.** ① Evidence 단위(사후 감사)는 이미 보인다 — `_execute`가
    `$ {argv}`를 로그 각 절 첫 줄에 쓰고 그 내용이 `Evidence.digest`에 들어간다.
    ② run 단위(조건 간 비교표)가 안 보였다 — 두 run을 나란히 놓았을 때 컴파일 조건이
    같았는지를 **run마다 Evidence 로그를 열어야** 알 수 있었다. 이 모델은 ②만 연다.

    ⚠ **`compile_commands.json`으로 가는 길을 여기서 정하지 않는다.** `_STD_ARGS` 주석이
    *"결국 config로 가야 할 값"*이라 적었고 그 판단은 실저장소 시점으로 미뤘다(#74 닫힘 2026-10-12)
    — 이 모델은
    **지금 코드 상수인 것을 기록에 남길 뿐**이고 값의 출처를 옮기지 않는다.
    """

    # 언어 → 그 언어의 clang 호출에 간 `-std` 값. 세 도구가 같은 값을 쓴다.
    # **빈 dict는 "컴파일한 도구가 없었다"**(Fake)이고 필드 자체의 `None`과 다르다.
    std: dict[str, NonEmptyStr]
    # `-I`로 간 디렉터리 중 **대상이 바뀌어도 그대로인 것**.
    #
    # ⚠ **"실제로 어떤 `-I`가 갔나"가 아니다.** `_include_args()`는 대상 디렉터리와 마운트
    # 루트 둘을 내는데 앞엣것은 대상마다 달라 두 run을 나란히 놓는 데 쓸 수 없다 — 내용이
    # 같아도 값이 달라진다. 그래서 고정분만 담고, 실제 argv는 층위 ①(Evidence 로그)이
    # 답한다. **이름이 그 한계를 들고 있어야 한다** — `include_dirs`였다면 읽는 쪽이
    # "이게 전부"로 읽는다(CLAUDE.md §2.2가 막는 그 오독).
    fixed_include_dirs: list[NonEmptyStr]


class RunRecord(UtcModel):
    """한 번의 실행에 대한 재현성 기록.

    **전이 시 주의**: `finished_at`은 실행이 끝난 뒤 채워지므로 반드시 전이를 탄다.
    `HarnessModel.transition()`을 써야 시간 순서 검증이 실제로 돈다.
    """

    run_id: Identifier
    # 예: "B3prime@pipeline". 축은 지금 `pipeline` 하나이고, 늘 때를 대비해 라벨에 남긴다.
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
    # 하네스가 요청에 **보낸** 샘플링 (config `budgets.yaml`의 `sampling`) — **프로필 공통값이다.**
    # registry가 모델별 `sampling`을 선언한 역할은 `temperature`·`top_p`가 그 값으로 덮여 나가고,
    # 그 역할에 실제로 보낸 값은 `model_request_params[role]`에 있다. 비교할 때는 둘을 합쳐 읽는다.
    sampling: Sampling
    # 역할 → 그 모델이 `generation_config.json`에 **선언한** 샘플링 (#99). `sampling`이
    # 보낸 값이라면 이쪽은 서버가 모델 파일에서 적용하는 값이다 — `top_k`·`repetition_penalty`는
    # 하네스가 안 보내고 요청·응답에도 안 실려 이 필드로만 기록에 남는다.
    # ⏵ 2026-10-08 — 위 두 줄(2026-08-29 `7bc68c6`)은 `top_k`에 대해 낡았다. registry
    # `sampling`이 요청에 실린다(`RequestSampling` -> `extra_params`, `f695dab` 2026-09-25 #261):
    # `temperature`·`top_p`·`top_k`·`min_p`·`presence_penalty`. `top_k`를 처음 선언한 것은
    # `f59d63c`(2026-09-26) — 그 모델을 쓰는 역할은 보낸 `top_k`가 `model_request_params`에도
    # 남는다. `repetition_penalty`는 여전히 안 보낸다. 응답 쪽 서술은 재확인 안 함.
    #
    # **`| None`이고 기본값 `None`인 것은 device·repo_dirty와 같은 관용구다.** `None`은
    # "이 manifest는 기록하지 않았다"(이 필드 이전에 쓰인 기록)이고, `{}`는 "기록했으나
    # 선언 샘플링을 읽은 역할이 없었다"로 서로 다르다. 빠지는 길은 둘이다 -> (a) 읽기 시도
    # 없음: `PROMPTED_ROLES` 밖·서빙 모델 없음(Fake 포함) (b) 읽었으나 `None`: `source` 없음·
    # 경로 리졸브 실패·파일 없음·파일 깨짐(`declared_sampling_by_role` docstring).
    # 읽었으나 선언이 0개인 역할은 빈 `ModelSampling()`으로 dict에 남으므로 `{}`가 아니다 —
    # 새 run은 항상 dict(비어도)를 남기므로 `None`은 옛 manifest에서만 나온다. 그래서 이
    # 필드를 늘려도 `test_a_manifest_written_before_tool_seconds_still_loads`가 지키는
    # "옛 manifest는 로드된다"가 깨지지 않는다.
    model_sampling: dict[str, ModelSampling] | None = None
    # 역할 → registry가 선언해 하네스가 **요청 본문에 실은** `reasoning_effort`.
    #
    # **`sampling`·`model_sampling` 어느 쪽도 이 값을 담을 수 없다.** `sampling`은 프로필
    # 공통(`budgets.yaml`)인데 이 값은 **모델별**(registry)이고, `model_sampling`은 모델이
    # `generation_config.json`에 **선언한** 값이라 「우리가 보낸 것」이 아니다. `thresholds`도
    # 아니다 — 그쪽은 `dict[str, float]`이라 문자열이 안 들어간다.
    #
    # **왜 남기나.** `engineering-guide.md` §5가 `RunRecord`에 "random seed와 sampling 설정"을
    # 요구하고 "하나라도 빠지면 그 run의 결과는 비교표에 넣지 않는다"로 닫는다. 이 값은
    # 출력을 바꾸고 모델마다 다르므로, 안 남기면 `high`로 돈 run과 기본값으로 돈 run이
    # manifest에서 같은 모양이 된다 — `ModelSampling` docstring이 `top_k`에 대해 적은
    # "샘플링 차이가 모델 차이로 섞인다"와 같은 자리다.
    #
    # **`| None`이고 기본값 `None`인 것은 `model_sampling`과 같은 관용구다.** `None`은
    # "이 manifest는 기록하지 않았다"(이 필드 이전의 기록)이고, `{}`는 "기록했으나 선언한
    # 역할이 없었다"로 서로 다르다 — 새 run은 항상 dict(비어도)를 남긴다.
    #
    # **`PROMPTED_ROLES`만 담는다** — `prompt_versions()`와 같은 사유다. embedding·reranker는
    # `VllmEmbeddingClient`·`VllmRerankerClient`를 타고 이 파라미터를 안 보내므로, 그 역할의
    # "effort"를 적으면 거짓 정보다(`config.prompt_versions` docstring의 그 판단).
    model_reasoning_effort: dict[str, NonEmptyStr] | None = None
    # 역할 → registry가 선언해 요청에 **얹은** 키(`ModelEntry.request_params()` — 샘플링 덮기·
    # `chat_template_kwargs`·`thinking_token_budget`). 규칙은 바로 위와 같다: 선언한 역할만,
    # `None`은 이 필드 이전의 manifest. `sampling`(프로필 공통)을 덮은 값도 여기 남는다.
    model_request_params: dict[str, dict[str, JsonValue]] | None = None
    # 그 run의 clang 계열 도구가 쓴 컴파일 조건 (#74). 사유와 한계는 `CompileFlags`에
    # 있다 — 요점은 **사후 감사로는 잡히고 비교표로는 안 잡히던** 축을 여는 것이다.
    #
    # **어댑터에게 묻는다.** `tool_images`·`model_sampling`과 같은 경로다 — 러너는
    # `src/adapters/`를 import 하지 않으므로(계층 규칙) `Deps`가 나른다. 러너가 상수를
    # 다시 적으면 어댑터가 무엇을 실제로 넘겼는지와 갈라지고, **갈라진 재현성 기록은 없는
    # 것보다 나쁘다**(`factory`가 `images()`에 대해 적은 그 문장).
    #
    # **`| None`이고 기본값 `None`인 것은 `model_sampling`과 같은 관용구다.** `None`은
    # "이 manifest는 기록하지 않았다"(이 필드 이전의 기록)이고, 빈 `CompileFlags`는
    # "기록했으나 컴파일한 도구가 없었다"(Fake 프로필)로 서로 다르다 — 새 run은 어댑터에게
    # 물어 항상 값을 남기므로 `None`은 **옛 manifest에서만** 나온다. 그래서 이 필드를
    # 늘려도 `test_a_manifest_written_before_tool_seconds_still_loads`가 지키는 "옛
    # manifest는 로드된다"가 깨지지 않는다.
    #
    # `incomplete_fields()`에는 넣지 않는다 — `tool_runner`와 같은 사유다. Fake로 돈 run은
    # 이미 `tool_images`가 비어 경고에 걸리고, 같은 사실을 두 줄로 내면 진짜 문제가 묻힌다.
    compile_flags: CompileFlags | None = None
    # `--slices`로 돈 run의 슬라이서 산출 — `{status, run_signature, slices}`(ADR 0012 결정 1).
    # CPG가 못 본 줄은 모델도 못 본다 — `partial`로 돈 run이 그 사실 없이 비교표에 서지 않게
    # 남긴다. `None`은 슬라이스 없이 돈 run(파일·심볼 타깃)이다.
    slicer: dict[str, str | int | None] | None = None
    # 채점기(oracle)를 켠 run의 제출 요약 (#386) —
    # `{vul_submitted, vul_failed, final, final_reason, record}` - 여기에 **0이 아닐 때만**
    # `vul_intermediate`(중간 gen 시도 수)·`vul_candidates`(최종 후보 수 - `vul_intermediate`가
    # 있으면 0이어도 있다)·
    # `vul_observation_failed`(중간 gen 판정 조회 실패·빈 답)·`vul_empty_poc`(0바이트라 안 낸 수)가
    # 붙는다.
    # `final`은 `scored`(판정을 받았다) · `failed`(지정했는데 판정을 못 읽었다) ·
    # `none`(지정된 후보가 없었다 — 후보가 없었거나 후보가 둘 이상인데 그중 등급·
    # `confidence`·Critic 판정 중 모르는 것이 있었다. 사유는 `final_reason` — `no_poc`·
    # `all_vul_submissions_failed`·`rate_limited`·`grade_unknown`) ·
    # `not_reached`(최종 단계가 안 불렸다 — 8단계가 끝까지 안 돌았다. 단 예산·서버 쪽 게이트웨이
    # 오류로 끊겨도 이미 낸 최종 후보가 있거나 추가 PoV 대상에서 끊긴 run은 최종 단계를 부르고
    # 끝난다)이고 `record`는 사이드카의 run_dir 상대 경로다.
    # 카나리에 기댄 run은 `canary_path`(풀어 쓴 경로)·`canary_sha256`(게이트가 **검사한 바이트의**
    # 해시)가 더 붙는다 - 점수가 어느 보고서에 기댔는지 사후에 변호하는 값이다(#487).
    # **판정 값(종료 코드·`success`)은 여기에 없다** — 서버가 돌려준 값이라 사이드카에 두고, 이
    # 매니페스트에는 **무슨 일이 있었는지**(몇 건을 냈고 몇 건이 실패했고 최종이 있었나)만 싣는다.
    # 그래야 「최종 PoC가 없다」·「제출이 전부 실패했다」가 `trace.jsonl`을 안 열고도 보인다.
    #
    # **`| None`이고 기본값 `None`인 것은 `slicer`와 같은 관용구다.** `None`은 채점기를 안 켠
    # run이고 이 필드 이전의 manifest도 같다 — 둘 다 「제출 안 함」이라 구별할 이유가 없다.
    # 그래서 이 필드를 늘려도 manifest 하위호환이 깨지지 않는다.
    oracle: dict[str, str | int | None] | None = None
    # `run --keep-responses`로 성공 응답까지 남기게 한 역할(#612). 스위치가 `prompts/` 파일 집합과
    # `model_call.response_refs` 키를 바꾸므로 박제한다. `None` = 안 켰다(이 필드 이전의 manifest도
    # 같다 - `slicer`와 같은 관용구라 하위호환이 안 깨진다).
    keep_responses: list[str] | None = None
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
