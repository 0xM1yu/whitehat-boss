# 엔지니어링 가이드

## 1. 지금 만들지 않는 것

처음부터 분산 시스템으로 만들지 않는다. 초기 구성은 다음으로 고정한다.

- 단일 Python 서비스
- SQLite 또는 DuckDB state
- 로컬 artifact 디렉터리
- Docker sandbox (도구 실행 격리)
- ~~bounded in-process queue~~ — D25로 제거(2026-08-31). 재투입 경로가 생기면 다시 연다

Redis, Kubernetes, 외부 vector database, 다중 worker는 **단일 노드 benchmark에서 병목이 확인된 뒤**에 추가한다. 병목 근거 없이 도입된 인프라는 리뷰에서 되돌린다.

## 2. 저장소 구조 (제안)

```
.
├── docs/                  # 이 문서 모음
├── prompts/               # 역할별 프롬프트, 버전 디렉터리
│   ├── scout/v1/          # 버전 디렉터리마다 prefix.md / body.md / meta.yaml
│   ├── scout/v2/
│   ├── auditor/v1/
│   ├── auditor/v2/
│   ├── critic/v1/
│   ├── critic/v2/
│   ├── single/v2/         # 단일패스 프롬프트(옛 이름 「B1·B3 통합」). v1 없이 v2로 들어왔다(`2f8109b`). 선언한 프로필이 없어 미배선
│   ├── scout_memory/v2/   # Scout 풀 3역할(ADR 0010 §3). 셋 다 v1 없이 v2로 들어왔다(`6a3c31e`)
│   ├── scout_boundary/v2/
│   └── scout_injection/v2/
├── config/
│   ├── models.yaml        # Model Registry (model-serving.md §5)
│   ├── profiles.yaml      # 벤치마크 조건별 프로필
│   └── budgets.yaml       # run 단위 예산
├── src/
│   ├── orchestrator/      # 파이프라인 노드 (순수 함수 + Pydantic)
│   ├── interfaces/        # ModelClient / Retriever / ToolRunner / StateStore
│   ├── adapters/          # 위 인터페이스의 구체 구현
│   ├── schemas/           # Candidate / Evidence / Finding / Decision / RunRecord
│   ├── bench.py           # §3 체크리스트 측정 (A~E). 진단 중 모델 서버에 요청을 보내는 쪽
│   ├── bench_metrics.py   # vLLM /metrics 판독 — 이름·파싱·구간 delta
│   ├── bench_report.py    # 체크리스트 판정 + models.yaml benchmark 반영
│   ├── preflight.py       # run 전 선결조건 점검(#534) — ssh·TCP·docker run으로 접속한다. 조립·도구 이미지·종료 코드
│   ├── preflight_remote.py  # 서버 쪽: 원격 교대·터널·원격 커밋·원격 가중치 캐시(WARN)·교대 없는 프로필의 서빙 포트
│   ├── preflight_oracle.py  # 채점 쪽: 채점기·API 키·카나리·과제 번들
│   ├── preflight_probes.py  # 바깥 세계 주입(`Probes`)과 공용 도우미
│   └── cli.py             # 표준 CLI 진입점
├── tools/                 # 정적분석·build·sanitizer·fuzz 래퍼 (Docker) · slicer/ 오프라인 Joern 슬라이서
├── eval/
│   ├── datasets/          # 평가셋 메타데이터 (원본은 별도 스토리지)
│   ├── leakage/           # 누출 자동 검증 스크립트
│   └── report/            # 비교표 생성
├── artifacts/             # run별 산출물 (git 제외)
└── tests/
```

`prompts/` 아래에서 **어느 버전을 읽을지는 노드가 아니라 config가 정한다**
(`ModelEntry.prompt_template_version` → `prompt_loader.load_prompt(role, version)`). 그래야
그 값이 `RunRecord.prompt_versions`와 같은 곳에서 나온다.

## 3. 교체 가능한 4개 인터페이스

도메인 로직은 이 4개에만 의존한다. 구현체 교체가 코드 변경 없이 설정만으로 가능해야 한다.
오케스트레이터 노드가 **부르는** 것은 `Deps`가 들고 있는 이 4개뿐이고, 그 수는 늘리지 않는다 —
러너가 직접 부르는 경계(`Oracle`)는 노드가 안 부르므로 이 수에 들지 않는다(§3.1).

```python
class ModelClient(Protocol):
    def complete(
        self, *, role: Role, prompt: Prompt, schema: type[SchemaT]
    ) -> ModelResult[SchemaT]: ...

class Retriever(Protocol):
    def search(self, query: Query, level: CapsuleLevel) -> ContextCapsule: ...
    def expand(self, capsule: ContextCapsule, need: MissingEvidence) -> ContextCapsule: ...

class ToolRunner(Protocol):
    def run(self, tool: str, args: ToolArgs, sandbox: SandboxSpec) -> Evidence: ...

class StateStore(Protocol):
    def put(self, obj: StoredObject) -> str: ...
    def get(self, kind: ObjectKind, id: str) -> StoredObject | None: ...
    def query(self, kind: ObjectKind, **filters: object) -> list[StoredObject]: ...
    def close(self) -> None: ...  # 멱등. run을 연 쪽(CLI)이 부른다 (#156)
```

> 구현 대응: `src/interfaces/`. 위 시그니처는 Harness v1 스캐폴드의 실제 코드에 맞춰
> 갱신했다. 바뀐 곳과 이유(**제안**이며 팀 검토 대상):
>
> | 곳 | 변경 | 이유 |
> | --- | --- | --- |
> | `ModelClient.role` | `str` → `Role` | 역할은 닫힌 집합이고 라우팅 키다. 문자열이면 오타가 런타임에야 드러난다 |
> | `ModelClient` 반환 | `Result` → `ModelResult[SchemaT]` | 구조화 출력의 타입을 잃지 않고, `usage`(input/output/cached 분리)와 실제 응답 모델을 함께 돌려줘야 한다(evaluation.md §5.2) |
> | `StateStore` 대상 | 4종 → `StoredObject` 8종 | `Artifact`·`MissingEvidence`·`ContextCapsule`·`RunRecord`도 저장 대상이다 |
> | `StateStore.kind` | `str` → `ObjectKind` | 자유 문자열이면 오타가 예외가 아니라 **빈 결과**를 낸다 |

**`query()`는 ID 오름차순을 보장한다.** 노드가 결과를 순회하는 순간 그 순서가 곧 실행
순서이고, 구현마다 갈라지면 프로필을 바꿨을 때만 재현이 깨진다.

### 3.1 구현체가 쓰는 하위 Protocol

`src/interfaces/`에는 위 4개 외에 **노드가 안 부르는 경계**도 있다 — 구현체끼리의 결합점과
러너가 직접 부르는 경계다. 도메인 로직은 이들을 보지 않으므로 위의 "4개"는 그대로다.

| Protocol | 누가 쓰는가 | 왜 인터페이스인가 |
| --- | --- | --- |
| `EmbeddingClient` · `RerankerClient` (`retrieval.py`) | `HybridRetriever`가 의존, 서빙 쪽이 제공 | knowledge-base.md §3의 2단계 재선별을 어떤 서버로 하든 Retriever 코드가 안 바뀌어야 한다. Fake 대체가 되어야 GPU 없이 이 경로가 돈다 |
| `Oracle` (`oracle.py`) | 러너가 8단계에서 부른다(`_submit_vul_to_oracle`·`_finalize_oracle`) — 노드는 안 부른다 | 채점 서버의 클라이언트는 어댑터이고 러너는 `src/adapters`를 import하지 않는다. 제출마다 `submit_vul`(PoV 중간 gen은 `query_vul`로 `vul` 판정도 읽는다), run당 한 번 `finalize`로 쪼갠 것은 fix 다리를 최종 PoC 한 개에만 돌리려는 것이다(any-of 방지). `finalize`는 최종으로 지정한 후보 하나에만 부른다 — 후보가 하나뿐이면 그 하나고, 둘 이상이면 같은 PoC 바이트끼리 접은 뒤 전순서(후보 자기 `vul` 응답 종료 코드 계단 — ⏵ 2026-10-14 이슈 #637, 이 한 코드가 지정이 읽는 유일한 채점기 값이다 → Critic 판정 → 내부 근거 등급 → `confidence` → 작은 바이트 → PoC 해시)로 하나를 고른다. 등급·`confidence`·Critic 판정 중 모르는 값이 있는 후보가 있으면 지정하지 않는다(`runner._designate_final` — ⏵ 2026-10-04에 「동점이면 거부」가 이 사슬로 바뀌었다). 서버 주소·과제 신원·키는 `cli._oracle_for`가 묶는다 |

**`Role`에 넣지 않은 이유**는 [ADR 0004](decisions/0004-retrieval-clients-separate-from-model-client.md)에 있다.
요약하면 `Role`은 프롬프트 버전·출력 스키마가 붙는 실행 계약 역할이고, 임베딩·리랭킹은
프롬프트도 스키마도 없다.

금지 사항: 오케스트레이터 노드에서 vLLM/LiteLLM/SQLite를 직접 import 하는 것. 어댑터 레이어를 거친다.
`src/orchestrator/**` 소스에 `src.adapters` 문자열이 없는지는 테스트가 강제한다.

## 4. 오케스트레이터 종속성 최소화

초기 runtime은 checkpoint·resume·human gate가 필요한 graph workflow에 적합한 **LangGraph v1**을 후보로 한다. 다만:

- 각 node는 **순수 Python 함수 + Pydantic schema**로 작성한다.
- 노드 함수는 LangGraph 타입을 시그니처에 노출하지 않는다.
- 상태는 `StateStore`를 통해서만 읽고 쓴다.

이 규칙을 지키면 Temporal·Prefect·자체 스케줄러로 이관할 때 노드 코드를 그대로 옮길 수 있다.

## 5. 재현성 (필수 기록 항목)

모든 run에서 다음을 `RunRecord`로 남긴다. 하나라도 빠지면 그 run의 결과는 비교표에 넣지 않는다.

- repository commit과 source hash
- model·tokenizer·quantization checksum
- prompt·agent spec version
- tool/container image digest**와 그것을 돌린 런타임** (digest는 런타임 사이에서도 아키텍처 사이에서도 비교할 수 없다 — STATUS.md §10)
- resolved budget와 threshold
- random seed와 sampling 설정
- KB 스냅샷 버전

## 6. 관찰 가능성

초기 필수는 **JSONL + OpenTelemetry trace**뿐이다. UI가 필요해지는 시점에 self-hosted Langfuse를 추가한다.

trace에 반드시 남길 것:

| 항목 | 이유 |
| --- | --- |
| 단계별 입력·출력 토큰 (cached 분리) | 비용 지표의 기반 |
| 역할별 모델·프롬프트 버전 | 조건 비교의 기반 |
| 후보 상태 전이와 사유 코드 | 단계별 제거율 산출 |
| 도구 실행 명령·종료 코드·소요 시간 | 검증 재현 |
| 모델 입력 context 전체 | 누출 검증 |

## 7. 도구 실행 격리

- 모든 build/sanitizer/fuzz/PoC 실행은 Docker sandbox 안에서만 한다.
- 기본값: 네트워크 차단, 읽기 전용 마운트, CPU·메모리·시간 상한.
- 대상 코드는 **공개 소스코드와 직접 구축한 로컬 환경**으로 한정한다. 외부 시스템에 대한 실행은 금지한다([disclosure-policy.md](disclosure-policy.md)).

## 8. 개발 규칙

1. **작은 파일 다수 > 큰 파일 소수.** 파일 200~400줄 목표, 800줄 상한.
2. 함수 50줄 이하, 중첩 4단계 이하. 조기 반환을 쓴다.
3. 에러는 삼키지 않는다. 도구 실패도 `Evidence`(실패 사유 포함)로 기록한다.
4. 스키마 경계에서 검증한다. 모델 출력은 항상 Pydantic으로 파싱한 뒤 사용한다.
5. 하드코딩 금지 — 임계값·예산·모델은 `config/`에서 온다.
6. 새 기능 전에 기존 코드에 같은 일을 하는 것이 있는지 먼저 찾는다.
7. 비결정적 로직(임계값 판정, dedupe 규칙)에는 반드시 테스트를 남긴다.

## 9. 되돌리기 비싼 결정은 ADR로

다음 범주는 `docs/decisions/`에 ADR을 남기고 진행한다.

- 오케스트레이터 선택·교체
- 상태 저장소 선택
- 모델 계열 확정 및 양자화 기본값
- 평가 프로토콜 변경(조건 정의, 데이터셋 구성)
- 분산 인프라 도입

ADR 형식: 배경 / 결정 / 대안과 기각 사유 / 결과와 되돌리는 비용 / 상태(제안·승인·폐기).
