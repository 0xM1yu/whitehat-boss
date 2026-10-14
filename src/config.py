"""config/*.yaml 로더.

engineering-guide.md §8.5: "하드코딩 금지 — 임계값·예산·모델은 `config/`에서 온다."
이 모듈이 그 규칙의 단일 진입점이다. 노드도 어댑터도 YAML을 직접 읽지 않는다.

설정 모델은 `src.schemas`의 `HarnessModel`을 상속한다. `extra="forbid"` 덕분에
YAML의 오타 필드가 조용히 무시되지 않고 로드 시점에 실패한다 — 여러 곳이 같은 설정
파일을 건드리므로 조용한 무시가 가장 비싼 실패다.

`Budget`·`Sampling`은 `src.schemas.run`의 것을 그대로 쓴다. 같은 값이 `RunRecord`에
박제되므로(evaluation.md §6) 별도 타입을 두면 두 정의가 갈라진다.
"""

import re
from datetime import datetime
from pathlib import Path
from typing import ClassVar, Literal

import yaml
from pydantic import Field, JsonValue, model_validator

from src.paths import safe_join
from src.schemas.common import HarnessModel, NonEmptyStr, Sha256Digest
from src.schemas.enums import CapsuleLevel
from src.schemas.run import Budget, Sampling

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"

# 프로필에 반드시 있어야 하는 역할. embedding·reranker는 Retriever 구현에 따라 없을 수 있다.
#
# **`scout`이 여기서 빠졌다.** 4단계는 여전히 필수지만 그 자리를 채우는 방식이 둘이 됐고
# (ADR 0010 §3), "둘 중 하나"는 이 튜플로 표현되지 않는다. 대신 아래
# `ProfileEntry._declares_one_scout_arrangement`가 본다 — `scout`을 여기 남겨 두면 풀
# 프로필이 로드 자체를 못 한다.
REQUIRED_ROLES = ("auditor", "critic")

# `retriever: hybrid`일 때만 추가로 요구되는 역할 (ProfileEntry의 검증기 참조).
RETRIEVAL_ROLES = ("embedding", "reranker")

# 4단계 Scout 자리를 채우는 두 방식. 프로필은 **단일 `scout` 하나 아니면 풀 셋**을
# 선언한다(ADR 0010 §3). 두 상수를 붙여 두는 것은 그 배타가 한 화면에서 읽혀야 하기
# 때문이다.
#
# 어느 멤버가 어느 CWE 계열인지는 여기서 정하지 않는다 — 정본은 PROJECT-CONTEXT.md §1 표이고
# 계약은 `prompts/<role>/v2/prefix.md`가 들고 있다. 이 상수가 아는 것은 "누가 풀
# 멤버인가"뿐이라, 계열이 재배치돼도 여기는 안 움직인다.
SCOUT_ROLE = "scout"
SCOUT_POOL_ROLES = ("scout_memory", "scout_boundary", "scout_injection")

# 프롬프트를 받고 구조화 출력을 내는 역할. `ModelClient`가 만들어지는 유일한 대상이다.
#
# `Role` 열거형과 다르다 — `Role.JUDGE`는 Evidence Gate가 결정론적이라 모델을 부르지
# 않는다. `RETRIEVAL_ROLES`와도 다르다 — embedding·reranker는 프롬프트도 출력 스키마도
# 없다(ADR 0004). 이 셋을 구분하지 않으면 `RunRecord.prompt_versions`에 존재하지 않는
# 프롬프트의 버전이 적힌다.
#
# `tests/test_config.py::test_prompted_roles_matches_prompt_directories`가 이 상수와
# `prompts/` 하위 디렉터리의 일치를 강제한다.
#
# **풀 셋이 여기 빠지면 그 역할은 `ModelClient`를 아예 못 받는다.**
# `adapters.factory.build_deps()`가 이 상수를 돌며 클라이언트를 만들고 없는 역할은
# 조용히 건너뛰므로(`if role in config.profile.roles`), 빠뜨려도 아무것도 안 터지고
# 4단계가 빈손으로 돈다.
PROMPTED_ROLES = (
    "scout",
    "scout_memory",
    "scout_boundary",
    "scout_injection",
    "auditor",
    "critic",
    "single",
    "pov",
)

# 이 스킴이면 FakeModelClient로 간다. 역할별로 갈리므로 프로필 안에서 혼용이 가능하다.
FAKE_SCHEME = "fake://"


class ConfigError(Exception):
    """설정 파일이 없거나 참조가 깨졌다. 삼키지 않고 기동 시점에 터뜨린다."""


class Benchmark(HarnessModel):
    """model-serving.md §5의 실측 기록.

    전 항목이 0이면 아직 이 장비에서 재보지 않았다는 뜻이다. §3 체크리스트가
    블로킹이므로(`docs/open-questions/R/R1.md`) 값이 0인 모델은 production 프로필에 넣지 않는다.
    """

    measured_at: datetime | None = None
    # **어느 장비에서 쟀는가.** `measured_at`(언제)과 대칭이다.
    #
    # 이 값이 없으면 registry의 수치가 어느 하드웨어 기준인지 기록에서 사라진다. 두 장비가
    # 모두 지원 대상인 이상(docs/portability.md) `--apply`를 어디서 치느냐에 따라 같은 필드에
    # 다른 장비의 값이 들어가는데, 그 사실이 남지 않으면 다음 사람이 이 수치로 다른 장비를
    # 예측하게 된다.
    #
    # **막지 않고 기록한다.** `--tool-runner`도 막지 않고 `RunRecord.tool_runner`에 남기고
    # (ADR 0005), `repo_dirty`도 dirty 실행을 막지 않고 기록만 한다. 여기서만 금지로 가면
    # 이 저장소가 지켜 온 논법이 깨진다 — 대신 `doctor`가 현재 장비와 다르면 경고한다.
    #
    # 기본값이 `None`인 것은 `measured_at`과 같은 이유다. 기존 registry 항목에 이 키가
    # 없으면 로드가 통째로 실패하고, `None`은 "장비 미상"이라는 정직한 값이다.
    measured_on: NonEmptyStr | None = None
    tok_s_single: float = Field(default=0.0, ge=0.0)
    tok_s_batch32: float = Field(default=0.0, ge=0.0)
    mem_gb_ctx8k: float = Field(default=0.0, ge=0.0)
    # **`tok_s_batch32`가 어떤 캐시 상태에서 나온 값인가** (이슈 #128).
    #
    # 2026-09-02 ATOM에서 같은 서버·같은 인자로 돌린 세 판의 `tok_s_batch32`가
    # 59.45 / 68.64 / 107.32로 **1.81배** 갈렸다. 원인은 산포가 아니라 prefix cache
    # 적중률이고(0.5142 / 0.7776 / 0.8577), 동시성 8·16·32 각각에서 세 판을 적중률로
    # 정렬하면 tok/s 정렬과 같아 **아홉 칸에 역전이 0건**이었다. 즉 옆 필드의 값은
    # **몇 번째 판이냐의 함수**인데 registry에 그 사실을 적을 칸이 없었다.
    #
    # **이 필드는 값을 고치지 않는다.** 왜곡을 없애는 쪽(판마다 본문을 흔든다)은
    # `bench`의 재현성과 맞바꾸는 설계 판단이라 #128이 정하지 않았다. 여기서 하는 것은
    # 그 이슈가 「최소 정직 조치」라 부른 것뿐이다 — 읽는 사람이 판단할 재료를 남긴다.
    #
    # **`MEASUREMENTS`에 넣지 않는다.** 모델의 능력이 아니라 그 측정이 어떤 상태에서
    # 나왔는가이고, 넣으면 이 값이 없는 모델이 전부 "부분 측정"으로 떨어진다.
    #
    # 기본값이 `None`인 것은 `measured_on`과 같은 이유다 — 기존 registry 항목에 이 키가
    # 없으면 로드가 통째로 실패한다. **0.0과 `None`은 다르다**: 0.0은 "캐시를 끄고 쟀다"
    # 라는 실측이고 `None`은 "카운터를 못 읽었다"다.
    prefix_hit_ratio_batch32: float | None = Field(default=None, ge=0.0, le=1.0)

    # 실측 대상 세 항목. `--apply`가 쓰는 키(`bench_report.APPLY_KEYS`) 중 수치만이다.
    # `ClassVar`가 아니면 pydantic이 필드로 읽어 registry 스키마가 바뀐다.
    MEASUREMENTS: ClassVar[tuple[str, ...]] = ("tok_s_single", "tok_s_batch32", "mem_gb_ctx8k")

    @property
    def missing_measurements(self) -> tuple[str, ...]:
        """아직 0인 항목. **세 상태를 가르는 단일 출처다.**

        비어 있으면 전부 측정, 셋 다면 미측정, 그 사이면 부분 측정이다.
        """
        return tuple(name for name in self.MEASUREMENTS if not getattr(self, name))

    @property
    def is_measured(self) -> bool:
        """**하나라도 쟀는가.** "전부 쟀는가"가 아니다 — `is_fully_measured`가 그쪽이다.

        이 값이 `any`인 것은 의도다. `--apply`가 못 잰 값을 0으로 남기므로(bench.py
        모듈 docstring) 부분적으로 채워진 상태가 정상 경로에 실재하고, 그때 이 모델을
        "아무것도 모르는 모델"로 취급하면 이미 확보한 수치까지 버리게 된다.

        **대신 이것만으로 production 게이트를 세우면 절반만 채우고도 열린다.** 그래서
        `doctor`가 `missing_measurements`로 부분 측정을 따로 판정한다 — ATOM에서
        `mem_gb_ctx8k`를 `nvidia-smi --query-gpu`로 얻을 수 없다는 것이 확정됐으므로
        (2026-08-17) 그 상태는 가정이 아니라 곧 실제로 발생한다. **다른 쿼리로도 이
        필드가 채워지지 않는다는 뜻은 아니다** — 이 문장이 지탱하는 것은 "부분 측정이
        실제로 생긴다"까지이고, 그것은 어느 쿼리에서든 참이다.
        ⏵ 2026-10-08 — ATOM은 2026-09-29 사용 종료(#340). vast는 전용 VRAM 장비다
        (`config/serving/vastai.yaml` 머리) — 거기서 이 필드가 채워지는지는 미실측이다.
        """
        return len(self.missing_measurements) < len(self.MEASUREMENTS)

    @property
    def is_fully_measured(self) -> bool:
        return not self.missing_measurements


class RequestSampling(HarnessModel):
    """registry가 모델별로 **보내라고** 선언한 샘플링. 이름은 vLLM 요청 필드 그대로다.

    `schemas.run.ModelSampling`(모델 파일이 **선언한** 것을 받아 적는 자리)과 다르다 — 이쪽은
    우리가 적는 설정이라 범위 밖 값·오타가 곧 결함이고, 로드에서 죽는다(`extra="forbid"`).
    `top_k`의 `-1`은 vLLM의 「끔」이다.
    """

    temperature: float | None = Field(default=None, ge=0.0)
    top_p: float | None = Field(default=None, gt=0.0, le=1.0)
    top_k: int | None = Field(default=None, ge=-1)
    min_p: float | None = Field(default=None, ge=0.0, le=1.0)
    presence_penalty: float | None = Field(default=None, ge=-2.0, le=2.0)


class ModelEntry(HarnessModel):
    """Model Registry 한 항목 (model-serving.md §5)."""

    endpoint: NonEmptyStr
    context_limit: int = Field(gt=0)
    quantization: NonEmptyStr
    license: NonEmptyStr
    # model·tokenizer·quantization 묶음의 해시. RunRecord.model_checksums로 들어간다.
    checksum: Sha256Digest
    # **그 checksum이 어디서 나왔는가.** 예: `hf://NVFP4/Qwen3-Coder-30B-A3B-Instruct-FP4@<rev>`
    # 또는 `file:///data/models/qwen3-coder-30b-nvfp4`.
    #
    # checksum만으로는 사람이 역추적할 수 없다. 후보가 전부 커뮤니티·서드파티 산출물이라
    # (2026-08-16 조사: Auditor NVFP4만 세 벌) **같은 이름의 모델이 리포지터리마다 다르고**,
    # 값이 안 맞을 때 "어느 리포의 어느 리비전이었나"를 답할 실마리가 기록에 없었다.
    #
    # **형식을 패턴으로 강제하지 않는다.** checksum 정본이 로컬 스냅샷 해시라
    # (model-serving.md §5) `file://`도 정당한 값이고, `hf://`로 좁히면 그 경로를 적을
    # 수 없다. `--device` 라벨 집합을 열어 둔 것과 같은 판단이다(ADR 0006). 대신
    # `NonEmptyStr`이라 "적었는데 빈 문자열"은 막는다.
    #
    # 기본값이 `None`인 것은 `Benchmark.measured_on`과 같은 이유다 — 기존 registry 항목에
    # 이 키가 없으면 로드가 통째로 실패하고, `None`은 "출처 미상"이라는 정직한 값이다.
    # `--apply`는 이 필드를 건드리지 않는다(`benchmark:` 블록 밖이고 `APPLY_KEYS`에 없다).
    source: NonEmptyStr | None = None
    # **베이스 가중치의 제작사** — 양자화한 조직이 아니라 가중치가 나온 조직이다
    # (ADR 0010 §4). 그 구별이 필요한 실물이 바로 위 `source`에 있다: `seccoderx-8b`의
    # 출처는 `hf://SecCoderX/…`지만 가중치의 베이스는 Qwen3-8B이므로 계열은 `qwen`이다.
    # (2026-09-24에 지운 옛 키 `qwen3-8b`·`qwen3-coder-30b-a3b`도 NVIDIA·NVFP4 조직이 양자화한
    # 가중치라 `qwen`이었다.)
    #
    # **왜 이 칸이 필요한가** (이슈 #121). evaluation.md §2.1이 B3/B3'의 조건으로
    # "제작사가 다른 모델"을 못박고 model-serving.md와 `orchestrator/nodes/critic.py`가
    # 같은 요구를 문장으로 적는데, **강제하는 코드가 저장소에 하나도 없었다.** 프로필 한
    # 줄로 Critic이 Auditor와 같은 계열이 되어도 그 run은 완주하고, registry에 계열을 적을
    # 칸이 없어 **사후에도 자동으로 못 가렸다.** 아래 `_check_family_split`이 그 판정을 한다.
    #
    # **`Literal`로 닫지 않는다.** 로스터는 계속 바뀌고(ADR 0010 머리) Auditor B안이
    # NVIDIA라 값이 더 는다 — 닫아 두면 registry에 모델을 더할 때마다 이 코드가 따라와야
    # 한다. `quantization`·`license`를 자유 문자열로 둔 것과 같은 판단이다.
    #
    # 기본값이 `None`인 것은 `source`·`Benchmark.measured_on`과 같은 이유다 — 기존
    # registry 항목에 이 키가 없으면 로드가 통째로 실패한다. **다만 `None`은 통과가
    # 아니다**: `_check_family_split`이 "미상"을 "다르다"로 읽지 않는다(CLAUDE.md §0).
    family: NonEmptyStr | None = None
    # **모델별 추가 요청 파라미터.** 이 축의 첫 필드가 이것이다 — 확정 로스터의
    # Critic이 `gpt-oss-20b` + `reasoning_effort: high`이기 때문이다(`PROJECT-CONTEXT.md` §1).
    # 같은 규칙으로 선 넷은 아래 `sampling`부터다.
    #
    # **서버를 띄우는 값과 가른다.** `TRITON_ATTN`·flashinfer off·`--enforce-eager`·
    # `--structured-outputs-config.*`는 **하네스가 안 보낸다** — 그것은 `vllm serve`를 치는
    # 사람의 값이고, 정본 자리는 `config/models.yaml`의 그 항목 「기동 조건」 주석이다
    # (`model-serving.md` §5). 여기 오는 것은 **요청 본문에 실리는 것**뿐이다.
    #
    # 기본값이 `None`인 것은 `family`·`source`와 같은 이유다. 그리고 `None`이면
    # **payload에 키를 안 넣는다** — 안 쓰는 서버가 거절할 수 있고, 성공 경로 payload의
    # 키 집합이 모델마다 달라지면 사후 대조가 깨진다(`context.call_model`의 #82 리뷰 F10과
    # 같은 규칙).
    #
    # **`Literal`로 닫는다** — 위 `family`가 안 닫는 것과 반대다. 저쪽은 로스터가 계속
    # 바뀌는 **열린 집합**이고 이쪽은 서버가 아는 값의 **닫힌 집합**이라,
    # `AdapterSelection`의 판단이 그대로 걸린다 — *"아직 없는 구현체 이름을 YAML에 적으면
    # factory가 KeyError를 내는 대신 설정 로드에서 실패해야 한다."* 자유 문자열이면
    # `High`·`hgih`가 로드를 통과해 **서빙 때만**, 혹은 조용히 무시된 채로 드러난다.
    #
    # ⚠ `Finding.trigger_status`가 일부러 `str`인 것과 헷갈리지 않는다. 그쪽은 **모델이
    # 내는 값**이라 분포를 보기 전에 좁히면 기록이 버려지고, 이쪽은 **우리가 적는 설정**이라
    # 오타가 곧 결함이다. 값이 늘면 여기 한 줄을 늘리고 그 전까지는 크게 실패한다.
    # ⏵ 2026-09-26 — 그렇게 한 값을 늘렸다: `xhigh`. Auditor `qwen3.8-27b`의 템플릿 등급이
    #   `xhigh`(기본)·`medium`·`low`이고 `high`가 없다. **모델마다 받는 값이 다르다** — gpt-oss는
    #   low·medium·high다. vLLM 0.27.1은 이 값을 템플릿 변수로 넘기므로(`chat_completion/
    #   protocol.py`의 `build_chat_params`) 모델이 모르는 등급은 서빙 때 400이다. 이 `Literal`은
    #   합집합이라 그 짝까지는 못 막는다.
    reasoning_effort: Literal["low", "medium", "high", "xhigh"] | None = None
    # **(2026-09-25 — ADR 0012 Phase 6.5 「모델별 요청 설정」) 이 축에 넷이 더 선다.** 규칙은 위
    # `reasoning_effort` 그대로다 — 선언 안 하면 `None`이고 payload에 키가 안 생긴다. 값은 아직
    # 아무 모델에도 없다 — 샘플링은 모델 카드 값으로 바꿀지 결정이 남았고, 나머지(사고 켜기·사고
    # 상한·요청 타임아웃)는 ATOM 실측 뒤에 채운다.
    # ⏵ 2026-10-08 — 「ATOM 실측 뒤」는 낡았다 — ATOM 사용 종료 2026-09-29(#340 ·
    #   docs/portability.md §6). 아래 ⏵대로 값은 실측 전에 계산값으로 들어갔다.
    # ⏵ 2026-09-26 — Auditor(`qwen3.8-27b`)가 처음으로 셋을 선언했다 — `sampling`(모델 카드의
    #   사고 모드 권장값) · `thinking_token_budget` · `request_timeout_s`. 사고를 켜기로 한 @danielcho02
    #   결정에 따라 실측 전에 계산값으로 넣었다(값과 근거는 `config/models.yaml`의 그 항목). 다른
    #   모델은 여전히 비어 있다.
    # ⏵ 2026-09-29 — `request_timeout_s`는 Scout·Critic 실서빙 키 셋(`seccoderx-8b`·`qwen3-14b`·
    #   `gpt-oss-20b`)도 선언한다(#261 — 계산값). `sampling`·`thinking_token_budget`은 그때
    #   Auditor뿐이었고, `chat_template_kwargs`는 어느 모델도 안 쓴다.
    # ⏵ 2026-09-30 — `feyospace-v1-small`(8단계 pov 좌석)도 `reasoning_effort`·`sampling`·
    #   `thinking_token_budget`·`request_timeout_s` 넷을 Auditor 값 그대로 선언한다
    #   (계획값 · 미실측 — `config/models.yaml`의 그 항목).
    # - `sampling` — 프로필 공통 `Sampling`(temperature·top_p)을 **이 모델에서 덮고** top_k 등을
    #   더한다. 좌석마다 모델 카드 권장이 갈리기 때문이다(Qwen 계열 「DO NOT use greedy」).
    # - `chat_template_kwargs` — 템플릿 인자(`enable_thinking` 등). 서버 기본 kwargs로 끄지 않고
    #   요청마다 명시한다.
    # - `thinking_token_budget` — 사고 토큰 상한. vLLM 0.27.1에서 V1 러너 모델(GDN hybrid — 지금
    #   로스터에서는 Auditor `qwen3.8-27b`와 PoV `feyospace-v1-small`, 전에는 `qwen3.6-35b-a3b`)만
    #   받고 dense Qwen3(V2)·gpt-oss는 거절한다 — 그 모델에 적으면 400이다.
    # - `request_timeout_s` — **요청 본문이 아니다.** 어댑터의 벽시계 상한(`_TIMEOUT_S` 대체,
    #   #261). 그래서 `request_params()`에 안 들어간다.
    sampling: RequestSampling | None = None
    chat_template_kwargs: dict[str, JsonValue] | None = None
    thinking_token_budget: int | None = Field(default=None, gt=0)
    request_timeout_s: float | None = Field(default=None, gt=0)
    prompt_template_version: NonEmptyStr
    fallback: str | None = None
    benchmark: Benchmark

    @property
    def is_fake(self) -> bool:
        return self.endpoint.startswith(FAKE_SCHEME)

    def request_params(self) -> dict[str, JsonValue]:
        """이 모델의 요청 payload에 **얹을** 키 전부. 선언한 것만 담는다 — 비면 `{}`."""
        params: dict[str, JsonValue] = (
            self.sampling.model_dump(exclude_none=True) if self.sampling else {}
        )
        if self.chat_template_kwargs is not None:
            params["chat_template_kwargs"] = self.chat_template_kwargs
        if self.thinking_token_budget is not None:
            params["thinking_token_budget"] = self.thinking_token_budget
        return params


class AdapterSelection(HarnessModel):
    """ModelClient를 제외한 3개 인터페이스의 구현 선택.

    ModelClient가 여기 없는 것은 의도다. 역할마다 다른 모델을 쓰고 그중 일부만 실서빙일
    수 있으므로(옛 `single_gpu_12g`처럼), 어댑터 선택은 `ModelEntry.endpoint` 스킴이 역할별로 한다.

    `Literal`로 닫아 둔 것도 의도다. 아직 없는 구현체 이름을 YAML에 적으면 factory가
    KeyError를 내는 대신 설정 로드에서 실패해야 한다.
    """

    # hybrid는 embedding·reranker를 실제로 호출한다. 그 두 역할이 프로필에 없으면
    # 로드 시점에 실패한다(아래 검증기) — factory에서 KeyError로 터지면 어느 프로필이
    # 잘못됐는지가 스택 아래에 묻힌다.
    retriever: Literal["fake", "hybrid"]
    # docker·singularity·podman은 같은 구현(`ContainerToolRunner`)의 런타임 선택이다.
    # Slurm는 Docker 데몬 접근이 없어 singularity를 쓴다(2026-08-17 실측).
    #
    # **ATOM은 docker와 podman을 둘 다 가진다**(2026-08-21 실측: Docker 29.2.1 · podman
    # 4.9.3). 이 주석은 2026-08-17 실측을 근거로 "ATOM은 데몬 접근이 없고 singularity도
    # 없어 podman을 쓴다"고 적고 있었는데(`32e2fb5`), 4일 뒤 docker가 확인되어 반증됐다.
    # 이후 실행 기록도 그쪽이다 — 8/25·26·27 ATOM run이 전부 docker로 돌았고 podman으로
    # 도구를 돌린 run은 `run_20260817_0001` 하나다. 어느 쪽을 선언하는지는 프로필마다
    # 다르고 그 표가 docs/portability.md §3에 있다.
    #
    # CLI `--tool-runner`가 이 Literal에서 choices를 뽑는다.
    tool_runner: Literal["fake", "docker", "singularity", "podman"]
    state_store: Literal["memory", "sqlite"]


class SwapPolicy(HarnessModel):
    """단계 경계에서 어느 역할이 상주하고 어느 역할이 교대하는가 (ADR 0009).

    **`none|sleep` 스칼라로는 이 구분을 못 적는다.** 교대 스키마 사례가 "정수 하나 +
    LRU"와 "역할/그룹별 명시 선언" 두 갈래로 갈리는데, ADR 0009가 뒤쪽을 골랐다 —
    어느 역할이 상주인지가 우리 파이프라인에서는 단계 순서로 정해지지 LRU로 정해지지
    않기 때문이다.

    **교대는 콜드 재기동이다**(2026-09-29 — ADR 0012 Phase 6.5). 단계 경계에서 교대 명령이 앞
    단계 서버를 끄고 다음 단계 모델을 새로 띄운다. vLLM `/sleep`·`/wake_up`을 쓰지 않으므로 옛
    `level`(sleep 단계) 필드는 지웠고, 적어 오면 로드에서 죽는다(`extra="forbid"`). 서버를 띄우는
    값(util·창·인자)과 명령은 프로필이 아니라 장비 파일이 든다(`ServingConfig`).

    **세 값에 기본을 주지 않는다.** 하위 호환을 위해 기본값이 필요한 것은 기존 YAML이
    선언하지 않는 필드(`ProfileEntry.execution`·`ExecutionPolicy.mode`)이고, 이 모델은
    `swap:`을 **적은 프로필에서만** 만들어지므로 그 사유가 걸리지 않는다. 반대로 기본을
    주면 `swap: {}`이 "교대한다"고 선언하면서 아무 역할도 안 부르는 상태가 되고, 교대
    왕복이 아직 미실측인 지금(`docs/research/2026-09-04-swap.md` §1.3·§9) `settle_timeout_s`
    의 기본값은 아무도 재지 않은 수가 config에 굳는 것이다 — ADR 0009가 "값은 실측 후
    채운다. 지금 값을 박지 않는다"고 적은 그 값이다(CLAUDE.md §0).

    **아래 넷은 ADR 0009의 불변식이 아니다.** ADR이 「스키마가 강제」로 이름 붙인 것은
    둘뿐이고(`swap`이 있으면 `mode == per_stage` · `resident ∪ swappable ⊆ roles`),
    아래는 이 스키마가 **더 얹는 것**이다. 셋째 불변식(실서빙 역할은 전부 목록에 있다)은
    registry를 봐야 해서 스키마가 아니라 `load_config`가 강제한다
    (`_check_swap_lists_every_served_role`). 얹는 이유는 바로 위 문단이 *"아무 역할도 안
    부르는 상태"*를 피하려고 기본값을 거절해 놓고 **정작 그 상태를 막지 않았기**
    때문이다 — `resident: []` + `swappable: []`가 통과했다. 사유를 적고 강제하지 않으면
    다음 사람은 그 사유를 강제되는 것으로 읽는다.

    - **`swappable`이 비면 거절한다.** 교대를 선언해 놓고 아무도 내리지 않는 선언이라
      「정책이 선 것처럼 보이면서 아무 일도 안 한다」 — 아래
      `ProfileEntry._swap_names_roles_this_profile_has`가 오타에 대해 막는 것과 같은
      상태다. 그 배선의 정본 표현은 `swap`을 **생략하는** 것이고 그쪽은 지금도 정당하다
      — ADR 0010 §1이 *"교대(`execution.swap`)는 스키마만 열고 기본은 끈다"*로 적은 그
      배선이다.
    - **한 역할이 두 목록에 함께 있으면 거절한다.** "내리지 않는다"와 "내린다"를 동시에
      선언한 것이라 어느 쪽이 이기는지 config만 보고 못 정한다.
    - **한 목록 안의 중복을 거절한다.** 두 목록은 역할 집합이고, 같은 이름이 두 번 적힌
      것의 유일한 효과는 그 사실을 눈에서 가리는 것이다.
    - **검색 역할(`RETRIEVAL_ROLES`)을 거절한다.** 검색 모델을 어느 단계에 띄울지는 5단계
      검색을 가르는 결정(ADR 0012 결정 3) 뒤에야 정해진다. 그 전에 적으면 상주든 교대든
      「선 것처럼 보이는 정책」이다 — 그래서 hybrid 프로필은 아직 교대를 선언하지 않는다.

    **`resident`가 비는 것은 막지 않는다** — 전부 교대시키는 정책은 말이 된다.
    """

    # 단계 경계에서 내리지 않는 역할.
    resident: tuple[NonEmptyStr, ...]
    # 단계 경계에서 내리고 다시 띄우는 역할.
    swappable: tuple[NonEmptyStr, ...]
    # 교대 한 번(내림 + 기동 + 준비 확인)의 마감. 교대 명령에 `SWAP_DEADLINE_S`로 넘어간다
    # (`adapters.model_swap`). 값은 기동 실측으로 정한다 — 위 docstring의 「박지 않는다」.
    settle_timeout_s: float = Field(gt=0)

    @model_validator(mode="after")
    def _lists_are_disjoint_role_sets_that_swap_someone(self) -> "SwapPolicy":
        """위 docstring의 넷. **ADR 0009의 불변식과 별개다.**"""
        for field_name, names in (("resident", self.resident), ("swappable", self.swappable)):
            repeated = sorted({name for name in names if names.count(name) > 1})
            if repeated:
                raise ValueError(
                    f"execution.swap.{field_name}에 같은 역할이 두 번 있다: {repeated}"
                )
        both = sorted(set(self.resident) & set(self.swappable))
        if both:
            raise ValueError(
                f"execution.swap이 같은 역할을 상주와 교대 양쪽에 적었다: {both}. "
                "한쪽만 남긴다"
            )
        if not self.swappable:
            raise ValueError(
                "execution.swap.swappable이 비었다 — 교대한다고 선언하고 아무도 내리지 "
                "않는다. 교대하지 않는 배선은 execution.swap을 생략해 적는다"
            )
        retrieval = sorted({*self.resident, *self.swappable} & set(RETRIEVAL_ROLES))
        if retrieval:
            raise ValueError(
                f"execution.swap이 검색 역할을 부른다: {retrieval}. hybrid 교대는 ADR 0012 "
                "결정 3(5단계 검색 분리) 뒤다 — 그때까지 검색 역할은 교대에 넣지 않는다"
            )
        return self


class ExecutionPolicy(HarnessModel):
    """실행 단위와 교대 정책 (ADR 0009).

    **기본값이 지금 동작이다.** `per_candidate`는 후보 하나가 3~6단계를 관통하는 현행
    러너이고, 그래서 이 필드를 선언하지 않은 프로필은 run이 바뀌지 않는다. ADR 0009가
    이 결정을 "되돌리기 싼" 것으로 부른 근거가 그것이고, 러너를 단계 단위로 재작성하는
    쪽은 **별도 PR**이었고 그 PR이 들어왔다(ADR 0009 결과 2) — `per_stage`를 적으면
    `runner._triage_by_stage`가 돈다. 여기서 정하는 것은 **어느 루프인가**와 교대 선언이다.
    교대(`swap`)를 적으면 러너가 단계 경계에서 교대 콜러블(`Dependencies.swap_to`)을 부르고 run
    끝에 내린다 — 교대 명령과 기동 조건은 프로필이 아니라 장비 파일이 든다(`ServingConfig`, ADR 0009
    「2026-09-29 제안」 절).
    """

    mode: Literal["per_candidate", "per_stage"] = "per_candidate"
    swap: SwapPolicy | None = None

    @model_validator(mode="after")
    def _swap_requires_stage_unit(self) -> "ExecutionPolicy":
        """후보 단위 + 교대 조합을 스키마가 막는다 (ADR 0009 불변식 1).

        후보마다 교대하면 교대 횟수가 후보 수만큼 곱해진다 — 같은 파이프라인에서
        **512회[계산]**가 나오는 조합이고, 단계 단위로 재배열하면 **3~5회[계산]**다.
        왕복 예산이 아직 미실측이라(§1.3) 그 조합이 도는 run은 지연의 대부분이 어디서
        왔는지 사후에 못 가른다.

        **두 수 다 실측이 아니다** (CLAUDE.md §0). 512는 `config/budgets.yaml`의
        `candidate_max: 256`에 후보당 왕복 2회를 곱한 것이고
        (`docs/research/2026-09-04-swap.md` §6.2 — 그 표의 열 이름이 "비용 [계산]"이다),
        3~5는 같은 표가 단계 단위 재배열에 적은 값으로 ADR 0010 §1이 "교대 횟수
        3~5회(계산값)"로 옮겨 적는다. **아무도 교대를 512번 세어 보지 않았다.**

        ⚠ **512는 단일 `scout` 기준이다.** Scout 풀은 대상 하나를 멤버 셋이 각각 보므로
        (ADR 0010 §3) 같은 `candidate_max`에서 후보가 셋 배가 되고 이 수는 **1,536**이
        된다[계산]. 어느 쪽도 실측이 아니라는 위 문장은 그대로다 — 여기서 고치는 것은
        **그 계산이 어느 구성의 것인가**이지 값의 성격이 아니다.
        """
        if self.swap is not None and self.mode != "per_stage":
            raise ValueError(
                f"execution.swap은 mode: per_stage에서만 선언할 수 있다 (지금 {self.mode!r})"
            )
        return self


class ProfileEntry(HarnessModel):
    """실행 프로필 하나 (model-serving.md §5)."""

    description: NonEmptyStr
    production: bool = False
    # 역할 이름 → models.yaml의 model key
    roles: dict[str, NonEmptyStr]
    adapters: AdapterSelection
    # budgets.yaml의 budgets 키
    budget: NonEmptyStr
    # 실행 단위와 교대 정책 (ADR 0009). **기본 `None`이 지금 동작이다** — 선언하지 않은
    # 프로필은 후보 단위로 돌고 교대하지 않는다. 지금은 `harness_pool`·`_hybrid`가 단계 단위를
    # 선언하고 그중 `harness_pool`만 교대한다(2026-09-29).
    execution: ExecutionPolicy | None = None

    @model_validator(mode="after")
    def _has_required_roles(self) -> "ProfileEntry":
        missing = [role for role in REQUIRED_ROLES if role not in self.roles]
        if missing:
            raise ValueError(f"프로필에 필수 역할이 없다: {missing}")
        return self

    @model_validator(mode="after")
    def _declares_one_scout_arrangement(self) -> "ProfileEntry":
        """4단계 Scout 자리는 **단일 `scout` 하나 아니면 풀 셋**이다 (ADR 0010 §3).

        ADR이 적는 것은 *"프로필은 `roles:`에 그 셋을 적고, 기존 `scout` 한 역할
        프로필도 그대로 유효하다(둘 중 하나가 있어야 한다)"*까지다. 아래 셋을 각각
        막는 근거는 그 한 문장이 아니다.

        - **둘 다 없다** — 4단계에 설 모델이 없다. `REQUIRED_ROLES`에서 `scout`을 뺀
          대가가 이것이라 여기가 아니면 아무 데서도 안 걸리고, 4단계가 후보 0건을 내면
          그것이 "취약점이 없었다"로 읽힌다.
        - **풀이 불완전하다** — 한 CWE 계열을 통째로 안 보는 프로필이 된다. 그 사실이
          후보 수 감소로만 나타나므로 run 결과만 봐서는 안 보인다.
        - **둘 다 있다** — ADR이 명시적으로 금지하지는 않는다. 막는 근거는 배선이다:
          4단계를 **어느 클라이언트가 도는지 config만 보고 정할 수 없다.** 위 둘과 달리
          여기서 갈리는 것은 "덜 본다"가 아니라 **네 개의 Scout 선언 중 무엇이 실제로
          돌았는지 사후에 못 되짚는 것**이다 — 그 선택이 코드 어딘가의 우선순위로
          내려가면 프로필은 두 배선을 같은 모양으로 적으면서 다른 run을 낸다.

        ⚠ **「안 돈 역할의 checksum이 기록에 남는다」를 근거로 적지 않는다.** 이
        저장소는 그 상태를 이미 정당한 것으로 다룬다 — `retriever: fake` 프로필의
        embedding·reranker가 정확히 그것이고, 아래 `served_models()`가 *"선언 자체는
        지우지 않는다 … 그래서 기록(`model_checksums`)은 그대로 두고 판정 대상만
        좁힌다"*로 그 판단을 적어 놓았다. 그 선례가 말하는 것은 "선언을 거절한다"가
        아니라 **"선언을 남기고 판정을 좁힌다"**라, 여기를 지지하지 않는다.
        """
        single = SCOUT_ROLE in self.roles
        declared = [role for role in SCOUT_POOL_ROLES if role in self.roles]
        if single and declared:
            raise ValueError(
                f"Scout 자리가 둘이다: 단일 {SCOUT_ROLE!r}과 풀 {declared}을 함께 "
                "선언했다. 한쪽만 남긴다"
            )
        if not single and len(declared) != len(SCOUT_POOL_ROLES):
            missing = [role for role in SCOUT_POOL_ROLES if role not in self.roles]
            raise ValueError(
                f"Scout 자리가 비었거나 풀이 불완전하다: 빠짐={missing}. "
                f"단일 {SCOUT_ROLE!r} 하나 아니면 풀 {list(SCOUT_POOL_ROLES)} 전부다"
            )
        return self

    @model_validator(mode="after")
    def _hybrid_needs_retrieval_roles(self) -> "ProfileEntry":
        """`retriever: hybrid`는 embedding·reranker 역할을 요구한다.

        REQUIRED_ROLES에 넣지 않은 것은 의도다 — `retriever: fake`인 프로필에는 그
        두 역할이 없어도 된다. 요구 조건이 어댑터 선택에 달려 있으므로 여기서 본다.
        """
        if self.adapters.retriever != "hybrid":
            return self
        missing = [role for role in RETRIEVAL_ROLES if role not in self.roles]
        if missing:
            raise ValueError(f"retriever: hybrid 프로필에 검색 역할이 없다: {missing}")
        return self

    @model_validator(mode="after")
    def _swap_names_roles_this_profile_has(self) -> "ProfileEntry":
        """`swap.resident ∪ swap.swappable ⊆ roles` (ADR 0009 불변식 2).

        없는 역할을 부르는 교대 선언은 **아무 일도 안 하면서 정책이 선 것처럼 보인다.**
        오타 하나면 그 모델만 교대에서 빠지고, 그 run은 메모리가 왜 안 줄었는지 설명할
        수 없는 상태로 끝난다. `_check_model_refs`가 model key의 참조 무결성을 로드
        시점에 보는 것과 같은 자리다 — 다만 이쪽은 프로필 안에서 닫히므로 여기서 본다.
        """
        if self.execution is None or self.execution.swap is None:
            return self
        named = {*self.execution.swap.resident, *self.execution.swap.swappable}
        missing = sorted(named - set(self.roles))
        if missing:
            raise ValueError(f"execution.swap이 이 프로필의 roles에 없는 역할을 부른다: {missing}")
        return self


class CapsuleBudgets(HarnessModel):
    """캡슐 레벨별 토큰 상한. 키는 `CapsuleLevel` 값과 같다 (knowledge-base.md §4)."""

    light: int = Field(gt=0)
    expanded: int = Field(gt=0)
    focused: int = Field(gt=0)


# Auditor `SOURCE`를 결정 1-b(「슬라이스가 걸친 함수의 원본 전부 · 15,000자」) 밖으로 넓히는 값들
# (#349). 필드 기본은 0이고 0이면 `flatten()`에 키도 안 생긴다(옛 manifest 모양). 하나라도 켜면
# Auditor 프롬프트 meta의 `source_options`가 그 키를 들어야 run이 선다(`cli`).
# ⏵ 2026-10-03 — `config/budgets.yaml`이 entry 줄이기를 뺀 넷을 켠다(20,000토큰 상한 · 정의 ·
# 피호출 본문 1단 · 호출자 창 10줄 · 사용자 결정). 서빙 판 v7(⏵ 2026-10-04 — v6 + 그 서술)이 그 넷을
# 서술하고 선언한다 — ⏵ 같은 날 **서빙 판은 v8**(v7 + 도구 루프)이었고 그 서술을 v7에서
# 바이트 그대로 물려받아 같은 `source_options`를 선언한다(⏵ 2026-10-06부터 v9).
# ⏵ 2026-10-04 — 다섯째 `auditor_callee_wrappers`(래퍼·별칭 너머, 이슈 #451)도 그 파일이 켜고
# v7·v8이 서술한다.
# ⏵ 같은 날 — 여섯째·일곱째
# `auditor_indirect_callees`(간접 피호출, C6)·`auditor_write_sites`(쓰기 지점 창, C5(a))도 그 파일이
# 켜고 v7·v8이 서술한다(index-v3 · #304).
# ⏵ 같은 날 — 여덟째 `auditor_guards`(sink 앞 구문상 가드 목록, #349)는 v9가 서술·선언한다
# (2026-10-06 — v7·v8은 안 한다). 필드 기본 0은 그 파일이 키를 안 적을 때의 값이다.
# ⏵ 2026-10-07 — **서빙 판은 v10이 됐다.** 위 「서술을 v7에서 바이트 그대로 물려받았다」는
# v8·v9의 서술이다. v10은 v7을 전면 재작성한 판이라 물려받지 않고 켜진 여덟을 다시 써서 meta
# `source_options`로 여덟을 선언한다(철자는 서빙 판 핀 `tests/test_config.py`의
# `test_the_canonical_auditor_prompt_states_the_runner_source_contract`가 문다).
# v10으로 돈 run은 0건이다(⏵ 2026-10-08 — 1건이 됐다, `docs/experiments/harness/2026-10-08-first-
# real-serving-run-arvo1065-vastai-remote.md` · `checksum` 0이라 비교표 밖).
# ⏵ 2026-10-09 — 서빙 판은 v11이다(v10 + `cwe` 서술 네 줄 — 옵션 서술은 v10과 바이트 동일, 이슈
# #525).
# ⏵ 2026-10-10 — 서빙 판은 v13이다(v11 + FACTS에서 `source` 사본을 뺀다 — 옵션 서술은 v11과 바이트
# 동일, 이슈 #67).
# ⏵ 2026-10-11 — 서빙 판은 v14다(v12 + FACTS에서 `source` 사본을 뺀다 — 옵션 서술은 v12와 바이트
# 동일, 이슈 #67).
# ⏵ 2026-10-13 — 서빙 판은 v15다(v14 + 계약 문장 여섯 — 옵션 서술은 v14와 같다).
# ⏵ 2026-10-14 — 아홉째 `auditor_input_context`(슬라이서가 CPG 호출 경로로 이은 입력 디코더 본문,
# S20 Phase 13)는 v16이 서술·선언한다(v15까지는 안 한다 — v15를 고르고 켜면 `cli`의 run·doctor가
# 막는다).
# ⏵ 2026-10-14 — 열째 `auditor_slice_state`(슬라이스의 정적 상태를 FACTS `slice_state` 키로 보인다,
# 이슈 #438 (b))는 v17이 서술·선언한다(v16까지는 안 한다). `SOURCE`가 아니라 FACTS를 넓히는
# 옵션이지만 같은 자리(meta `source_options`)로 선언한다 - 켜면 모델 입력이 달라져 프롬프트가
# 서술해야 하는 점이 같다. **기본은 끈다** - 켜는 것은 측정 결정이다(켠 run ↔ 끈 run 짝 비교).
AUDITOR_SOURCE_OPTIONS = (
    "auditor_callee_hops",
    "auditor_definitions",
    "auditor_source_tokens",
    "auditor_entry_trim",
    "auditor_caller_lines",
    "auditor_callee_wrappers",
    "auditor_indirect_callees",
    "auditor_write_sites",
    "auditor_guards",
    "auditor_input_context",
    "auditor_slice_state",
)


class Thresholds(HarnessModel):
    """정책 임계값. 노드가 직접 비교하지 않고 RoutingPolicy가 읽는다."""

    scout_threshold: float = Field(ge=0.0, le=1.0)
    capsule_expansion_max: int = Field(ge=0)
    # need_evidence 루프 상한 — Scout가 NE 신호를 낸 뒤 같은 세션에서 되물을 수 있는
    # 최대 홉 수 (ADR 0012 결정 6 — 이 값이 위 `capsule_expansion_max`를 대체한다.
    # 이슈 #92). 0이면 루프가 꺼진다. **명세값이고 이 하네스에서 안 쟀다** —
    # `cli.PROVISIONAL_THRESHOLDS`에 있다. 홉을 세고 이 값과 비교하는 것은
    # `runner._run_ne_hops`다.
    max_hops: int = Field(ge=0)
    requeue_max: int = Field(ge=0)
    critic_top_n: int = Field(gt=0)
    # 8단계에서 `validated` **밖의** Finding 중 PoV를 더 시도할 수 **상한**. `0`이면 종전
    # 동작이다 — PoV는 8단계에 `VALIDATED`로 들어온 Finding만 돈다. 이 값을 올리면 그 밖에서도
    # 상위 몇 건을 더 시도한다(이슈 #386 ①의 후속).
    # **풀은 「Critic이 밀어 내지 않은 Finding」이다** — Critic이 반박으로 판정한 것과 판정 호출이
    # 실패해 버려진 것은 뺀다(반박된 Finding의 PoC가 최종 제출이 되지 않게 — `runner._pov_targets`).
    # ⏵ 2026-10-11 — 보류(`need_evidence`)는 들고 정렬·지정에서 뒤다(이슈 #604).
    # 드는 것: Critic을 안 거친 Finding(`critic_top_n`에 잘린 것 ·
    # 6단계가 E0로 `QUEUED`한 것)과 재현 명세가 없어 `hypothesized`에 멈춘 것(F-9) 등이다.
    # ⚠ **이 필터는 이 기능이 처음 겨냥한 run의 풀을 바꾸지 않는다** — 그 run(`run_20261003_0003`)의
    # Finding 셋은 Critic을 안 거쳤고 전부 Auditor가 스스로 낸 부정 결론(`no_signal` ·
    # `confidence` 0.10·0.10·0.05)이다. 풀은 그것을 거르지 않고(결정 대기), 그런 Finding에서
    # PoV가 낼 PoC가 있는지는 미측정이다(출처: `docs/experiments/harness/2026-10-04-cybergym-
    # operational-run-vastai-remote.md` §4).
    # **켤 때의 값은 미실측이다** — ⏵ 2026-10-06 후보 **하나**는 쟀다(PoV 좌석 프로브의
    # find_0001 - 4홉 · 출력 92,543 · 입력 242,717토큰, 홉 상한에 끊겼다). 못 잰 것은 후보가
    # **스스로 서기까지의** 홉·토큰이고 그 측정의 입력이 이 배선 뒤의 실모델 run 1회다.
    # 자르는 순서는 Critic 판정 계단(uphold 0 < 미경유 1 < 보류 2 < 반박 3) → `confidence` 내림차순
    # → 동점은 `finding_id`다(⏵ 2026-10-12 — 계단이 맨 앞에 붙어 `critic_top_n`의 순서
    # (`confidence` → `finding_id`)와 더는 같지 않다, PR #608) — 정렬이 흔들리면 재현 실행이
    # 갈라진다.
    pov_attempt_top_n: int = Field(ge=0)
    # 8단계 PoV 툴 루프(`runner._run_pov_hops`)의 **전용** 홉 상한 (이슈 #511) — 모델 호출은
    # finding당 최대 `1 + 이 값`회다(hop 0..값). 0이면 hop0(gen) 뒤 곧장 MAX_HOP이라 submit이 없다.
    # `max_hops`에서 떼어냈다 — Auditor가 `auditor_tool_hops`(#419)로 갈라진 것과 같은 이유다:
    # PoV 홉을 `max_hops`로 늘리면 Scout 되묻기와 `cli estimate`의 `scout_calls`가 같이 움직인다.
    # **필드 기본이 없다** — 값은 `config/budgets.yaml`만 든다. `auditor_tool_hops`의 기본 0은
    # 「꺼짐 = 종전 경로」지만 여기 0은 「submit에 못 닿는다」라, 기본 0이면 키가 빠진 설정이 PoV
    # 산출을 조용히 끈다. **값은 미결정이다**(`cli.PROVISIONAL_THRESHOLDS`) — 근거는 그 파일 주석.
    pov_tool_hops: int = Field(ge=0)
    # 한 run이 4단계에 올릴 target 수 상한 (W2). 초과분은 폐기하지 않고 사유와 함께
    # 기록한다 — 근거와 그 한계는 `config/budgets.yaml`에 적었다.
    candidate_max: int = Field(gt=0)
    # agent-contracts.md §1의 token_budget.retries. 이 값을 올리면 시도 수 `attempts`가 늘어
    # "스키마 파싱 실패율"(evaluation.md §5.1)의 분자·분모가 함께 커지므로 조건 간 비교에서
    # 고정되어야 한다 — 어댑터 기본값으로 두지 않는다(#83).
    schema_retries: int = Field(ge=0)
    # 요청 하나의 출력 토큰 상한 (agent-contracts.md §1의 token_budget.output_max).
    # `openai_compat`이 payload의 `"max_tokens"`로 싣는다. 없으면 서버가 `--max-model-len`
    # 까지 생성하고, 2026-08-27 ATOM에서 30B가 실제로 그렇게 폭주해 클라이언트 타임아웃이
    # 났다. `schema_retries`와 같은 이유로 여기 있다 — 조건 간 비교에서 고정되어야 하고
    # `flatten()`이 `RunRecord.thresholds`로 실어야 한다. 어댑터 기본값으로 두지 않는다.
    output_max: int = Field(gt=0)
    # 위 전역값을 **역할 단위로 덮는 자리** (이슈 #79의 선택지 **(c)**). 선언한 역할만 바뀌고
    # 나머지는 `output_max`를 그대로 쓴다.
    #
    # **`ModelEntry.reasoning_effort`가 낸 전례 그대로다** — optional이고, 선언 안 하면
    # 기존 동작이 한 톨도 안 바뀐다. 기본값이 `{}`인 것이 그 뜻이다.
    #
    # **왜 registry가 아니라 여기인가.** `reasoning_effort`는 모델 성질이라 registry가
    # 맞지만 이 값을 정하는 것은 **캡슐 레벨**이다 — #79의 (c)가 지목한 것이 "EXPANDED
    # 역할에 더 준다"이고, 레벨은 역할의 성질이다(`bench.ROLE_SPEC`). 같은 모델이 두
    # 역할에 앉으면 필요한 상한이 갈린다.
    #
    # **키를 `PROMPTED_ROLES`로 닫는 것은 `reasoning_effort`가 `Literal`로 닫힌 사유와
    # 같다.** 자유 키면 `critick: 8192`가 로드를 통과하고 **조용히 무시된 채** 전역값으로
    # 돈다 — 그 run은 "역할별 상한을 줬다"고 기록되지만 실제로는 안 준 것이다. 오타가 곧
    # 결함인 **우리가 적는 설정**이므로 크게 실패한다. 프롬프트를 안 받는 역할
    # (embedding·reranker)이 빠지는 것도 같은 자리다 — 그쪽은 `_model_client`를 타지
    # 않아 이 값을 실을 경로 자체가 없다(`prompt_versions()`의 "거짓 정보" 판단).
    output_max_by_role: dict[str, int] = Field(default_factory=dict)
    capsule_budget_tokens: CapsuleBudgets
    # Auditor `SOURCE`에 피호출 함수 본문을 붙이는 단계 수(#349 (a)). 0이면 붙이지 않는다 —
    # ADR 0012 결정 1-b(「슬라이스가 걸친 함수의 원본 전부」) 그대로다. 1이면 실린 함수의 본문 안
    # 호출이 부르는 함수의 본문을 뒤에 붙인다(`runner._auditor_source`). 결정 1-b를 넓히는 값이라
    # 필드 기본은 끈다(⏵ 2026-10-03 `budgets.yaml`이 1로 켠다 — 위 `AUDITOR_SOURCE_OPTIONS` 주석).
    # 2 이상은 아직 없다. ⏵ 2026-10-04 — 붙인 피호출 함수가 짧은 래퍼면 그 너머까지 가는 것은
    # 이 값이 아니라 아래 `auditor_callee_wrappers`다(C4). 이 값만 켜면 1단에서 멈춘다(래퍼 너머
    # 이전 모양).
    # ⚠ (⏵ 2026-10-03 — 아래는 서빙 판이 옵션을 서술하지 않던 때의 경고다. ⏵ 2026-10-04 서빙 판 v7이
    # 넷을 서술했고 ⏵ 같은 날 서빙 판 v8도, ⏵ 2026-10-06부터의 v9도 같은 서술을
    # 물려받았다 — `prompts/auditor/v7/prefix.md` · `v8/` · `v9/`. v6는 1-b 모양만 서술한다.
    # ⏵ 2026-10-07 서빙 판이 된 v10은 물려받지 않고 전면 재작성에서 다시 썼다 — `v10/`.)
    # 켜면 **서빙 판** Auditor 프롬프트의 입력 서술(⏵ 2026-10-03 `prompts/auditor/v6/prefix.md`
    # — 그전에는 v4 · 판은 `config/models.yaml`이 고른다 — 슬라이스가 걸친
    # 함수를 `(file, start line)` 순으로)과 어긋난다 — 피호출 함수는 그 뒤에 붙는다. 켜는 run은
    # 프롬프트 판을 같이 올려야 한다 — 그 판 meta의 `source_options`가 이 키를 들지 않으면
    # `cli`의 run·doctor가 막는다(`AUDITOR_SOURCE_OPTIONS`). 아래 넷도 같다.
    auditor_callee_hops: int = Field(default=0, ge=0, le=1)
    # Auditor `SOURCE`에 실린 원문이 쓰는 매크로·타입·파일 변수·enum 상수의 정의 줄을 붙이는가
    # (#349 (f) — enum 상수는 index-v4부터).
    # 0이면 안 붙인다(결정 1-b 그대로). 피호출 함수보다 먼저 붙는다 — 위 ⚠와 같은 프롬프트 판 문제.
    auditor_definitions: int = Field(default=0, ge=0, le=1)
    # Auditor `SOURCE` 상한을 토큰으로(#349 (c)). 0이면 지금처럼 글자 상한
    # (`runner._AUDITOR_SOURCE_CHARS` 15,000)이다. 양수면 Auditor 모델 토크나이저(없으면 글자
    # 근사)로 센 토큰이 이 값을 넘지 않게 함수·정의·피호출 본문·호출자 창을 넣는다. entry 함수는
    # 여전히 상한 밖이다
    # (아래 `auditor_entry_trim`이 0일 때).
    # **값은 아직 안 정했다** — 저장소 전체 실측에서 효과가 더 늘지 않는 가장 작은 값을 고른다
    # (#349). ⏵ 2026-10-03 — `config/budgets.yaml`이 20000으로 정했다. 근거는 저장소 전체 실측이
    # 아니라 로컬 소표본이다(2026-10-01 s500 실패 표본 9개 · run ID 없음 — 그 파일의 주석).
    # 2026-10-01 s500 handoff Auditor `SOURCE` 81,748건을 Qwen3.8-27B 토크나이저로 센 글자/
    # 토큰 비는 중앙값 3.04(p10 2.53 · p90 3.70) — 15,000자 ≈ 4,900토큰은 그 중앙값으로 나눈
    # **계산값**이다. 측정은 로컬·run ID 없음(원자료 저장소 밖 `measure_results/s500/`).
    auditor_source_tokens: int = Field(default=0, ge=0)
    # entry 함수가 위 상한을 넘으면 줄이는가(#349). 0이면 지금처럼 entry 함수는 상한 밖이다(통째로
    # 실림 — s500 handoff `SOURCE` 하나의 최대가 155,280토큰, 위와 같은 로컬 측정). 1이면 함수
    # 머리·닫는 줄·entry 줄과 슬라이스 줄 앞뒤만 남기고 뺀 구간을 표시한다(`runner._trim_entry`).
    # 위 ⚠와 같은 프롬프트 판 문제.
    auditor_entry_trim: int = Field(default=0, ge=0, le=1)
    # Auditor `SOURCE`에 entry 함수를 부르는 호출 지점 앞뒤 몇 줄을 붙이는가(#349 (b)). 0이면 안
    # 붙인다(결정 1-b 그대로). 호출자 쪽 검사(인자 길이·NULL 확인)를 겨냥한다. 창은 호출자 함수
    # 범위 안으로 자르고 정의·피호출 함수 다음(쓰기 지점 창 앞)에 붙는다(`runner._caller_window`).
    # **값은 안 정했다** — 저장소 전체 실측에서 고른다. ⏵ 2026-10-03 — `config/budgets.yaml`이
    # 10으로 정했다. 근거는 저장소 전체 실측이 아니라 로컬 소표본이다(harness_final_v1 vuln
    # 41개를 GT 함수 중심으로 조립한 5줄 대비 비교 · run ID 없음 — 그 파일의 주석). 위 ⚠와 같은
    # 프롬프트 판 문제.
    auditor_caller_lines: int = Field(default=0, ge=0)
    # Auditor `SOURCE`가 래퍼·이름만 바꾸는 매크로 너머까지 따라가는가(C4 — 이슈 #451). 0이면
    # 안 따라간다 — 피호출 본문은 1단에서 멈추고 정의는 실린 이름의 것만 붙는다(이 값이 생기기
    # 전 모양).
    # 1이면 `auditor_callee_hops`가 붙인 피호출 함수가 짧은 래퍼일 때 그 너머 함수를 2단까지 더
    # 붙이고(`runner._AUDITOR_WRAPPER_*`), `auditor_definitions`가 붙인 매크로가 이름만 바꿔 넘기면
    # 그 이름의 정의(2번까지)와 함수 본문을 더 찾는다(`runner._AUDITOR_ALIAS_ROUNDS`). 두 단계에
    # 얹히는 값이라 둘 다 꺼져 있으면 효과가 없다. 필드 기본은 끈다 — `budgets.yaml`이 1로 켠다
    # (2026-10-04 · 켠 run은 `flatten()`에 이 키가 보인다). **세 상수는 정한 값이지 잰 값이 아니다**
    # (팀 검수 83표본의 래퍼 예). 위 ⚠와 같은 프롬프트 판 문제 — v7이 서술한다.
    auditor_callee_wrappers: int = Field(default=0, ge=0, le=1)
    # Auditor `SOURCE`가 필드 경유 호출(`sys->alloc(…)`)이 갈 수 있는 함수 본문을 붙이는가(C6 —
    # #304). 1이면 피호출 단계(`auditor_callee_hops`)에서 실린 함수의 필드 경유 호출마다 index-v3
    # `fn_bindings`로 푼 후보(`indirect_calls_of`)를 붙인다 — 아직 안 실린 후보가
    # `runner._AUDITOR_INDIRECT_MAX`개를 넘는 필드는 어느 것인지 몰라 그 필드만 건너뛴다(PR #468
    # 리뷰 MEDIUM-2 — 상한은 필드마다). 비제품 경로 강등은 피호출 조회와 같다
    # (`auditor_definitions`를 켠 run). 피호출 단계에 얹히는 값이라 그것이 꺼져 있으면 효과가 없다.
    # v3 표가 없는 인덱스(이미지 재빌드 전)면 아무것도 안 붙는다. 필드 기본은 끈다 —
    # `budgets.yaml`이 1로 켠다(2026-10-04). 위 ⚠와 같은 프롬프트 판 문제 — v7이 서술한다.
    auditor_indirect_callees: int = Field(default=0, ge=0, le=1)
    # Auditor `SOURCE`에 붙이는 **쓰기 지점 창** 수의 상한(C5(a) — #304). 양수 N이면 슬라이스 줄에
    # 나온 이름 중 실린 함수의 지역·파라미터가 아닌 것(파일 변수 등)과 필드 이름마다 index-v3로
    # 쓰는 지점(`writes_of`·`field_writes_of`)을 찾아, 이미 실린 범위 밖의 지점 앞뒤
    # `runner._AUDITOR_WRITE_LINES`줄을 그 함수 범위 안으로 잘라 N곳까지 붙인다(호출자 창과 같은
    # 창 · 호출자 창 뒤). 쓰는 함수의 같은 이름 지역에 쓰는 지점은 뺀다. 순서는 이름·필드가 슬라이스
    # 줄에 처음 나온 순서이고 함수가 다른 지점이 먼저다(`runner._write_sites`). 버퍼·길이 필드가
    # 어디서 정해지는지(`->len = …`·`buf = malloc(n)`)를 겨냥한다. 필드 기본은
    # 0(끔) — `budgets.yaml`이 켠다. v3 표가 없으면 아무것도 안 붙는다. 위 ⚠와 같은 프롬프트 판
    # 문제 — v7이 서술한다.
    auditor_write_sites: int = Field(default=0, ge=0)
    # Auditor `SOURCE`에 sink 앞의 구문상 가드 목록을 붙이는가. 0이면 안 붙인다(결정 1-b 그대로).
    # 1이면 sink를 아는 슬라이스(Case B)의 entry 함수 바로 뒤에 `GUARDS (syntactic — may not
    # dominate)` 블록을 붙인다(`source_guards`) — sink 호출의 인자 이름을 언급하는 if·while·for·
    # 삼항·assert 계열 조건을 `파일:줄 | 조건`으로(대입 한 걸음으로 이어진 이름 포함). 지배를
    # 주장하지 않고, 하나도 없으면 「없음」이 아니라 맞춰 본 이름과 못 보는 것을 적은 한 줄이다.
    # Case A·품는 함수를 모르는 창 앵커·줄이 어긋날 수 있는 파일(폼피드 등, #259)은 안 붙이고
    # 사유를 trace `guards_skipped`가 든다. 행 상한·예산 계산은 `source_guards`·
    # `runner._auditor_source`가 든다. 인덱스 표를 안 읽는다 — 원문만 본다. 필드 기본은 0이고
    # `budgets.yaml`이 켠다(⏵ 2026-10-06 — **켠 효과는 미실측**). Auditor v9가 이 옵션을
    # 서술·선언한다. v7·v8은 안 하므로 켜고 그 둘을 고르면 `cli` run·doctor가 막는다.
    # ⏵ 2026-10-07부터 서빙 판 v10도 서술·선언한다(meta `source_options` 여덟 중 하나).
    # ⏵ 2026-10-09 — 서빙 판 v11(이슈 #525)도 같은 서술로 선언한다.
    # ⏵ 2026-10-10 — 서빙 판 v13(이슈 #67)도 같은 서술로 선언한다.
    # ⏵ 2026-10-11 — 서빙 판 v14(이슈 #67)도 같은 서술로 선언한다.
    # ⚠ PoV(`pov._WIDE_INPUT_VERSIONS` 판)도 같은 SOURCE를 받아 이 블록이 PoV 프롬프트에도 든다.
    auditor_guards: int = Field(default=0, ge=0, le=1)
    # Auditor `SOURCE`의 쓰기 지점 창 다음 마지막 블록(#650 표지 줄 앞)으로 슬라이서의
    # `analysis.input_context`가 고른 입력 디코더 함수 본문을 붙이는가(S20 Phase 13 · council
    # 2026-10-14). 0이면 안 붙인다 -
    # `SOURCE`·이벤트가 이 옵션이 생기기 전과 바이트 동일이다. 1이면 `SliceRef.input_context`가
    # `status == "selected"`이고 `basis == "cpg"`일 때 그 `functions[]`의 본문을 같은
    # 상한(`auditor_source_tokens`)·`seen` 기계로 붙인다(`orchestrator/source_input_context`). 새
    # 수치 상한은 없다. `syntax` 근거·`dispatches`· `carried_decoders`는 싣지 않고 이벤트에 수만
    # 남긴다. 키가 없는 슬라이스(`--index` 없이 돈 슬라이서)는 아무것도 안 붙는다. 필드 기본은 끈다
    # - `budgets.yaml`이 켠다. **켠 효과는 미실측이다.** ⚠ PoV(`pov._WIDE_INPUT_VERSIONS` 판)·
    # Critic도 같은 SOURCE를 받아 이 블록이 그 프롬프트에도 든다.
    auditor_input_context: int = Field(default=0, ge=0, le=1)
    # Auditor FACTS에 슬라이스의 정적 상태 `slice_state`(`analysis_kind` · `degraded` ·
    # `truncated` + 사유 · 경계 사유 · 상한 둘)를 싣는가(이슈 #438 (b) · S20 Phase 14 ·
    # `orchestrator/slice_state`). 0이면 안 싣는다 - Auditor 입력(SOURCE·FACTS)과
    # `auditor_source` 이벤트가 이 옵션이 생기기 전과 바이트 동일이다. 1이면 슬라이스가 있는
    # 후보의 hop0 FACTS에 키가 붙고(Auditor 자리만 - 다른 역할의 FACTS는 그대로) 같은 값이
    # `auditor_source` 이벤트의 `slice_state_*`로 남는다. 슬라이서가 분석 사실을 안 적은
    # 슬라이스는 `false`가 아니라 `null`이다. 서술·선언은 Auditor **v17**이 든다(v16 이하를
    # 고르고 켜면 `cli`의 run·doctor가 막는다). **기본은 끈다 - 켜는 것은 측정 결정이다**:
    # 이슈 #438의 닫히는 조건이 켠 run ↔ 끈 run 짝 비교를 요구하고, 입력이 길어지는 정도와 판정
    # 분포가 움직일 방향은 안 쟀다.
    auditor_slice_state: int = Field(default=0, ge=0, le=1)
    # Scout 슬라이스 렌더가 그 자리 창(`context_limit` − `output_max` − 여유)을 넘으면 entry에
    # 가까운 줄부터 남기고 빼는가(#353 (b)). 0이면 지금처럼 통째로 보내고, 넘으면 서빙이 크기
    # 거절해 그 후보만 빠진다(`runner._is_size_rejection`). 1이면 `scout._fit_slice`가 줄인다 —
    # trace `scout_slice_trimmed`가 남는다. 프롬프트 판은 안 바꾼다(Scout v3 prefix가 SOURCE를
    # 줄이 빠진 슬라이스로 서술한다). 기본은 끈다 — 켜면 같은 슬라이스 집합의 Scout 입력이 달라진다.
    # `input: slice` 프롬프트의 슬라이스 타깃에서만 돈다(`input: source`는 함수 본문이라 — #529).
    scout_slice_trim: int = Field(default=0, ge=0, le=1)
    # Auditor `SOURCE`가 어디까지 믿을 입력인지를 `auditor_source` 이벤트의 `gate_reasons`로
    # 남기는가(`orchestrator/source_gate.py` — 슬라이서가 낸 절단·경계와 `SOURCE` 조립이 본 것에서
    # 사유 코드를 낸다). 0이면 이벤트가 옛 모양 그대로다. **관측 전용이다** — Auditor 입력(SOURCE·
    # FACTS)도 프롬프트 판도 안 바꾸므로 `AUDITOR_SOURCE_OPTIONS`(입력을 넓혀 meta
    # `source_options`가 서술해야 하는 옵션)가 아니라 `scout_slice_trim`과 같은 자리다.
    auditor_source_gate: int = Field(default=0, ge=0, le=1)
    # Auditor가 `repro_spec`을 비워 내면 하네스가 합성하는가 (#S7). 0이면 종전 — 비면 그대로
    # 비어 F-9(`repro_spec_missing`)로 `hypothesized`에 멈추고 8단계가 안 돈다. 1이면
    # `auditor._ensure_repro_origin`이 후보 위치로 명세를 합성해(`origin=harness`) E3(`validated`)
    # 까지 올려 8단계가 돈다. CyberGym Level 1처럼 과제가 재현법을 안 주는 run을 위한 것이라
    # 기본은 끈다.
    # ⚠ E4(`proven`)는 이 값과 무관하게 실행 로그를 요구한다(`ReproOrigin` 주석). 모델이 낸 명세는
    # 이 값과 무관하게 `origin=model`로 덮인다.
    # ⚠ **켜면 #232가 잠복에서 도달 가능으로 바뀐다** (#393 리뷰 MEDIUM-1) — 전엔 8단계에
    # 아무것도 안 올라와 #232가 닿을 수 없었다. 합성이 Finding을 validated로 올려 8단계가 도구를
    # 돌린다.
    # ⏵ **2026-10-05 — 그 뒤 문장이 코드와 어긋났으므로 정정한다.** 앞 판은 *「`p4`는 `ok=True`만
    # 보고 크래시와 exit 0을 안 가른다 -> 크래시 0건으로 proven이 날 수 있다」*고 적었다.
    # `gate.py`의 `p4`는 런타임 Evidence의 종료 코드가 **0도 `None`도 아닐 것을 이미 요구한다**
    # (이슈 #401). 그리고 **이 값이 먹이는 술어는 `p4`가 아니라 `p3`**다 — 합성 명세는
    # E3(`validated`)에 들어가고 `p4`(E4 `proven`)는 `repro_spec`을 읽지 않는다(위 ⚠가 적는
    # 그대로다). 남은 한계 둘은 **비-0 종료가 크래시의 대용 지표라는 점**과
    # **`expected_signal` 미대조**이고 그것이 #232다. 켜기 전에 #232를 먼저 본다.
    synthesize_repro_spec: int = Field(default=0, ge=0, le=1)
    # Auditor(5단계)가 한 후보에서 돌 수 있는 도구/조회 홉 수 상한 (이슈 #419).
    # **0이면 종전 한 방 경로다** — 노드가 홉 루프에 안 들어가므로 요청 바이트가 지금과 똑같다.
    # ⏵ 2026-10-04 — 필드 기본은 여전히 0이지만 **저장소 설정은 켜짐이다**: `config/budgets.yaml`이
    # 16을 들고 정본 Auditor가 루프 판(v9)이다. 「기본」이 어느 쪽인지는 이 필드가 아니라 그
    # 파일이 정한다.
    # 양수면 매 홉 `AuditorAction`(`src/schemas/auditor.py`)을 받아 조회·도구를 돌리고 관측을
    # 다음 홉 user 턴으로 되먹인다. (⏵ 2026-10-13 #600 H2ⓐ - 같은 관측이 Critic 입력에도 간다.)
    # ⏵ 2026-10-06 — 위 「정본 Auditor가 루프 판(v9)」은 2026-10-07에 v10으로 올라갔고
    # **봉투는 둘이다**: v8·v9는 매 홉 `AuditorAction`, v10은 형제 `AuditorProcedureAction`을
    # 낸다. 홉 구조·상한은 같고 `answer`가 싣는 최종 답만 다르다(`Finding` ↔
    # `AuditProcedure`). 봉투 둘은 `src/schemas/auditor.py`에 있다.
    # ⏵ 2026-10-09 — 서빙 판은 v11이고 봉투는 셋이다: v11은 셋째
    # `AuditorProcedureNullableCweAction`(최종 답
    # `AuditProcedureNullableCwe` — `no_signal`에서 `cwe`가 null일 수 있다, 이슈 #525)을 낸다.
    # ⏵ 2026-10-10 — 봉투는 넷이다: v12(서빙 판 아님)가 넷째
    # `AuditorProcedureAuditAlwaysAction`(최종 답 `AuditProcedureAuditAlways` — `no_signal`에도
    # `audit`를 채운다, 이슈 #578)을 낸다.
    # ⏵ 같은 날 — 서빙 판은 v13으로 올라갔고 그 봉투는 v11의 셋째와 같다(이슈 #67 — FACTS만 다르다).
    # ⏵ 2026-10-11 — 서빙 판은 v14이고 그 봉투는 v12의 넷째와 같다(이슈 #67 — FACTS만 다르다).
    # ⏵ 2026-10-13 — 서빙 판은 v15이고 봉투는 다섯째 `AuditorProcedureProbeInputAction`이다(v12
    # 봉투의 서브클래스 — `probe`의 `spec` 설명만 다르다). 허용집합은 여섯이다.
    # ⏵ 2026-10-14 — 서빙 판은 v16이고 봉투는 v15와 같다(새 봉투를 안 낸다 — 허용집합은 여섯
    # 그대로). ⏵ 같은 날 서빙 판은 v17이고 봉투는 같다(S20 Phase 14 — 허용집합은 여섯 그대로).
    #
    # **켤 때 값은 16이다.** 근거는 외부 관측 하나 — 모델이 한 세션에 gdb를 8회 불렀다는
    # 보고의 2배다(계산값). ⚠ **그 8회는 우리 하네스 실측이 아니다**: 이 루프가 서기 전의 우리
    # Auditor는 `call_tool`을 안 불렀고(켜면 `probe`가 부른다), 2026-10-05 전에 배선돼 있던 pov v2는
    # gdb를 광고하지 않았다(v3·v4는 한다). 그래서 16은 **계획값**이고 실측 근거는 홉당 usage
    # (trace `auditor_hop`)가 처음 돈 run에서 만든다.
    #
    # **값의 성격은 폭주 차단이다 — 창 적합을 보장하지 않는다.** vast 실측 창 262,144
    # (`config/serving/vastai.yaml`)에서 출력 상한 65,536(`output_max_by_role.auditor`)을 빼면 입력
    # 몫이 196,608(계산값)이다. v4 최악 추정 입력 28,833(`config/models.yaml`의 그 좌석
    # 「기동 조건」 절 — ⏵ 2026-10-04 정본은 v8이었고 ⏵ 2026-10-06부터 v9이며 prefix가 더 길어
    # hop0가 이보다 크다. ⏵ 2026-10-07부터 v10 — prefix 56,906자(v9는 35,501자 ·
    # `load_prompt(...).prefix`의 글자 수이고 토큰으로는 안 쟀다)라 hop0는 더 크다 · ⏵ 2026-10-09
    # v11 — 56,872자, 이슈 #525 · ⏵ 2026-10-10 v13 — prefix는 v11에서 한 조각만 빠졌다, 이슈 #67 · ⏵
    # 2026-10-11 v14 — prefix는 v12에서 한 조각만 빠졌다 · ⏵ 2026-10-13 v15 — 계약 문장 여섯이
    # 더해져 더 길다, 글자 수는 안 쟀다 · ⏵ 2026-10-14 v16 — v15에 prefix 다섯 군데, 글자 수는 안
    # 쟀다)을 넣으면 167,775가 남지만(계산값) **16홉이 그 몫을 다 채울 수 있다**: 최악 글자/토큰 비
    # 1.22에서 `lookup` 관측 16개만 196,736이라 몫을 넘는다(산수와 비의 출처는 `nodes/auditor.py`의
    # `_LOOKUP_MAX_CHARS`
    # 주석). 이 상한은 홉이 끝없이 쌓이는 것을 막을 뿐 창은 안 막는다 — 넘으면 서버가 크기
    # 거절한다. ⏵ 2026-10-13(이슈 #514) 그러면 노드가 그 홉에 `context_overflow` 이벤트를 남기고
    # 러너가 그 후보만 버린다(run은 가고 경고 한 줄 - 후보 전부가 그러면 #296의 성공 0 백스톱이
    # 선다).
    # ⚠ 53,248 창(입력 몫 20,480)은 **ATOM 시절 계획값**이다 — ATOM 사용 종료 2026-09-29(#340).
    #
    # 🔴 **`max_hops`(지금 3)를 올려 대신 쓰지 않는다.** 그 값을 읽는 자리가 둘이고
    # (`runner._run_ne_hops` · `cli estimate`의 `scout_calls = candidates * (1 + max_hops)`) 올리면
    # Scout 되묻기가 같이 늘어난다 — PoV 루프는 `pov_tool_hops`로 갈렸다. NE 쪽은
    # 효과가 이미 측정돼 있다 — hop0 NE 84좌석 중 5건, 루프에 든 10좌석(강제 8 · 스스로 NE 2)에서
    # 판정이 바뀐 것 1건, 정상 표본 0/24(`docs/experiments/`의 2026-09-27·09-28 NE 스모크).
    #
    # 켜면 프롬프트 판이 도구를 서술해야 한다 — meta가 `output_schema: AuditorAction`과 `tools:`를
    # 선언한 판이어야 한다. 어긋나면 `cli`의 run(종료 코드 2)·doctor(ERROR)와 노드가 **같은 함수**
    # (`nodes/auditor.loop_problem`)로 막는다(`AUDITOR_SOURCE_OPTIONS`의 `_check_source_options`
    # 선례). `cli estimate`의 Auditor 호출 상한(`_expected_calls`)도 이 값을 읽는다 — 후보당 `1 + 이
    # 값`이다.
    # ⏵ 2026-10-06 — 위 `output_schema: AuditorAction`은 v8·v9의 선언이다. v10은
    # `AuditorProcedureAction`을 선언한다. 노드의 허용집합은 `Finding`·`AuditorAction`·
    # `AuditorProcedureAction` 셋이고 `AuditProcedure`는 봉투의 `procedure` payload라 그 집합에
    # 없다(선언하면 `resolve_output_schema`가 거절한다).
    # ⏵ 2026-10-09 — v11은 `AuditorProcedureNullableCweAction`을 선언해 허용집합은 넷이다(이슈
    # #525).
    # ⏵ 2026-10-10 — v12는 `AuditorProcedureAuditAlwaysAction`을 선언해 허용집합은 다섯이다(이슈
    # #578).
    # ⏵ 2026-10-13 — v15는 `AuditorProcedureProbeInputAction`을 선언해 허용집합은 여섯이다.
    auditor_tool_hops: int = Field(default=0, ge=0, le=64)

    def auditor_source_options_on(self) -> tuple[str, ...]:
        """켜진 Auditor `SOURCE` 옵션(#349) — 결정 1-b를 넓히는 값들. 프롬프트가 그 입력을
        서술하는지는 `cli`가 meta의 `source_options`와 맞춘다(#351 리뷰 MEDIUM-2)."""
        return tuple(key for key in AUDITOR_SOURCE_OPTIONS if getattr(self, key))

    @model_validator(mode="after")
    def _role_caps_name_real_roles(self) -> "Thresholds":
        unknown = sorted(set(self.output_max_by_role) - set(PROMPTED_ROLES))
        if unknown:
            raise ValueError(
                f"output_max_by_role이 프롬프트 역할이 아닌 것을 부른다: {unknown}. "
                f"있는 것: {sorted(PROMPTED_ROLES)}"
            )
        bad = sorted(role for role, cap in self.output_max_by_role.items() if cap <= 0)
        if bad:
            raise ValueError(f"output_max_by_role의 값은 양수여야 한다: {bad}")
        return self

    def budget_for(self, level: CapsuleLevel) -> int:
        return int(getattr(self.capsule_budget_tokens, level.value))

    def output_max_for(self, role: str) -> int:
        """이 역할이 요청에 실을 출력 상한. 선언이 없으면 전역값이다.

        **이 함수가 유일한 해석 지점이어야 한다.** `config.thresholds.output_max`를 직접
        읽는 자리가 남으면 그 경로만 전역값으로 돌고, 실제로 쓰인 상한과
        `RunRecord.thresholds`에 적힌 값이 갈린다 — `output_max`가 선언만이고 강제가
        없던 시절(PR #36 이전)이 정확히 그 모양이었다.

        **모르는 이름에 전역값을 주지 않는다.** `dict.get`의 기본값은 "선언 안 한 역할"과
        "역할이 아닌 문자열"을 구분하지 않아, `"CRITIC"`·`"critick"`이 **선언한 상한을 무시한
        채** 전역값으로 돌고 그 run은 "역할별 상한을 줬다"고 기록된다 — `output_max_by_role`의
        오타를 로드 때 막는 것과 같은 결함을 읽는 쪽에 열어 두는 셈이다. 위 `_model_client`가
        `role`에 기본값을 안 두는 사유(PR #185)가 여기 한 겹 안쪽에 그대로 걸린다.
        """
        # `ConfigError`다 — `model_key_for`가 같은 부류(설정에 없는 역할)를 `KeyError`에서
        # 번역해 내보내는 것과 같은 자리라, 여기만 raw `KeyError`면 부르는 쪽이 두 가지를
        # 잡아야 한다.
        if role not in PROMPTED_ROLES:
            raise ConfigError(
                f"{role!r}은 프롬프트 역할이 아니다. 있는 것: {sorted(PROMPTED_ROLES)}"
            )
        return self.output_max_by_role.get(role, self.output_max)

    def flatten(self) -> dict[str, float]:
        """`RunRecord.thresholds`(dict[str, float])에 그대로 넣을 수 있는 평탄한 표현.

        중첩 값은 점 표기로 편다(`capsule_budget_tokens.light`). RunRecord가 평탄한
        float 맵을 요구하는 것은 조건 간 비교에서 임계값을 열 단위로 대조하기 위해서다.

        **역할별 `output_max`도 같은 표기로 여기 실린다** — `output_max_by_role.critic`.
        `reasoning_effort`가 `RunRecord`에 **필드를 새로 낸 것과 갈리는 자리**이고, 이유는
        타입 하나다: 저쪽은 문자열이라 `dict[str, float]`에 못 들어가고 이쪽은 int라
        들어간다(`RunRecord.model_reasoning_effort` 주석이 그 배제를 적어 뒀다). 그러므로
        기록 경로를 새로 열 필요가 없다.

        **선언이 없으면 키를 안 만든다.** 빈 채로 키가 생기면 이 필드 이전의 manifest와
        모양이 갈리는데 그 차이가 뜻하는 것이 없다 — `reasoning_efforts()`가 "선언한
        역할만 담는다"고 적은 것과 같은 판단이다.
        """
        dumped = self.model_dump()
        nested = dumped.pop("capsule_budget_tokens")
        by_role = dumped.pop("output_max_by_role")
        # 기본(0)이면 키를 안 만든다 — 위 「선언이 없으면 키를 안 만든다」와 같은 판단이다.
        # `scout_slice_trim`도 같다(#375 M-3): 기본 꺼짐 run의 `RunRecord.thresholds`에 `0.0`이
        # 생기면 이 필드 이전의 manifest와 모양이 갈리는데 그 차이가 뜻하는 것이 없다.
        # `scout_slice_trim`은 Auditor SOURCE 옵션이 아니라(프롬프트 `source_options` 대상이
        # 아님) `AUDITOR_SOURCE_OPTIONS`에 넣지 않고 여기서 따로 뺀다. `auditor_tool_hops`(#419)도
        # 같다 — 끈 run의 `run_manifest.json`에 `0.0`이 새로 생기면 지금까지 쌓인 모든
        # manifest와 열이 갈린다(요청 바이트가 같아도 재현성 기록의 모양은 갈린다). ⏵ 2026-10-04 —
        # 저장소 기본은 이제 켜짐(`budgets.yaml` 16)이라 이 키는 켠 run의 manifest에 보인다.
        # ⚠ 설정이 양수여도 Fake 좌석이 면제로 한 방으로 돌면 안 도는 값이라 러너가 이 키를 뺀다
        # (`runner._recorded_thresholds`).
        # `auditor_source_gate`도 같다.
        for key in (
            *AUDITOR_SOURCE_OPTIONS, "scout_slice_trim", "auditor_tool_hops", "auditor_source_gate"
        ):
            if not dumped[key]:
                dumped.pop(key)
        flat = {key: float(value) for key, value in dumped.items()}
        for level, tokens in nested.items():
            flat[f"capsule_budget_tokens.{level}"] = float(tokens)
        for role, cap in by_role.items():
            flat[f"output_max_by_role.{role}"] = float(cap)
        return flat


class HarnessConfig(HarnessModel):
    """한 프로필로 해석(resolve)된 설정 전체.

    노드·어댑터는 이 객체만 본다. YAML 파일 경로도, 프로필 이름도 여기서 끝난다.
    """

    profile_name: NonEmptyStr
    profile: ProfileEntry
    models: dict[str, ModelEntry]
    budget: Budget
    thresholds: Thresholds
    sampling: Sampling
    seed: int

    @property
    def swap(self) -> SwapPolicy | None:
        """이 프로필의 교대 선언. 없으면(`execution` 미선언 포함) `None`이다."""
        return self.profile.execution.swap if self.profile.execution is not None else None

    def model_key_for(self, role: str) -> str:
        try:
            return self.profile.roles[role]
        except KeyError as exc:
            raise ConfigError(f"프로필 {self.profile_name!r}에 역할 {role!r}이 없다") from exc

    def model_for(self, role: str) -> ModelEntry:
        return self.models[self.model_key_for(role)]

    def model_checksums(self) -> dict[str, str]:
        """`RunRecord.model_checksums`에 넣을 역할 → checksum 맵."""
        return {role: self.models[key].checksum for role, key in self.profile.roles.items()}

    def prompt_versions(self) -> dict[str, str]:
        """`RunRecord.prompt_versions`에 넣을 역할 → prompt_template_version 맵.

        **`PROMPTED_ROLES`만 담는다.** 전 역할을 담으면 `embedding`·`reranker`처럼
        프롬프트가 존재하지 않는 역할의 "버전"이 실행 기록에 적힌다 — 거짓 정보이고,
        ADR 0004가 `Role` 확장을 기각한 이유가 정확히 이것이다.

        `model_checksums()`는 반대로 전 역할을 담는다. 그 둘은 실제 모델이고 **임베딩
        모델이 바뀌면 검색 결과가 바뀌므로** 재현성 기록에서 뺄 수 없다. `RunRecord`가
        두 dict를 독립 필드로 선언하고 있어 키 집합이 달라도 문제가 없다.
        """
        return {
            role: self.models[self.profile.roles[role]].prompt_template_version
            for role in PROMPTED_ROLES
            if role in self.profile.roles
        }

    def reasoning_efforts(self) -> dict[str, str]:
        """`RunRecord.model_reasoning_effort`에 넣을 역할 → `reasoning_effort` 맵.

        **선언한 역할만 담는다.** 안 선언한 모델은 요청 payload에도 키가 안 생기므로
        (`ModelEntry.reasoning_effort` 주석), 기록에도 키가 없는 것이 실제와 같다 —
        `None`을 적으면 "기본값으로 돌았다"와 "그 서버가 이 파라미터를 아예 모른다"가
        같은 모양이 된다.

        **`PROMPTED_ROLES`만 보는 것은 `prompt_versions()`와 같은 사유다** —
        embedding·reranker는 `_model_client`를 타지 않아(그쪽은 `VllmEmbeddingClient`·
        `VllmRerankerClient`다) 이 값을 보낼 경로 자체가 없다. 담으면 거짓 정보다.
        """
        return {
            role: entry.reasoning_effort
            for role in PROMPTED_ROLES
            if role in self.profile.roles
            and (entry := self.models[self.profile.roles[role]]).reasoning_effort is not None
        }

    def request_params(self) -> dict[str, dict[str, JsonValue]]:
        """`RunRecord.model_request_params`에 넣을 역할 → 요청에 얹은 키. 규칙은 바로 위와 같다."""
        return {
            role: params
            for role in PROMPTED_ROLES
            if role in self.profile.roles
            and (params := self.models[self.profile.roles[role]].request_params())
        }

    def served_models(self) -> dict[str, str]:
        """이 배선에서 **실제로 뜨는** 역할 → model key.

        `model_checksums()`가 전 역할을 담는 것과 대비된다. 저쪽은 기록이라 빠짐이 없어야
        하고, 이쪽은 판정 대상이라 **해시할 대상이 실재하는 역할만** 담아야 한다 —
        `FakeModelClient`는 프로세스 안에서 도는 코드이므로 가중치도 토크나이저도 없고,
        그 checksum이 0인 것은 미기록이 아니라 정확한 값이다.

        역할 단위인 것은 한 프로필에서 Fake와 실서빙이 섞일 수 있기 때문이다(옛
        `single_gpu_12g`는 scout만 실서빙이었다 — 2026-09-24 삭제).

        **거르는 축이 둘이다** (이슈 #108, 선택지 b).

        1. `is_fake` — 위 사유.
        2. `retriever: fake`일 때의 `RETRIEVAL_ROLES` — embedding·reranker 클라이언트는
           hybrid 빌더 안에서만 만들어지므로(`adapters.factory`의 두 지점이 전부
           `adapters.retriever == "hybrid"` 게이트 안이다) 그 프로필에서는 한 번도 뜨지
           않는다. 조건은 바로 위 `ProfileEntry._hybrid_needs_retrieval_roles`가 쓰는 것과
           같다 — 그쪽은 "hybrid면 요구한다"이고 이쪽은 "hybrid가 아니면 안 센다"로,
           같은 사실의 양면이다.

        **선언 자체는 지우지 않는다.** `harness_pool`이 `retriever: fake`인 채로 그 두 역할에
        실모델을 적어 둔 것은 실수가 아니라 의도다(처음 이 판단을 적을 때는 옛 `b1_single`·
        `local_balanced`가 그 모양이었다) — hybrid로 올리는 날
        쓸 모델을 적어 둔 것이고, 지금 fake인 이유는 ATOM에 모델 5개를 동시에 띄우면
        R1(sm_121 커널 호환성) 검증의 실패 지점이 늘기 때문이다(그 근거를 적었던 옛
        `local_balanced`의 retriever 주석은 2026-09-24 프로필과 함께 지웠다 — git 이력).
        ⏵ 2026-10-08 — 그 장비(ATOM · `sm_121`)는 2026-09-29 사용 종료(#340 ·
        docs/portability.md §6). 지금 장비에서 fake로 두는 근거는 이 문단이 다시 세우지 않았다.
        그 정보를 프로필에서 지우면 나중에 hybrid로 올릴 때 어느 모델을 쓸지 다시 정해야
        한다. 그래서 **기록(`model_checksums`)은 그대로 두고 판정 대상만 좁힌다.**
        """
        wired = set(self.profile.roles)
        if self.profile.adapters.retriever != "hybrid":
            wired -= set(RETRIEVAL_ROLES)
        return {
            role: key
            for role, key in self.profile.roles.items()
            if role in wired and not self.models[key].is_fake
        }

    def unmeasured_models(self) -> list[str]:
        """benchmark가 비어 있는(실측 전) 모델 키.

        model-serving.md §5: 이런 모델은 production 프로필에 넣지 않는다.
        판정만 하고 차단은 `cli doctor`가 한다 — dev 프로필에서는 정상 상태이기 때문이다.
        """
        return sorted(
            key
            for key in set(self.profile.roles.values())
            if not self.models[key].benchmark.is_measured
        )

    def partially_measured_models(self) -> dict[str, tuple[str, ...]]:
        """**일부만** 잰 모델 키 → 아직 0인 항목.

        `unmeasured_models()`가 "아직 아무것도 안 쟀다"를 답한다면 이쪽은 "쟀는데 덜
        쟀다"를 답한다. 둘을 가르지 않으면 `is_measured`가 `any`이므로 절반만 채운
        모델이 production 게이트를 그냥 통과한다.
        """
        return {
            key: self.models[key].benchmark.missing_measurements
            for key in sorted(set(self.profile.roles.values()))
            if self.models[key].benchmark.is_measured
            and not self.models[key].benchmark.is_fully_measured
        }

    def models_measured_elsewhere(self, device: str) -> dict[str, str]:
        """benchmark를 **다른 장비에서** 잰 모델 키 → 그 장비.

        `unmeasured_models()`와 같은 계층의 판정이다. 저쪽이 "아직 안 쟀다"를 답한다면
        이쪽은 "쟀는데 여기가 아니다"를 답한다 — 둘 다 그 수치로 이 장비를 예측하면 안
        된다는 뜻이지만 대응이 다르다(저쪽은 재야 하고, 이쪽은 알고 쓰면 된다).

        `measured_on`이 `None`인 모델은 세지 않는다. "장비 미상"은 불일치가 아니라
        모름이고, 그것을 불일치로 보고하면 registry를 채우기 전 상태에서 전 모델이
        경고에 걸려 진짜 불일치가 묻힌다.
        """
        measured = (
            (key, self.models[key].benchmark.measured_on)
            for key in set(self.profile.roles.values())
        )
        return {key: on for key, on in sorted(measured) if on is not None and on != device}


def _read_yaml(path: Path) -> dict[str, object]:
    if not path.exists():
        raise ConfigError(f"설정 파일이 없다: {path}")
    with path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        raise ConfigError(f"{path}의 최상위는 매핑이어야 한다")
    return data


def _require(mapping: dict[str, object], key: str, where: str) -> dict:
    value = mapping.get(key)
    if not isinstance(value, dict):
        raise ConfigError(f"{where}에 매핑 {key!r} 항목이 없다")
    return value


def _check_model_refs(name: str, profile: ProfileEntry, models: dict[str, ModelEntry]) -> None:
    for role, key in profile.roles.items():
        if key not in models:
            raise ConfigError(f"프로필 {name!r}의 역할 {role!r}이 없는 모델 {key!r}을 참조한다")
        fallback = models[key].fallback
        if fallback is not None and fallback not in models:
            raise ConfigError(f"모델 {key!r}의 fallback {fallback!r}이 registry에 없다")


def _normalized_family(value: str) -> str:
    """계열 이름 비교용 정규화 — 앞뒤 공백과 대소문자만 없앤다.

    `ModelEntry.family`를 `Literal`로 닫지 않은 것은 의도다(그 필드 주석 참조 — 로스터가
    계속 바뀌므로 모델을 더할 때마다 코드가 따라오면 안 된다). **그 대가가 표기
    흔들림**이고, 원문 그대로 비교하면 `'Qwen'`·`' qwen'`이 `'qwen'`과 다른 계열로
    통과한다. 그러면 `_check_family_split`은 있는데 아무것도 안 잡는 상태가 되고,
    그것이 강제 장치가 없던 원래보다 나쁘다(이슈 #121·CLAUDE.md §0).

    **타입은 계속 열어 둔다. 좁히는 것은 비교뿐이다.** 값을 고쳐 저장하지도 않는다 —
    registry에 적힌 문자열은 그대로 `RunRecord`로 간다.
    """
    return value.strip().casefold()


def _check_family_split(name: str, profile: ProfileEntry, models: dict[str, ModelEntry]) -> None:
    """auditor와 critic이 **다른 계열**인지 본다 (ADR 0010 §4, 이슈 #121).

    evaluation.md §2.1이 B3/B3'의 조건으로 "제작사가 다른 모델"을 못박고
    model-serving.md와 `orchestrator/nodes/critic.py`가 같은 요구를 적는데, 세 곳 다
    문장이고 **검사하는 코드가 없었다.** 사람이 프로필을 손으로 맞추는 것이 유일한
    방어였고, 어긋나면 그 run은 `B3prime@…` 라벨을 달고 조용히 완주한다.

    **같은 모델 키는 허용한다.** B1은 전 역할이 같은 단일 모델이라(build-plan §8의
    "항상 최강 단일 멤버와 비교") 그 조건에서는 auditor == critic이 정의다. 단순
    부등으로 막으면 비교표의 기준선이 로드 단계에서 죽는다.

    **계열 미상은 통과가 아니다.** 판정할 근거가 없는 것이지 다른 것이 아니다 —
    `family:`를 안 적는 것만으로 이 검사를 우회할 수 있으면 강제 장치가 있다고 믿는
    채로 아무것도 안 잡는 상태가 되고, 그것이 원래보다 나쁘다(CLAUDE.md §0).

    `doctor`가 아니라 `load_config`에 있는 것은 `run`이 `doctor`를 부르지 않기
    때문이다 — WARN으로 두면 안 보고 지나갈 수 있다.
    """
    auditor_key = profile.roles.get("auditor")
    critic_key = profile.roles.get("critic")
    # 둘 중 하나가 없는 프로필은 `_has_required_roles`가 이미 잡는다. 여기서 다시
    # 말하지 않는 것은 같은 결함에 두 메시지가 나오면 어느 쪽이 원인인지 흐려지기 때문이다.
    if auditor_key is None or critic_key is None or auditor_key == critic_key:
        return

    auditor_family = models[auditor_key].family
    critic_family = models[critic_key].family
    unnamed = sorted(key for key in (auditor_key, critic_key) if models[key].family is None)
    if unnamed:
        raise ConfigError(
            f"프로필 {name!r}: 모델 {unnamed}의 family가 없어 auditor·critic의 계열을 "
            "판정할 수 없다. registry에 계열을 적는다"
        )
    # 비교는 정규화한 것으로, 메시지는 registry 원문으로 — 표기가 흔들린 것이 원인일 때
    # 그 사실이 메시지에 보여야 고칠 자리를 찾는다.
    if _normalized_family(str(auditor_family)) == _normalized_family(str(critic_family)):
        raise ConfigError(
            f"프로필 {name!r}의 auditor({auditor_key})와 critic({critic_key})이 같은 계열 "
            f"{auditor_family!r}·{critic_family!r}이다 — evaluation.md §2.1이 조건으로 "
            "제작사가 다른 모델을 요구한다"
        )


def _check_swap_lists_every_served_role(config: HarnessConfig) -> None:
    """교대를 선언한 프로필은 실서빙 역할을 **전부** `resident ∪ swappable`에 적는다 (ADR 0009
    불변식 3 — 「2026-09-29 제안」 절 1).

    `⊆ roles`(불변식 2)는 한 방향뿐이다. 교대는 목록에 적힌 역할의 서버만 띄운다
    (`runner._swap_for`) — 목록 밖의 실서빙 역할은 같은 키를 쓰는 다른 역할이 그때 떠 있을 때만
    서버를 만나고, 아니면 그 호출이 서버 없이 나가 run이 틀린 사유(`model_gateway_unavailable`)로
    멈춘다. 상주가 없으면 그 역할만 부르는 단계의 교대는 키 없이 — 명령 계약상 「자기 run의 서버
    전부 내림」으로 — 불린다. **같은 키가 다른 역할(상주 등) 덕에 떠 있어 당장은 도는 배선도
    거부한다** — 그 역할의 서버를 다른 역할의 선언에 기대게 두지 않는 엄격한 규칙이다. 그래서
    검색 역할이 실서빙인 hybrid 프로필은 교대를 선언할 수 없다 — 목록에 넣으면 `SwapPolicy`가,
    빼면 이 검사가 거부한다.

    `ProfileEntry`가 아니라 여기 있는 것은 실서빙 판정(`served_models()`)이 registry의
    `is_fake`를 봐야 해서다 — `_check_family_split`과 같은 자리다.
    """
    swap = config.swap
    if swap is None:
        return
    unlisted = sorted(set(config.served_models()) - {*swap.resident, *swap.swappable})
    if unlisted:
        raise ConfigError(
            f"프로필 {config.profile_name!r}의 execution.swap이 실서빙 역할 {unlisted}을 "
            "안 적었다 — 교대는 목록에 적힌 역할의 서버만 띄우므로 그 역할은 같은 키를 쓰는 다른 "
            "역할이 그때 떠 있을 때만 서버를 만난다. 전부 적거나(검색 역할은 못 적는다 — ADR 0012 "
            "결정 3 뒤) 교대를 선언하지 않는다"
        )


def _check_real_serving_limits_are_positive(config: HarnessConfig) -> None:
    """실모델을 서빙하는 프로필은 예산 상한 넷이 전부 양수여야 한다.

    `BudgetLedger._overrun`은 상한 0을 **검사하지 않는다** — `dev_fake`의 `gpu_seconds: 0`이
    「Fake는 GPU를 안 쓴다」는 뜻이라서다. 같은 0이 실서빙 프로필의 예산에 적히면 「한도 없음」이
    되어 임대 GPU가 시간 단위 과금 그대로 한도 없이 돈다. 이 저장소에서 `0`은 보통 「안 쟀다」이고
    (CLAUDE.md §0) `tool_seconds`는 안 적으면 0이다(`Budget`) — 실수로 0이 들어올 길이 둘이다.

    **실서빙 판정은 `served_models()`다.** 프로필의 `production`이 아니다(`harness_pool`이
    `production: false`인 채 실서빙이다). Fake 전용 프로필은 그대로 0을 쓴다. 이 검사가 원장이
    아니라 로드에 있는 것은 원장이 자기 모델이 실서빙인지 모르기 때문이다.

    **숫자를 정하지 않는다.** 아직 안 잰 상한이면 0이 아니라 직접 고른 잠정 상한을 적고 잠정치라고
    표시한다(`budgets.default.tool_seconds`가 그 모양이다). 막는 것은 0뿐이고 작은 값은 안 막는다.
    """
    served = config.served_models()
    if not served:
        return
    # `Budget`의 필드는 전부 상한이다 — 키를 여기 다시 적지 않으면 상한이 늘 때 이 검사가 안 샌다.
    zero = sorted(item for item, limit in config.budget.model_dump().items() if limit == 0)
    if zero:
        raise ConfigError(
            f"프로필 {config.profile_name!r}은 실모델 {sorted(served)}을 서빙하는데 예산 "
            f"{config.profile.budget!r}의 {zero}가 0이다 — 상한 0은 「검사하지 않는다」"
            "(`BudgetLedger`)라 임대 GPU가 한도 없이 돈다. 실서빙 프로필은 상한 넷을 전부 양수로 "
            "적는다(`tool_seconds`는 안 적으면 0이다). 0을 쓸 수 있는 것은 GPU를 안 쓰는 Fake "
            "전용 프로필뿐이다"
        )


def _check_role_output_caps_fit_context(config: HarnessConfig) -> None:
    """실서빙 역할의 출력 상한이 그 역할이 앉은 모델의 registry `context_limit`보다 작아야 한다
    (이슈 #214의 선택지 (a)).

    vLLM은 `prompt_tokens + max_tokens > max_model_len`이면 요청을 400으로 거절한다. 프롬프트는
    최소 1토큰이므로 상한이 창 **이상**이면 그 역할의 **모든** 호출이 400이다 — 입력 길이와
    무관하게 성립하는 유일한 경계라 이것만 막는다. 「입력 몫이 얼마 남아야 하는가」는 입력 크기에
    달려 로드 때 정할 수 없고, 임의 상수로 막으면 그 상수가 근거 없는 수가 된다(이슈 #214
    「닫히는 조건」의 ⚠).

    **대조 대상이 registry인 이유.** 서버 창(`--max-model-len`)은 로드 시점에 없다. 대신 **교대를
    선언한 프로필**의 run은 `check_serving`이 창 = `context_limit`을 강제하므로, 이 검사를 통과한
    상한은 그 창에서도 「창 이상」이 아니다. 교대를 선언할 수 없는 hybrid 실서빙 프로필
    (`harness_pool_hybrid`)은 이 밖이다 — 로드의 registry 대조만 서고 서버 창은 아무도 안 본다
    (#331).

    **실서빙 역할만 본다**(`served_models()`). Fake는 서버로 요청을 안 보내 400이 날 자리가 없다 —
    `_check_real_serving_limits_are_positive`와 같은 판정이다. 프롬프트를 안 받는 역할(embedding·
    reranker)은 출력 상한을 실을 경로가 없어 뺀다(`Thresholds.output_max_by_role` 주석).

    ⚠ `thresholds`는 프로필과 무관하게 공통이지만(`config/budgets.yaml`) 판정은 프로필마다
    갈린다 — 같은 상한이 창이 큰 모델에 앉은 프로필에서는 통과하고 작은 모델에 앉은 프로필에서는
    로드가 실패한다. 그 프로필에서 그 역할이 전량 400이라는 사실이 프로필마다 갈리기 때문이다.
    """
    over = [
        f"{role}({key}): 출력 상한 {config.thresholds.output_max_for(role)} ≥ context_limit "
        f"{config.models[key].context_limit}"
        for role, key in sorted(config.served_models().items())
        if role in PROMPTED_ROLES
        and config.thresholds.output_max_for(role) >= config.models[key].context_limit
    ]
    if over:
        raise ConfigError(
            f"프로필 {config.profile_name!r}에서 출력 상한이 모델 창 이상인 역할이 있다: "
            f"{'; '.join(over)} — vLLM은 입력 + max_tokens가 창을 넘으면 400을 내므로 그 역할의 "
            "호출이 전부 거절된다. `thresholds.output_max_by_role`(없으면 `output_max`)을 창보다 "
            "작게 적거나 창이 큰 모델을 앉힌다"
        )


def check_container_runtime_caps_tool_time(config: HarnessConfig) -> None:
    """`tool_runner`가 `fake`가 아니면 도구 시간 상한(`tool_seconds`)이 양수여야 한다 (이슈 #436).

    `RunContext.call_tool`은 Fake 런타임이 아니면 도구 시간을 **청구한다** - 그런데 상한 0은
    검사되지 않으므로(`BudgetLedger._overrun`) 실컨테이너가 한도 없이 돈다. 위 실서빙 가드는
    `served_models()`가 비는 Fake 모델 프로필에서 조기 return하므로 이 축을 못 본다 - 실제로
    `dev_fake` 예산을 쓰는 dev 프로필 셋이 실컨테이너를 돌고 있었다. 막는 것은 0뿐이고 값은
    정하지 않는다.

    **오버라이드 뒤에도 돈다** - `cli.load_profile`이 `--tool-runner`로 config를 갈아 끼운 뒤에
    이 함수를 다시 부른다(`load_config`만 지나면 `dev_fake` + `--tool-runner docker`가 건너뛴다)."""
    runner = config.profile.adapters.tool_runner
    if runner == "fake" or config.budget.tool_seconds > 0:
        return
    raise ConfigError(
        f"프로필 {config.profile_name!r}은 도구 런타임 {runner!r}로 실컨테이너를 돌리는데 예산 "
        f"{config.profile.budget!r}의 tool_seconds가 0이다 - 상한 0은 「검사하지 않는다」"
        "(`BudgetLedger`)라 도구 시간이 한도 없이 돈다. 컨테이너 프로필은 tool_seconds를 "
        "양수로 적는다(0을 쓸 수 있는 것은 도구 시간을 청구하지 않는 Fake 런타임뿐이다). "
        "`--tool-runner` 오버라이드라면 컨테이너 dev 프로필(`dev_docker` 등)을 쓴다"
    )


def load_config(profile: str, config_dir: Path = CONFIG_DIR) -> HarnessConfig:
    """프로필 하나를 해석해 `HarnessConfig`로 돌려준다.

    참조가 깨졌거나 필드가 빠지면 여기서 `ConfigError`로 실패한다. 실행 중간에
    KeyError로 터지면 그때까지 쓴 GPU 시간을 버리게 된다.
    """
    models_raw = _read_yaml(config_dir / "models.yaml")
    profiles_raw = _read_yaml(config_dir / "profiles.yaml")
    budgets_raw = _read_yaml(config_dir / "budgets.yaml")

    models = {
        key: ModelEntry.model_validate(value)
        for key, value in _require(models_raw, "models", "models.yaml").items()
    }
    profiles = _require(profiles_raw, "profiles", "profiles.yaml")
    if profile not in profiles:
        raise ConfigError(f"프로필 {profile!r}이 없다. 있는 것: {sorted(profiles)}")
    entry = ProfileEntry.model_validate(profiles[profile])
    _check_model_refs(profile, entry, models)
    _check_family_split(profile, entry, models)

    budgets = _require(budgets_raw, "budgets", "budgets.yaml")
    if entry.budget not in budgets:
        raise ConfigError(
            f"프로필 {profile!r}이 없는 예산 {entry.budget!r}을 참조한다. "
            f"있는 것: {sorted(budgets)}"
        )

    seed = budgets_raw.get("seed")
    if not isinstance(seed, int):
        raise ConfigError("budgets.yaml에 정수 seed 항목이 없다")

    config = HarnessConfig(
        profile_name=profile,
        profile=entry,
        models=models,
        budget=Budget.model_validate(budgets[entry.budget]),
        thresholds=Thresholds.model_validate(_require(budgets_raw, "thresholds", "budgets.yaml")),
        sampling=Sampling.model_validate(_require(budgets_raw, "sampling", "budgets.yaml")),
        seed=seed,
    )
    _check_swap_lists_every_served_role(config)
    _check_real_serving_limits_are_positive(config)
    _check_role_output_caps_fit_context(config)
    check_container_runtime_caps_tool_time(config)
    return config


# --- 장비 파일 (ADR 0012 Phase 6.5 — 단계 단위 콜드 재기동 교대) -----------------------


class ServerLaunch(HarnessModel):
    """장비 파일의 서버 한 행 — 교대 명령이 그 모델을 **어떻게 띄우는가**.

    하네스는 이 값을 쓰지 않는다. 읽는 쪽은 교대 명령(장비마다 다른 스크립트)이고, 하네스가
    여기 스키마를 두는 이유는 오타·범위 밖 값이 **run 시작 전에** 죽게 하려는 것과 창을
    registry와 대조하려는 것(`check_serving`) 둘이다.
    """

    # `--gpu-memory-utilization`.
    util: float = Field(gt=0, le=1)
    # `--max-num-seqs`.
    max_num_seqs: int = Field(gt=0)
    # `--max-model-len`. registry `context_limit`과 **같아야 한다**(`check_serving`).
    max_model_len: int = Field(gt=0)
    # 위 셋 밖의 기동 인자(parser·attention backend 등). 비어 있을 수 있다.
    extra_args: tuple[NonEmptyStr, ...] = ()
    # 기동 프로세스에만 거는 환경변수.
    env: dict[NonEmptyStr, str] = Field(default_factory=dict)
    # 준비 판정에 보낼 요청의 종류. 채팅 모델·임베딩·리랭커가 받는 엔드포인트가 다르다.
    probe: Literal["chat", "embed", "rerank"]
    # 기동 한 번의 준비 상한(초) — 명령을 친 뒤 준비 요청이 돌아오기까지. 넘으면 교대 명령이
    # 그 기동을 버리고 다시 시도한다. 장비·모델마다 몇 배씩 갈려 기본값을 두지 않는다(근거는
    # 장비 파일 주석).
    ready_timeout_s: float = Field(gt=0)

    def intended_args(self) -> list[str]:
        """이 행이 **의도한** 기동 인자 — `RunRecord.serving_launch_intended`에 실리는 값.

        `tools/serve/swap.sh`가 `vllm serve`에 붙이는 순서 그대로다(`--gpu-memory-utilization`
        -> `--max-model-len` -> `--max-num-seqs` -> `extra_args`). 모델·포트·revision은 registry가
        들므로 여기 없다. **환경변수(`env`)는 싣지 않는다** - 비밀이 가장 흔히 걸리는 자리이고
        샘플링 설정과 무관하다. 비밀처럼 보이는 인자는 가려서 낸다(`redact_launch_args`).
        """
        return redact_launch_args(
            [
                "--gpu-memory-utilization",
                str(self.util),
                "--max-model-len",
                str(self.max_model_len),
                "--max-num-seqs",
                str(self.max_num_seqs),
                *self.extra_args,
            ]
        )


# 이 이름 조각이 플래그에 들어 있으면 그 값은 비밀로 본다(`--api-key`·`--hf-token` ...). 조각 단위로
# 맞춘다 - `--tokenizer`·`--max-num-batched-tokens`는 걸리지 않는다. **고른 목록이고 잰 것이
# 아니다.**
_SECRET_FLAG_PARTS = frozenset(
    {"key", "token", "secret", "password", "passwd", "credential", "credentials", "apikey", "auth"}
)
# 플래그 없이 값만 박힌 모양(HF·OpenAI·GitHub·Slack 토큰 접두).
_SECRET_VALUE = re.compile(r"^(?:hf_|sk-|ghp_|github_pat_|xox[abp]-)\S+")
REDACTED = "***"


def _is_secret_flag(arg: str) -> bool:
    name = arg.split("=", 1)[0]
    parts = set(name.lstrip("-").lower().replace("_", "-").split("-"))
    return name.startswith("-") and bool(_SECRET_FLAG_PARTS & parts)


def redact_launch_args(args: list[str]) -> list[str]:
    """기동 인자에서 비밀로 보이는 값을 `***`로 가린다. 새 리스트를 돌려준다.

    세 모양을 본다: `--api-key VALUE`(다음 인자) · `--api-key=VALUE` · 플래그 없는 토큰 접두 값.
    플래그 이름의 `_`는 `-`로 보고 맞춘다(`--api_key`도 가린다).
    **휴리스틱이다** - 이름이 다른 비밀 플래그는 못 거른다. 그래서 장비 파일 `extra_args`에 비밀을
    직접 적지 않는다(환경변수로 넘긴다).
    """
    out: list[str] = []
    mask_next = False
    for arg in args:
        if mask_next and not arg.startswith("-"):
            out.append(REDACTED)
        elif _is_secret_flag(arg) and "=" in arg:
            out.append(f"{arg.split('=', 1)[0]}={REDACTED}")
        elif _SECRET_VALUE.match(arg):
            out.append(REDACTED)
        else:
            out.append(arg)
        mask_next = _is_secret_flag(arg) and "=" not in arg
    return out


class ServingConfig(HarnessModel):
    """`config/serving/<device>.yaml` — 한 장비에서 교대 명령과 모델별 기동 조건.

    **프로필에 적지 않는다.** 같은 프로필을 다른 장비에서 돌리면 명령과 util·창만 갈린다 —
    그 값을 프로필에 적으면 장비 수만큼 프로필이 는다. `run --device <device>`가 파일을 고른다.
    """

    # 교대 명령 argv 머리. 하네스가 뒤에 올릴 model key들을 붙인다(키 없음 = 전부 내림).
    command: tuple[NonEmptyStr, ...] = Field(min_length=1)
    # model key → 기동 조건.
    servers: dict[NonEmptyStr, ServerLaunch]


def load_serving(device: str, config_dir: Path = CONFIG_DIR) -> ServingConfig:
    """`config_dir/serving/<device>.yaml`을 읽는다. 없거나 깨졌으면 `ConfigError`다.

    장비 라벨은 값 집합이 열려 있어(ADR 0006) 경로 조각으로 쓰기 전에 `serving/` 안에 가둔다.
    스키마 오류도 `ConfigError`로 바꾼다 — CLI가 한 갈래로 잡아 run 디렉터리 전에 멈추게.
    """
    try:
        path = safe_join(config_dir / "serving", f"{device}.yaml")
        return ServingConfig.model_validate(_read_yaml(path))
    except (ConfigError, ValueError) as exc:  # 파일 없음 · PathEscape · ValidationError
        raise ConfigError(f"장비 파일 {device!r}: {exc}") from exc


def check_serving(config: HarnessConfig, serving: ServingConfig) -> None:
    """장비 파일이 이 config의 registry·교대 선언과 맞는가. 어긋나면 `ConfigError`다.

    1. 행의 키가 registry에 있다 — 없는 키는 교대 명령이 띄울 수 없는 모델이다.
    2. 교대가 올릴 served 모델(상주 ∪ 교대 역할의 실서빙 키)마다 행이 있다.
    3. 서버 창 `max_model_len`이 registry `context_limit`과 같다. 2026-09-25 ATOM에서 40,960인
       Scout 둘을 16,384로 띄운 채 돌렸고 하네스는 아무 말도 안 했다 — 프롬프트 12건이 400으로
       거절됐다(`run_20260925_0005`·`0006`, #331). 파일에 다른 값을 적으면 그 실수가 굳는다.
    """
    unknown = sorted(set(serving.servers) - set(config.models))
    if unknown:
        raise ConfigError(f"장비 파일의 서버 행이 registry에 없는 모델을 부른다: {unknown}")
    swap = config.swap
    if swap is not None:
        roles = {*swap.resident, *swap.swappable}
        needed = {key for role, key in config.served_models().items() if role in roles}
        missing = sorted(needed - set(serving.servers))
        if missing:
            raise ConfigError(
                f"프로필 {config.profile_name!r}이 교대로 올릴 모델의 서버 행이 장비 파일에 "
                f"없다: {missing}"
            )
    wrong = [
        f"{key}: max_model_len {row.max_model_len} ≠ context_limit "
        f"{config.models[key].context_limit}"
        for key, row in sorted(serving.servers.items())
        if row.max_model_len != config.models[key].context_limit
    ]
    if wrong:
        raise ConfigError(
            f"장비 파일의 서버 창이 registry와 다르다: {'; '.join(wrong)} — 창은 context_limit "
            "그대로 띄운다"
        )
