# 0012. 하네스 정본 v2 — 슬라이스 입력 · need_evidence · 검색 역할 · 저장소 범위

- 상태: **승인** (2026-09-25 — 손수민 구두 승인, 조민규가 전했다. 사인오프 기록은 [README](README.md)의 0012 문단이다). 2026-09-24에 「제안」인 채 머지됐고(#260 — sumin 리뷰 M1), 이 ADR에 기댄 [D10](../open-questions/D/D10.md)·[D12](../open-questions/D/D12.md)는 승인 커밋(PR #275)이 닫았다(`Closes #60`·`#62` — 아래 「결과」)
- 일자: 2026-09-24
- 관련: [ADR 0010](0010-harness-structure-and-model-roster.md) §2·§3·「리뷰에서 정할 것」 #2, [PROJECT-CONTEXT.md](../../PROJECT-CONTEXT.md) §1·§2·§5, [build-plan.md](../build-plan.md) §5·§6, [architecture.md](../architecture.md) §3, [D3](../open-questions/D/D3.md)·[D10](../open-questions/D/D10.md)·[D12](../open-questions/D/D12.md)·[D13](../open-questions/D/D13.md)·[D20](../open-questions/D/D20.md)·[D74](../open-questions/D/D74.md)·[D78](../open-questions/D/D78.md)·[D82](../open-questions/D/D82.md)·[D83](../open-questions/D/D83.md)·[D84](../open-questions/D/D84.md)·[D85](../open-questions/D/D85.md)·[D86](../open-questions/D/D86.md), `prompts/scout_{memory,boundary,injection}/v3/prefix.md`, `prompts/auditor/v3/`, 팀 Notion 워크스페이스 [화햇대장](https://app.notion.com/p/3aebec1a15bc80c18c66fddd77844013)(실험팀 「Scout RAG baseline」·「Auditor·Critic 모델 최종 선정」·「scout 모델 후보 실험」·「Embedder Reranker 재실험」, 「Agent Fine-Tuning 팀 가이드」 — 2026-09-23 판독, 원문은 저장소에 없다)

## 왜 이 기록이 필요한가

**슬라이스가 무엇인지를 정본이 세 가지로 말하고, 코드는 그 어느 것도 아니다.** 이 ADR의 파일:줄은 `c301342`(그날의 `origin/main`) 기준이다. ~~코드(`src/`·`config/`·`prompts/`·`tools/`·`slicer*.py`)는 이 PR이 안 바꾸므로 그 줄 그대로 찾아지고,~~ (⏵ 2026-09-24 — 이 PR은 코드를 안 바꿨지만 같은 날 뒤에 머지된 PR들이 바꿨다. 코드 참조 가운데 **27곳이 main에서 그 줄이 아니다**(`c301342`의 줄 내용과 대조 · 같은 줄을 두 번 든 곳은 두 번 셌다). 파일별로 `config/models.yaml` 20(#262·#266·#267·#269 — 로스터 밖 키 아홉을 지운 #267이 가장 크다) · `slicer*.py` 5(#252·#255) · `src/interfaces/model_client.py` 2(#262)이고, 다른 파일의 참조는 그 줄 그대로다. 대부분은 옮겨졌고, 지운 `deepseek-r1-distill-qwen-32b` 항목을 가리키던 곳(`:677`·`:687`·`:688`·`:690`)은 줄이 없어졌다. 코드 줄은 `git show c301342:<경로>`로 읽는다.) (⏵ 2026-09-25 — Phase 3a·3b·3c가 `src/`를 더 바꿔(러너 · `nodes/scout.py` · `prompt_loader.py` · 두 Retriever 등) 그 파일들의 참조도 줄이 움직였다 — 위 「다른 파일의 참조는 그 줄 그대로다」는 2026-09-24까지다. 코드 줄은 여전히 `git show c301342:<경로>`로 읽는다.) **이 PR이 고치는 문서는 절·문구로 가리킨다** — 줄 번호를 곁들인 곳은 `c301342`의 줄이라고 밝혔다. 줄마다 쓰인 때는 `git log -L`로 뽑았다([CLAUDE.md](../../CLAUDE.md) §2.4).

| 자리 | 슬라이스의 자리 | 그 줄이 쓰인 때 |
| --- | --- | --- |
| [PROJECT-CONTEXT.md](../../PROJECT-CONTEXT.md) §2 「tree-sitter를 왜 계속 두는가」 문단의 첫 문장(`c301342`의 `:86`) | *"Joern은 **슬라이스**(모델이 볼 코드)를"* — **모델 입력** | `520b51a`(#196), 2026-09-15 |
| 같은 문단의 뒤 문장(`c301342`의 `:88-90`) | 후보 열거는 tree-sitter `symbols_for()`이고 *"슬라이스는 … 그 자리를 대신하지 못한다"* — **후보가 아니다** | 같은 커밋 |
| [build-plan.md](../build-plan.md) §5 「1 · 인덱스 · 슬라이스」 문단의 첫 문장(`c301342`의 `:134`) | *"여기서 잘라낸 조각이 뒤 단계가 보는 전부다"* — **모델 입력**(`c301342`의 원문이다. 이 PR은 그 문장을 남기고 ⚠ 노트를 달았다 — PR #275의 승인 커밋이 이 결정 쪽으로 좁혔다(2026-09-25)) | `1787c55`(2026-09-02) → `520b51a`(2026-09-15) |
| [D74](../open-questions/D/D74.md) (a) → PROJECT-CONTEXT.md §5 「Joern 슬라이서」 행 · [ADR 0010](0010-harness-structure-and-model-roster.md) 「리뷰에서 정할 것」 #2 행(`c301342`의 `:167` · `:235`) | *"후보 열거에만"*, 캡슐 본문은 안 건드린다 — **후보이고 모델 입력이 아니다** | `b865ee6`(#218), 2026-09-19 |
| Scout v3 프롬프트 (`scout_memory/v3/prefix.md:31` · `scout_boundary/v3/prefix.md:37-39` · `scout_injection/v3/prefix.md:24`) | *"`SOURCE` is a program slice"* — 원본 줄번호 gutter · `/* ===== path ===== */` 배너 · `FACTS.slice_criterion`(`scout_memory/v3/prefix.md:59-65`) — **모델 입력** | `5767a58`(#239), 2026-09-22 |

**#196의 분업은 「후보 = tree-sitter 심볼, 모델 입력 = 슬라이스」였고 D74 (a)는 정확히 그 반대(「후보 = 슬라이스, 모델 입력 = 심볼 본문」)다.** 코드는 둘 다 아니다 — 후보도 입력도 tree-sitter 심볼이다.

- `git grep -n -i slice -- src config` 0건(`c301342`). 슬라이서는 루트의 `slicer*.py` 오프라인 스크립트로만 있다(⏵ 2026-10-03 — 그 파일들은 `tools/slicer/`로 옮겼다).
- 후보: `_plan_targets`가 `ctx.symbols_for(artifact.path)`를 부르고(`src/orchestrator/runner.py:928`) 인덱스가 없으면 파일 하나가 후보다(`:929-935`).
- Scout의 `SOURCE`: `_ask_model`의 `ctx.read_source(capsule.target)`(`src/orchestrator/nodes/scout.py:119`) → `src/cli.py:440`의 hook → `read_symbol_source()`(`src/adapters/fake_retriever.py:193-204`)가 `line_start`~`line_end`를 잘라 준다 — **줄번호도 배너도 없다.**

**프롬프트는 이미 슬라이스 쪽으로 갔다.** 풀 프로필의 Scout 키가 v3를 고르므로(`config/models.yaml:667`·`:690`) 슬라이스를 전제한 프롬프트가 심볼 본문을 받는다. `FACT_KEYS`(`src/orchestrator/prompt_loader.py:56-65`)에 `slice_criterion`이 없고, 프롬프트가 쓰라는 `requested_context` 객체(`kind`·`target`·`purpose`·`request_code` — `scout_memory/v3/prefix.md:242` · `scout_boundary/v3/prefix.md:135`)를 스키마는 문자열 목록으로 받는다(`src/schemas/scout_response.py:46`). → [D82](../open-questions/D/D82.md). (⏵ 2026-09-26 — 스키마 쪽은 PR #302가 풀었다: `requested_context: list[RequestedContext]`. `FACT_KEYS`에 `slice_criterion`이 없는 것은 그대로다.)

**need_evidence는 소비자가 없다.** v3는 세 신호를 낸다 — `triage_review` 0.80 · `triage_need_evidence` 0.30 · `triage_reject` 0.10(`scout_memory/v3/prefix.md:111-113`). 하네스는 점수만 본다: `scout_threshold: 0.5`(`config/budgets.yaml:42`) 아래는 전부 `below_scout_threshold`로 보류돼(`src/orchestrator/policy.py:113-120`) **NE와 reject가 구별되지 않고**, 요청을 담은 `analysis_summary`는 버린다(`scout.py:150-159`). `git grep -n -w triage_need_evidence -- src` 0건. 되묻기 루프(홉 상한·정지 조건·같은 세션 멀티턴)의 명세는 팀 Notion 「Scout RAG baseline」에 있고, 요청 형식(`requested_context`의 네 키)은 v3 프롬프트에도 있다(`scout_memory/v3/prefix.md:242` · `scout_boundary/v3/prefix.md:135`).

**결정은 났는데 저장소에 없다.** 2026-09-23에 조민규가 슬라이스를 Scout 입력으로 정했고(council 권고의 반대 — 아래 「대안」 (A)), 같은 날 팀 피드백이 구조에 동의하면서 3단계의 쓰임을 추가 논의로 돌리고 injection 좌석을 Qwen3-14B로 잠정했다. 저장소를 하네스 개발 전용으로 두는 것도 같은 날 결정이다. [ADR 0011](0011-retire-the-2x2-condition-axis.md)과 같은 사정이다 — 결정이 저장소 밖에만 있으면 다음 사람이 옛 정본을 살아 있는 지시로 읽는다.

## 결정

### 1. Joern 슬라이스가 Scout의 코드 입력이다 — D74 (a)를 대체한다

조민규 결정(2026-09-23). [D74](../open-questions/D/D74.md) (a) *"후보 열거에만"*을 대체한다.

- **후보 = 슬라이스 1개.** `slices.json`(`slice_version: "slice-v2"` — `slicer.py:421`)의 `slices[]` 한 항목이 러너 타깃 하나다.
- **Scout의 `SOURCE`는 슬라이스 렌더다** — v3가 선언한 모양 그대로: 원본 줄번호 gutter, 파일마다 `/* ===== path ===== */` 배너. FACTS에 `slice_criterion`(`given_end`: `source`·`sink` — `scout_memory/v3/prefix.md:61-65`)을 싣는다.
  렌더러가 파일에서 줄을 세는 기준은 슬라이서의 줄 번호와 같아야 한다 — 지금 `read_symbol_source()`는
  `splitlines()`로 세어(`fake_retriever.py:202`) 폼피드 뒤 원문이 어긋난다([#259](https://github.com/0xM1yu/whitehat-boss/issues/259)).
- **tree-sitter는 짝으로 남는다 — 심볼 인덱스로.** E2의 `GRAPH` Evidence와 캡슐의 `callers`/`callees`(`names_for()`)를 계속 내고, 결정 2 resolver의 심볼 표(index-v2)가 된다. 인덱스를 Joern CPG로 옮기지 않는 것은 2026-09-24 조민규 결정이다 — ATOM 실행에서 실제로 부족할 때 다시 본다(아래 「대안」 (F)).
- **하네스는 슬라이서를 import하지 않는다** — `slices.json`을 파일로 받는다. 코드 경계는 인덱서와 같다 — 둘 다 `src/`와 서로 import하지 않고 JSON 파일 하나로만 만난다(인덱서 쪽 선언 `tools/harness-tools/indexer.py:11-14` · `slicer*.py`도 `src`를 import하지 않는다). **다른 것은 실행 주체다** — 인덱서는 하네스가 1단계에서 도구 컨테이너로 부르고(`indexer.py:4`), 슬라이서는 **run이 부르지 않는다** — Joern과 함께 하네스 run 밖에서 미리 돌아 `slices.json`을 남긴다(도구 이미지 `tools/harness-tools/Dockerfile`에 Joern·JDK가 없다). **여기서 「하네스 밖」은 이 뜻이다** — run이 부르지 않고 파일로만 만난다는 것이지 저장소 밖이라는 뜻이 아니다. 슬라이서는 이 저장소에서 개발한다(결정 5).
- **슬라이서의 `status`(`ok`·`partial`·`failed` — `slicer.py:426`)와 `run_signature`(`slicer_pipeline.py:168`)를 run 기록에 싣는다.** CPG가 못 본 줄은 모델도 못 본다([D74](../open-questions/D/D74.md)가 (a)를 고른 근거 ③) — 그 한계가 조용하지 않게 기록에 남아야 한다.
- 슬라이스 없이 v3 Scout 프롬프트를 부르는 run은 **시작 전에 거부**한다 — 지금의 조용한 불일치([D82](../open-questions/D/D82.md))가 서는 자리다. 슬라이스를 안 쓰는 프로필(dev·단일 `scout`)은 지금 경로 그대로다. (⏵ 2026-09-25 — 가드가 들어갔다: 프롬프트 meta가 `input: slice`를 선언하고(v3 Scout 셋) `cli.run`이 그런 프롬프트를 `--slices` 없이 부르는 run을 run 디렉터리를 잡기 전에 exit 2로 거부한다. 모델에 안 닿는 run(`--dry-run`·낮은 `--stop-after`)은 막지 않고 `doctor`는 같은 판정을 WARN으로 낸다. 불일치를 없애는 쪽 — 슬라이스를 `SOURCE`로 렌더하는 것 — 은 이 가드 아래에 쌓인 3c가 했다(`run --slices` run에 한해서다). D82의 「닫히는 조건」은 그래도 안 찬다 — need_evidence 쪽(③④⑤)이 Phase 5다. ⏵ 2026-09-26 — ③④는 찼다 — 스키마는 PR #302이고, ③의 판정(루프가 `requested_context`를 소비한다)은 이 변경이다. ⑤는 절반만 찼다 — 신호·점수가 어긋나면(예: `triage_reject` + 높은 점수) 신호가 이기는 것은 테스트로 고정됐으나, 첫 코드가 세 신호 밖인 응답이 점수만으로 승격되지 않는 것은 아직 테스트가 없다(설계상 v1·v2와 같은 경로로 떨어진다 — `scout.finish()` docstring). D82 본문 2026-09-26 절이 자세하다. D82는 2026-10-12 닫혔다(③④⑤·⑥의 처분은 D82.md 맨 위 ⏵).) (⏵ **2026-10-07 — 서빙 Scout 판이 v4가 되며 이 가드는 아무것도 막지 않는다**(PR #524 배선). v4 meta가 `input: source`라 저장소의 어느 프로필도 가드의 목록을 채우지 않는다 — 판정은 `cli._slice_input_prompts` 하나이고, v3는 디스크에 남아 그것을 고르는 설정이면 다시 찬다. ⚠ **막아야 할 어긋남의 방향이 뒤집혔다** — 이제 위험한 쪽은 「원본 소스 프롬프트인데 슬라이스가 온다」이고, 그것을 보는 줄은 없다.)

**[D78](../open-questions/D/D78.md)의 셋은 이렇게 푼다.**

| | D78이 든 것 | 이 결정 |
| --- | --- | --- |
| ① | `Location`이 불연속 줄 집합을 못 든다 | `Location`은 슬라이스의 **대표 위치**(`entry`의 file·line)이고, 어느 슬라이스인지는 **`Candidate.slice_id`**가 든다 — 하네스가 도장하고 모델은 쓰지 않는다(`scout_member`와 같은 자리 — `scout.py:157-159` · `src/orchestrator/context.py:321-324`) |
| ② | 캡슐 캐시 키에 슬라이서 신원이 없다 | `cache_key_for()`(두 Retriever)에 slice id와 `run_signature`를 넣고 `CAPSULE_POLICY_VERSION`(`fake_retriever.py:47` · `hybrid_retriever.py:76`)을 올린다 |
| ③ | 슬라이스를 STATIC Evidence로 적으면 E1이 구조적으로 참이다 | 슬라이스와 RETRIEVED 블록은 **Evidence로 기록하지 않는다** — `static_ok`·`graph_ok`가 후보와 결합 없는 존재 검사라(`src/orchestrator/gate.py:140` · `:145`) 적는 순간 E1·E2가 부푼다 |

⚠ **①은 절반만 풀린다.** Case B의 `entry.symbol`은 감싸는 함수가 아니라 sink 호출 이름이고(`slicer_queries.py:302` — `"symbol" -> c.name`) 감싸는 함수는 후보의 `scopes`에 있다(`slicer_candidates.py:31`). 군집 슬라이스는 `members`를 합쳐 여러 함수에 걸친다(`slicer_source.py:223`). `Location.symbol`을 무엇으로 채울지, `names_for()`의 「본문 줄 범위」 술어를 슬라이스 대상에 어떻게 쓸지는 Phase 3이 정한다. (⏵ 2026-09-25 — 앞의 것은 정해졌다: 대표 후보의 `scopes[0].symbol`(감싸는 함수), 없으면 Case A는 entry 이름이고 Case B는 비운다 — sink 호출 이름은 함수 이름이 아니다(Phase 3b PR #284와 그 리뷰 수정 — `src/adapters/slices.py`의 `slice_refs`). ~~뒤의 것은 아직이다 — 슬라이스 타깃의 캡슐 그래프 섹션이 entry 줄 하나를 「본문」으로 본다. 그 관측은 [D78](../open-questions/D/D78.md)의 2026-09-25 ⏵다.~~ → 뒤의 것도 같은 날 정해졌다(#288): 슬라이스의 (file, line) 집합이 본문이고 「본문 안에 정의」는 정의 범위가 그 집합과 겹치는가다 — [D78](../open-questions/D/D78.md) 닫힘.)

⚠ **이 결정은 슬라이스 입력의 탐지 품질을 근거로 하지 않는다 — 그 값이 없다.** Scout 좌석 모델을 고른 실험팀 「scout 모델 후보 실험」은 원본 함수로 쟀다(「슬라이스 품질 문제로 원본 파일 제공」 — 팀 보고, run ID 없음). 근거는 방향의 정렬이다 — 정본(PROJECT-CONTEXT.md §2 「tree-sitter를 왜 계속 두는가」 · build-plan.md §5 1단계 — 위 「왜」 표), 풀 프로필이 부르는 v3 프롬프트, 하네스팀 브리핑(2026-09-09~14, 「슬라이스 한 개가 후보 한 개」 — 팀 문서)이 같은 쪽을 가리킨다.

### 1-b. Auditor의 입력 표현 — **정해짐: 넓은 (i)** (2026-09-25)

현 Auditor v3 프롬프트는 입력을 원본 소스로 선언한다 — `prompts/auditor/v3/body.md:13`(*"representation: original source code"*) · `prompts/auditor/v3/prefix.md:9`(*"contains **original source code**"*). registry의 Auditor는 아직 v1을 고르고(`config/models.yaml:718`) v3는 디스크에만 있다(`prompts/auditor/v3/meta.yaml:1-2`). ⏵ 2026-09-29 — registry의 Auditor(`qwen3.8-27b`)가 v3를 고른다. 근거 본문 배선과 같은 변경이다(`src/orchestrator/nodes/auditor.py`의 `_CONTENT_AWARE_VERSIONS`). ⏵ 2026-09-29 — 같은 날 v4로 올렸다(#328 머지 뒤). v4도 입력을 원본 소스로 선언한다(`prompts/auditor/v4/body.md`의 「original source code」). ⏵ **2026-10-03 — v6로 올렸다**(#418·#421 · v5는 건너뛰었다). v6도 같이 선언한다 — `prompts/auditor/v6/body.md`의 「Target under analysis — original source code」 제목이고, 그 `body.md`는 v5와 바이트 동일이다. 위 두 줄 번호는 쓴 날의 것이다 — 키로 찾는다. ⏵ 2026-10-08 — 그 뒤 v7·v8(2026-10-04) · v9(v8 + `auditor_guards` 서술 · PR #508, 2026-10-06 01:54 KST 머지) · v10(v7 전면 재작성 · PR #521, 2026-10-07 16:12 KST 머지)으로 올랐다. `body.md`는 v5~v10이 바이트 동일이라 같은 「Target under analysis — original source code」 제목이고 선언은 그대로다. 지금 고르는 판은 여기 적지 않는다 — `config/models.yaml` `qwen3.8-27b`의 `prompt_template_version`이 정본이고 경과는 [PROJECT-CONTEXT.md](../../PROJECT-CONTEXT.md) §5 Auditor 행이 든다.

- **(i) 원본 소스 — 제안 기본값.** Auditor는 슬라이스가 걸친 함수의 원본 본문을 받는다. 프롬프트가 기대하지 않는 표현을 넣으면 Scout의 D82가 Auditor에서 재현된다. 대가: Scout와 Auditor가 다른 코드를 본다 — 슬라이스는 여러 파일에 걸칠 수 있어(`scout_boundary/v3/prefix.md:50`) 「어느 함수(들)의 본문인가」를 Phase 3이 정해야 한다. (⏵ 2026-09-25 — Phase 3c는 이것을 좁게 풀었다: entry 줄을 품는 함수 하나다(인덱스의 가장 좁은 심볼, 품는 심볼을 모르면 entry 앞뒤 고정 창(`_AUDITOR_WINDOW_LINES`) — `runner._auditor_target`). 군집 슬라이스가 걸친 다른 함수는 Auditor가 아직 못 본다. 1-b 결정 자체는 그대로 열려 있다. ⏵ 같은 날 넓혔다 — 아래 결정 문단.)
- **(ii) 슬라이스 렌더** — Scout와 같은 입력. 팀 계약(Notion 「Auditor·Critic 모델 최종 선정」)이 Auditor 입력을 「슬라이스+줄번호+정적 근거」 캡슐로 적는 쪽이다. 선행: Auditor 프롬프트 개정(위 두 줄).

결정권은 손수민 리뷰, 기한 2026-09-26(아래 「이 ADR이 열어 두는 것」 1-b). 결정 전까지는 (i)이다.

**⏵ 2026-09-25 — 결정: 넓은 (i) + 크기 상한**(팀원 전원 합의 — 조민규 전언, 기한 전). Auditor의 `SOURCE`는 슬라이스가 걸친 함수의 원본 전부다 — entry를 감싸는 함수(`runner._auditor_target` — 위 ⏵ 노트의 범위 그대로이고 캡슐의 대상이다)가 먼저이고, 나머지는 슬라이스 줄마다 그 줄을 품는 가장 좁은 인덱스 심볼을 (파일, 시작 줄) 순으로 붙인다(`runner._auditor_source`). 문자 수 상한 `_AUDITOR_SOURCE_CHARS`(15,000 — 고를 때는 계산값이었고 **2026-09-25 ATOM에서 쟀다**: 이 상한으로 실서빙 Auditor에 태운 147호출이 서빙 입력 몫 20,480토큰 안에 들었다 — 초과 0건 · 최대 `input_tokens` 15,437 · 여유 5,043. run `run_20260925_0002`·`0003`·`0004`, 브랜치 `exp/atom-slice-smoke` @ `41f802a`(머지 안 함), `checksum` 0이라 비교표 밖. ⚠ **그 147호출은 Auditor 프롬프트 v1의 것이다** — 2026-09-29에 registry의 Auditor가 v3가 되어 입력이 +3,212~8,740토큰(계산값) 늘 수 있고, v3의 입력 토큰은 미실측이다(`config/models.yaml`의 `qwen3.8-27b` 항목). ⏵ 2026-09-29 — 같은 날 v4로 올라 증가분은 +4,923~13,396토큰(계산값 — prefix +12,251자 · body 틀 +92자 · 근거 본문 최대 4,000자 = 16,343자를 같은 비 1.22~3.32로 나눴다)이고, v4 입력 토큰은 미실측이다. ⏵ **2026-10-03 — v6로 올라**(#418·#421 · v5는 건너뛰었다) 증가분은 **+7,330~19,948토큰**(계산값 — prefix +20,244자 · body 틀 +92자 · 근거 본문 최대 4,000자 = 24,336자를 같은 비 1.22~3.32로 나눴다)이다. **v4로 돈 run이 끝까지 0건이라 위 v4 수는 미실측으로 끝났고 v6 입력 토큰도 미실측이다**(`docs/experiments/` grep, 2026-10-03 — ⚠ `artifacts/`가 gitignore라 커밋된 보고서만 센 것이다). 위 입력 몫 20,480 창이면 선언된 비 안에서는 하한 +7,330만으로도 여유 5,043을 넘지만(계산값) **「항상 넘는다」로 적지 않는다** — 영어 텍스트의 글자/토큰 비를 4 아래로 묶은 실측이 없어 추정이고 상한 증명이 아니다. 지금 창은 `context_limit` 262,144(입력 몫 196,608(⏵ 2026-10-03 상한 인상 뒤 — 그 전은 229,376))라 상한 35,385(= 15,437 + 19,948, 계산값)도 안쪽이다. 산수와 한계는 `config/models.yaml`의 `qwen3.8-27b` 항목이 든다. ⚠ 위 v3 입력 토큰 서술은 쓰일 때 이미 틀렸다 — 서술은 `5c11c08`(2026-09-29 15:29 KST)이고, 스모크는 그보다 앞선 06:24~07:25 KST에 2호출로 쟀다(3,782·3,766토큰 — 슬라이스 719자라 작아 위 계산값의 검증이 못 된다 · [스모크 보고서](../experiments/harness/2026-09-29-stage-swap-smoke-atom.md)). 비 1.22~3.32의 출처·한계는 `config/models.yaml`의 `qwen3.8-27b` 항목이 든다. ⚠ 같은 측정에서 글자/토큰 비가 1.22~3.32로 벌어져 **글자 상한이 토큰을 묶어 주지는 않는다** — 근거는 이 실측이지 환산이 아니다. 자세한 것은 그 상수의 주석)를 넘는 함수는 함수째 빼고 entry 함수는 늘 넣는다. 뺀 함수 수와 어느 함수에도 안 든 줄 수(구조체 필드·전역·인덱서가 안 받는 `.h`), 그리고 entry 함수 혼자의 크기(`anchor_chars` — 상한이 그것은 안 본다)는 trace `auditor_source`가 든다. (ii)는 고르지 않았다 — Auditor 프롬프트가 원본을 선언한다(위 첫 문단). ⏵ 2026-10-08 — 위 증가분 사슬과 「상한 35,385도 안쪽」은 **v6까지의 계산**이다. 그 뒤 판이 v7·v8·v9·v10으로 올라 prefix가 더 길어졌고(v10 56,906자 — `load_prompt(...).prefix` 길이, 2026-10-06, [PROJECT-CONTEXT.md](../../PROJECT-CONTEXT.md) §5 Auditor 행) SOURCE 상한도 20,000토큰이 됐다(아래 결정 1-b의 2026-10-03 노트). v7까지의 계산은 `config/models.yaml`의 `qwen3.8-27b` 항목이 들고, v8~v10의 토큰 환산은 없다(같은 §5 행 — 「토큰으로 환산한 수는 안 쟀다」). 호출당 입력 토큰은 v6(`SOURCE` 옵션 0)의 실측이 있다 — [10-04 운영 run](../experiments/harness/2026-10-04-cybergym-operational-run-vastai-remote.md) §5 Auditor `in 토큰` 6,764~7,257(WSL 5호출) · 6,220~13,039(Windows 3호출). 그 보고서가 이 수를 main 기본 구성(v7 이후 · `SOURCE` 옵션 켬)에 못 옮긴다고 적는다(같은 §5 머리 · §메타 `prompt_versions`). v7 이후 판의 입력 토큰은 미실측이다 — v10으로 돈 [10-08 run](../experiments/harness/2026-10-08-first-real-serving-run-arvo1065-vastai-remote.md)의 보고서는 Auditor 출력 토큰만 든다(§4). v4·v5로 돈 run은 0건이다.

**⏵ 2026-10-03 — 결정 1-b를 넓힌다**(사용자 결정 · [#349](https://github.com/0xM1yu/whitehat-boss/issues/349)). 슬라이스가 걸친 함수 뒤에 그 코드가 쓰는 정의(매크로 · 타입 · 파일 변수), 실린 함수가 부르는 함수 본문 1단, entry 함수를 부르는 호출 지점 앞뒤 10줄을 붙이고, 상한은 15,000자에서 Auditor 토크나이저 20,000토큰으로 바꾼다(`config/budgets.yaml`의 옵션 넷 · `runner._auditor_source`). entry 줄이기는 끈 채다. 서빙 판이 그 모양을 서술하고 meta `source_options`로 선언한다 — ⏵ 2026-10-04 그 판은 v6를 그 자리에서 고친 것이 아니라 v7(v6 + 그 서술)이고, 같은 날 **v8**(도구 루프, #419)이 그 서술을 바이트 그대로 물려받아 배선됐다(#418의 수가 v6 문장의 것이라서 — [agent-contracts](../agent-contracts.md) §4 규칙 1). ⏵ 2026-10-08 — 그 뒤 v9(v8 + `auditor_guards` 서술 · PR #508, 2026-10-06 01:54 KST 머지)가 v8 서술을 바이트 그대로 물려받았고, v10(v7 전면 재작성 · PR #521, 2026-10-07 16:12 KST 머지)은 그 서술을 다시 써서 바이트 동일이 아니다. 서술이 러너와 맞는지는 판 번호 없이 서빙 판을 고르는 `tests/test_config.py`의 검사가 문다. 서빙 판은 여기 적지 않는다 — `config/models.yaml` `qwen3.8-27b`의 `prompt_template_version`이 정본이다. 근거 -> FT 데이터를 이 조립기로 만들므로 학습 입력과 실제 run 입력이 같아야 하고, 정의 · 피호출 · 호출자 맥락이 빠지면 경계 판단을 못 한다는 것([D95](../open-questions/D/D95.md)). 고른 조합의 근거는 로컬 소표본 둘이다(D95 본문). 위 「상한 15,000자」 서술은 이 날까지의 것이다.

### 2. need_evidence 정본 프로토콜 — 팀 Notion 「Scout RAG baseline」

명세의 원천은 실험팀 Notion 「Scout RAG baseline」이다(화햇대장 › 실험팀 — 원문은 저장소에 없다). 이 ADR은 요약만 든다.

| 항목 | 정본 |
| --- | --- |
| 세션 | **(슬라이스, 에이전트)마다 하나.** 같은 슬라이스를 세 좌석이 독립으로 판정한다 |
| 신호 | `candidate.reason_codes[0]` — `triage_review`·`triage_need_evidence`·`triage_reject` 셋 |
| 요청 | `analysis_summary.requested_context[]` **객체** — `kind`·`target`·`purpose`·`request_code` 네 키(`scout_memory/v3/prefix.md:242` · `scout_boundary/v3/prefix.md:135`). Notion 명세는 앞 셋을 들고 v3 프롬프트는 `request_code`까지 넷을 쓴다 — 스키마는 프롬프트를 따른다 |
| 신뢰 | **`target`만 조회에 쓴다.** `kind`는 동명 정의를 고르는 점수에만 쓴다 |
| 대화 | **같은 세션 멀티턴** — 찾은 것(RETRIEVED)과 못 찾은 것(UNFILLED)이 다음 user 턴으로 간다 |
| 상한 | **최대 3홉** — `max_hops`(결정 6이 D3의 확장 상한을 이것으로 대체한다) |
| 정지 | 여섯 — 신호가 NE가 아님 · 파싱 실패 · 3홉 초과 · 직전과 같은 요청 반복 · `requested_context`가 비었음 · resolver가 하나도 못 줌 |
| 기록 | 홉마다 `hop`·`signal`·`schema_ok`·`stop_reason`·`requests`·`followup_text`·`latency_s`·`usage`·`raw_text` |
| 좌석 집계 | 한 좌석이라도 review면 **review를 낸 좌석의 후보**가 TRIAGED(Auditor로 — 좌석마다 각각, 아래 첫 불릿)이고, 다른 좌석의 NE는 루프를 돌지 않는다(그 좌석의 `stop_reason`은 `peer_review` — 위 정지 여섯 밖이라 이 ADR이 붙이는 이름이고 제안 초안이다, Phase 5가 정한다 — ⏵ 2026-09-26 이 변경이 확정했다: `NeStopReason.PEER_REVIEW`, `src/orchestrator/runner.py`의 `_settle_scout_session`). review가 없고 NE가 있으면 NE를 낸 좌석만 루프를 돈다. 전부 reject면 끝 |

- **좌석 집계는 루프를 어느 좌석이 도는가의 규칙이고, Auditor로 보내는 단위는 [ADR 0010](0010-harness-structure-and-model-roster.md) §3의 합집합(방식 ①)을 그대로 쓴다.** 0010 원문은 *"임계값을 넘은 멤버의 후보를 **고르지도 합치지도 않고 각각** 5단계로 보낸다"*(§3 「팬아웃」 불릿)이다. **바뀌는 것은 보내는 기준 하나다** — 「임계값을 넘은」이 「최종 신호가 `triage_review`인」이 된다(바로 아래 불릿). 그래서 위 표의 「TRIAGED(Auditor로)」는 슬라이스 단위가 아니라 review를 낸 좌석의 후보마다다 — 슬라이스(0010의 말로는 함수)당 하나로 합쳐 Auditor를 한 번 부르는 것은 0010이 기각한 방식 ②이고, 기각 사유는 *"Auditor가 받은 근거가 어느 멤버 것인지 섞여"* 공동오답 행렬이 흐려진다는 것이다. 대가가 하나 있다 — 한 좌석이 review면 다른 좌석의 NE는 루프를 안 돌아 미해소로 남고, 그 좌석의 공동오답 행렬(ADR 0010 §2 표 아래 · §3 — `scout_member`로 만든다) 칸은 맞힘도 놓침도 아닌 「NE 미해소」로 빈다.
- **판정은 점수 임계가 아니라 최종 신호로 한다.** v3는 「약한 review는 낮은 점수」를 허용한다(`scout_memory/v3/prefix.md:121` · `scout_boundary/v3/prefix.md:122`) — 임계 0.5로 가르면 그 review가 보류로 떨어진다[코드·프롬프트 대조 — run 없음]. (⏵ 2026-09-24 — v3 스모크 run이 생겼다. 그 run에서 이 방향은 0건이었다(review 12건이 전부 0.8). 반대 방향 — `triage_reject`인데 점수 1.0이라 승격 — 이 2건 나왔다. [D82](../open-questions/D/D82.md)의 「2026-09-24 실측」 ⑤다.)
- **Auditor·Critic의 NE는 2차다** — 같은 resolver를 쓴다.
- resolver의 세부(target 정규화 8단계 · 조회 우선순위 function > macro > type > local > global > field > unknown · 동명 점수 · SOURCE와 95% 이상 겹치면 재주입 안 함 · RETRIEVED 렌더 라벨)는 명세를 옮긴다. 이 저장소에 구현이 없으므로(`git grep -n -w triage_need_evidence -- src` 0건) 명세에서 짓거나 실험팀 코드를 옮긴다(Phase 5). (⏵ 2026-09-26 — 이 변경이 지었다: `src/adapters/symbol_resolver.py`·`symbol_render.py`. `git grep -n -w triage_need_evidence -- src`는 이제 0건이 아니다.)
- 루프는 후보를 만들기 **전에** 돈다 — 상태기계에 전이를 새로 넣지 않는다(제안 초안, Phase 5가 정한다). [D20](../open-questions/D/D20.md)의 잔여(Scout가 맥락을 더 받을 경로 — `expand()` 호출 지점 0)를 이 루프가 대신한다. (⏵ 2026-09-26 — 이 변경이 그대로 지켰다: `state_machine.py`의 C-3에 사유 행 둘(`SCOUT_REJECT`·`NEED_EVIDENCE_UNRESOLVED`)만 늘었고 새 from→to는 없다. D20은 이 변경으로 닫혔다 — D20 본문 참조.)

### 3. 3단계(Retriever·Embedder·Reranker)의 쓰임 — **열림**

팀 피드백(2026-09-23)이 추가 논의로 돌렸다 — 2026-09-26 대면(아래 「이 ADR이 열어 두는 것」 3). 쟁점은 resolver를 3단계 Retriever로 볼지, Embedder·Reranker를 어디에 쓸지(need_evidence 미해결 요청의 보충 · 동명 정의 재순위 · 사전 캡슐 · 안 씀)다. **그 논의를 기다리지 않고 정해 두는 것은 둘이다.**

1. **need_evidence는 결정론적 심볼 resolver로 푼다**(결정 2) — 임베딩·리랭크를 거치지 않는다.
2. **canonical 프로필은 합의 전까지 `retriever: hybrid`를 쓰지 않는다.**

**제안 초안** — Retriever = resolver. Embedder·Reranker = resolver가 못 푼 요청의 의미 검색 fallback이고, 측정 게이트(스모크의 미해결률) 뒤에만 켠다(Phase 10).

지금 코드에서 임베딩·리랭크가 닿는 곳은 캡슐의 `related` 칸이다 — EXPANDED·FOCUSED에만 있고 Scout의 LIGHT에는 없으며(`src/adapters/hybrid_retriever.py:108-112`) 러너가 부르는 것은 EXPANDED뿐이다(`runner.py:1180`). `retriever: hybrid` 프로필에서만 돈다. `prompts/_retrieval/`는 코드가 읽지 않는다(`src/`에서 `_retrieval/` 0건). 팀 재실험(Notion 「Embedder Reranker 재실험」, 2026-09-23 판독)이 고른 조합은 flow-GT macro R@5 0.729(무작위 0.369)였고 — 팀 보고, run ID 없음 — 11차 멘토링(2026-09-21)은 두 모델의 재설계가 필요하다고 했다.

### 4. 로스터 — 제안

**정본은 여전히 [PROJECT-CONTEXT.md](../../PROJECT-CONTEXT.md) §1이다** — 2026-09-21 회의의 교체판이고 main에는 2026-09-22 #240(`ce6a4c8`)으로 들어왔다. ~~아래는 그 표를 바꾸자는 제안이고, 바꾸는 절차는 그 파일 §3의 넷이다 — registry 키가 먼저 서야 한다(Phase 6).~~ → ⏵ **2026-09-24 — 같은 날 조민규가 아래 표의 로스터를 확정했고 §3의 넷으로 옮겼다** — registry `qwen3-14b` 등재 · 두 풀 프로필(`harness_pool`·`_hybrid`) · PROJECT-CONTEXT.md §1 · ADR 0010 §2(#266·#267). 그래서 §1 정본이 이제 이 표의 로스터다. ~~**이 ADR 문서의 상태(제안)는 그대로다** — 손수민 리뷰 전이고,~~ (⏵ 2026-09-25 — 손수민 구두 승인(조민규 전언)으로 이 문서도 승인됐다 — 머리 상태 줄.) 로스터 확정과 ADR 승인은 다른 일이다.

⏵ 2026-09-24 — 이 절(아래 표·목록)의 `config/models.yaml` 줄 번호는 `c301342` 판이다. 그 뒤 #262·#266·#267·#269가 파일을 바꿔 main에서는 다른 줄이거나 없는 줄이다(머리 문단의 수) — 키 이름으로 찾는다.

| 자리 | 제안 | 상태 | 근거 |
| --- | --- | --- | --- |
| 4 · `scout_memory` | **교체 가능** — 기본값 `seccoderx-8b`(`config/models.yaml:653`) | ~~결정 기한 2026-09-30~~ → 2026-09-24 확정(조민규) — `seccoderx-8b` | 실험팀 실험 중 |
| 4 · `scout_boundary` | **교체 가능** — 기본값 `seccoderx-8b` | ~~결정 기한 2026-09-30~~ → 2026-09-24 확정(조민규) — `seccoderx-8b` | 실험팀 실험 중 |
| 4 · `scout_injection` | ~~**Qwen3-14B 잠정** — registry에 `qwen3-14b` 키가 아직 없어(`config/`에서 0건) 새로 연다. 지금 값은 `deepseek-r1-distill-qwen-32b`(`:677`)~~ → **`qwen3-14b`** — 2026-09-24 registry에 열었고(#266) `deepseek-r1-distill-qwen-32b`는 지웠다(#267) | ~~잠정~~ → 2026-09-24 확정(조민규) | 팀 피드백 2026-09-23 — 모델 크기를 줄인다 |
| 5 · Auditor | `qwen3.6-35b-a3b`(`:700`) | 2026-09-21 교체로 선 자리(정본 PROJECT-CONTEXT.md §1 — 근거는 실험팀 2026-09-20 선정), 품질 미측정 | Notion 「Auditor·Critic 모델 최종 선정」(2026-09-23 판독) — 품질 근거는 5건 표본(팀 보고, run ID 없음) |
| 7 · Critic | `gpt-oss-20b`(`:419`) | 2026-09-09 판부터 같은 키이고(ADR 0010 §2) 2026-09-21 교체판에도 남았다(근거는 같은 2026-09-20 선정), 품질 미측정 | 같은 문서 |

- 새 키 `seccoderx-8b`·`deepseek-r1-distill-qwen-32b`·`qwen3.6-35b-a3b`는 `checksum`이 0이고(`config/models.yaml:664`·`:687`·`:715`) `source`에 `@rev`가 없다(`config/models.yaml:665`·`:688`·`:716`). `@rev`가 없으면 토크나이저 디렉터리를 못 정해 기본(글자수 근사) 카운터로 남는다(`src/adapters/tokenizer.py:119-122`). `gpt-oss-20b`·`qwen3-reranker-4b`는 두 칸이 다 있다(`config/models.yaml:512-513` · `:635-636`). `qwen3-14b`를 열면 같은 두 칸을 채워야 선다. (⏵ 2026-09-24 — `qwen3-14b`를 열었다(#266): `source`에 `@rev`가 있고 `checksum`은 0이다. `deepseek-r1-distill-qwen-32b`는 지웠다(#267). `seccoderx-8b`·`qwen3.6-35b-a3b`는 `checksum` 0 · `@rev` 없음 그대로다. ⏵ 2026-09-26 — `@rev`를 박았다: `seccoderx-8b@25d7f38…`·`qwen3.6-35b-a3b@995ad96…`(둘 다 [2026-09-24 스모크](../experiments/harness/2026-09-24-roster-smoke-atom.md)가 `--revision`으로 서빙한 리비전이고 HF 현재 main과 같다). 이제 `tokenizer_dir_from_source`가 스냅샷을 풀어 실 토크나이저 카운터로 뜬다(그 박스에 `tokenizer.json` 캐시·`tokenizers` 있으면 — 없으면 `file_absent`/`library_absent`로 사유가 남는다). `checksum`은 여전히 0이다.)
- ⏵ 2026-09-26 — `qwen3.6-35b-a3b`는 registry에서 지웠다(Auditor가 `qwen3.8-27b`로 바뀌었다). 새 키는 `source`에 처음부터 `@rev`가 있고(`@1d4bf0f…`, 2026-09-26 Hugging Face 직접 판독) `checksum`만 0이다.
- ~~Scout 좌석이 「교체 가능」인 것은 실험팀이 아직 고르는 중이라서다(결정 기한 2026-09-30).~~ → ⏵ 2026-09-24 — 조민규가 세 좌석을 확정했다(위 표). 그 실험도 원본 함수 입력이었다 — 결정 1의 두 번째 ⚠.
- ⏵ **2026-09-25 — `scout_memory`가 다시 움직였다.** 실험팀 최종 권고로 `seccoderx-8b`에서 `qwen3-14b`로 확정(조민규) — 파인튜닝 체크포인트가 오기 전까지 베이스 키. `scout_boundary`는 위 확정(`seccoderx-8b`) 그대로다. 정본은 PROJECT-CONTEXT.md §1.
- ⏵ **2026-09-26 — Auditor도 다시 움직였다.** 조민규 결정으로 `qwen3.6-35b-a3b`에서 `qwen3.8-27b`(dense 27.8B · GDN hybrid · VLM, bf16)로 바꾸고 추론(사고)을 켰다(`reasoning_effort: xhigh`). 근거는 벤더 모델 카드 주장뿐이다 `[출처주장]`(Qwen3.6-27B 대비 — 우리 35B-A3B와의 직접 비교 아님). 정본은 PROJECT-CONTEXT.md §1.

### 5. 저장소는 하네스 개발 전용이다 — 실험팀 실험의 원천은 팀 Notion

조민규 결정(2026-09-23). ⏵ 범위 좁힘 2026-09-26 — 바로 아래.

⏵ **2026-09-26 — 범위를 좁힌다: 하네스 실험·조사는 이 저장소에 남긴다.** 이 결정의 원문(아래 불릿)은 2026-09-24 **06:34**에 쓰였고(`a7deb68`), 같은 날 **17:29**에 [CLAUDE.md](../../CLAUDE.md) 머리 블록이 *「하네스 실험 보고서는 쓴다 — 하네스를 실서빙으로 돌린 스모크·서빙 실측은 하네스 결함(D·R)을 관리하는 근거라 `docs/experiments/`에 남긴다」*로 정정했다(`3b26564`). **그 정정이 원문과 보관 표시에 안 왔다** — [CLAUDE.md](../../CLAUDE.md) §2.4가 드는 「정본도 낡는다」의 형태다. 조민규 결정(2026-09-26)으로 둘을 맞추고 조사까지 넓힌다.

| 무엇 | 원천 |
| --- | --- |
| **하네스를 돌린 run 기록**(스모크·서빙 실측·기동 실측 — run ID·장비·프로필·commit을 든다) | **이 저장소 `docs/experiments/`** |
| **하네스 결정·결함의 근거가 되는 조사** | **이 저장소 `docs/research/`** |
| 실험팀 실험 — 모델 비교·선정, 조건(B1·B3 등)·벤치마크 설계, 데이터셋·정답지, 프롬프트 A/B | 팀 Notion |
| 슬라이서·인덱서의 **품질** 측정(recall·커버리지) | 팀 Notion |

**가르는 축은 「전처리냐」가 아니라 「하네스를 돌렸느냐」다** — 슬라이서는 이 저장소에서 개발하지만(위 결정 1 문단) 그 **품질**을 재는 실험은 실험팀 소관이다. 아래 「`PR·D 본문에`」는 그대로 유효하다 — 보고서를 쓰는 run이라도 ADR·D가 인용할 때는 그 세 필드를 같은 문장에 싣는다. 실험팀 수치를 인용할 때의 판독 날짜·「팀 보고, run ID 없음」 규칙도 그대로다. `STATUS.md`는 실험 기록이 아니라 현황 기록이라 보관 그대로다(현재 상태의 정본은 [PROJECT-CONTEXT.md](../../PROJECT-CONTEXT.md)다).

- **실험 기록의 원천은 팀 Notion 워크스페이스 [화햇대장](https://app.notion.com/p/3aebec1a15bc80c18c66fddd77844013)이다.** ~~새 실험 기록은 이 저장소로 들이지 않는다.~~ (⏵ 2026-09-26 — 이 불릿의 「실험 기록」은 실험팀 실험으로 좁혀졌고, 하네스를 돌린 run 기록은 `docs/experiments/`에 들인다 — 위 표.) 이 저장소가 드는 것은 하네스의 코드·설정·프롬프트, 하네스가 파일로 받는 입력을 만드는 오프라인 도구와 그 결함, 하네스 결정(ADR)·결함(D·R), 그리고 하네스 자신의 run 기록(run ID·장비·profile·commit — PR·D 본문에)이다. 그 도구는 `tools/slicer/`의 `slicer*.py`(⏵ 2026-10-03 — 루트에서 옮겼다)이고 이 저장소에서 계속 개발한다 — 2026-09-24 기준으로 열려 있는 슬라이서 PR #252·#255가 그 예이고, 테스트도 이 저장소의 `tests/test_slicer.py`·`tests/test_slicer_v2.py`에서 돈다.
- **하네스 결정의 근거가 된 실험은 ADR·D 본문에 요약하고 Notion 페이지를 링크한다.** 그 수치를 인용할 때는 출처(Notion 페이지)와 **판독 날짜**를 같은 문장에 적고, 그 페이지가 들고 있으면 **run ID · 실제로 서빙한 모델 · 실험이 끝난 시각**도 같은 문장에 옮긴다(아래 「잃는 것」 6). run ID가 없으면 **「팀 보고, run ID 없음」**을 붙인다 — Notion 페이지는 git 이력이 없어 [CLAUDE.md](../../CLAUDE.md) §2.4의 `git log -L`로 그 줄이 언제 쓰였는지 못 잰다. §0은 그대로다.
- **외부 문헌 조사도 같은 범위를 따른다.** ~~`docs/research/`는 실험 기록이 아니라 남이 주장한 것의 조사지만 하네스 개발 산출물도 아니다 — 새 조사는 Notion에 두고, 하네스 결정의 근거가 되면 실험과 같이 ADR·D 본문에 요약한다.~~ → ⏵ 2026-09-26 — 하네스 결정·결함의 근거가 되는 조사는 `docs/research/`에 쓴다(위 표). 그 밖의 조사는 Notion에 두고, 하네스 결정의 근거가 되면 ADR·D 본문에 요약한다.
- **~~보관 대상은 셋이다 — `docs/experiments/`·`docs/research/`·`STATUS.md`.~~ 옮기지 않고 제자리에 둔다.** (⏵ 2026-09-26 — 두 폴더의 보관은 풀렸고 보관은 `STATUS.md`만 남는다 — 위 표. 두 폴더 머리의 표시는 「보관이 풀렸다」로 바뀌었다. 두 폴더를 옮기지 않는 이유는 아래 링크 수 그대로다.) ~~각 머리에 보관 표시를 달고,~~ 안의 서술은 절마다 든 날짜의 기록으로 읽는다. 두 폴더를 옮기면 경계를 넘는 상대 링크 — 폴더 밖에서 안으로, 안에서 밖으로 — 433개(파일 대상 431 · 디렉터리 대상 2)가 깨지고, 그 링크를 든 추적 `.md` 99개에서 `test_internal_links_point_at_a_real_file`(파일마다 한 케이스)이 빨개진다. `c301342`에서 그 테스트의 `_LINK` 정규식으로 추적 `.md` 전수를 훑어 센 값이다 — 옮겨서 돌려 본 것이 아니다.
- **보관은 폐기가 아니다.** ADR·D·R 본문과 [CLAUDE.md](../../CLAUDE.md)가 근거로 드는 보고서·조사는 그 날짜의 기록으로 그대로 인용한다 — `docs/research/`만 해도 [ADR 0009](0009-stage-unit-execution-and-model-swap.md)·[ADR 0010](0010-harness-structure-and-model-roster.md), [D4](../open-questions/D/D4.md)·[D63](../open-questions/D/D63.md)·[D75](../open-questions/D/D75.md), [R8](../open-questions/R/R8.md)이 링크로 인용한다.
- **현재 상태의 정본은 [PROJECT-CONTEXT.md](../../PROJECT-CONTEXT.md)·[architecture.md](../architecture.md)·D·R 본문이다.** `STATUS.md`는 거기서 빠진다 — 절마다 든 날짜까지의 현황 기록이다.
- **운영 규약은 보관되지 않는다.** `docs/experiments/README.md`의 「서빙 서버 stdout을 파일로 받는다」 절은 실험 기록이 아니라 서버를 띄우는 사람의 규약이고 그대로 유효하다 — [model-serving.md](../model-serving.md)(`:325-328`)가 그 절을 규약의 정본으로 가리킨다.
- **비교표 규칙도 보관되지 않는다.** 정본은 [evaluation.md](../evaluation.md) §6(*"기록이 불완전한 run의 결과는 비교표에 넣지 않는다"*)과 §2.3(공정성 규칙)이고, 어느 필드가 불완전한지는 `incomplete_fields()`(`src/orchestrator/runner.py:1733`)가 경고한다. `STATUS.md` §8(「모델 비교표에서 제외되는 항목」)은 그 판정의 날짜 박힌 기록이다 — 그 절을 규약으로 가리키던 문장([D57](../open-questions/D/D57.md) 본문 등)과 코드 주석은 evaluation.md §6으로 읽는다(주석 정리는 Phase 11).

### 6. 재평가(requeue)·캡슐 확장·`previous` 경로를 폐기한다 — 모자란 맥락은 need_evidence 루프가 채운다

셋 다 `src/`에 생산자·호출처가 없다(2026-09-24, `c301342`에서 `git grep`).

| 경로 | 무엇이 죽어 있나 | 확인 |
| --- | --- | --- |
| **requeue** | `DecisionPoint.REQUEUE`(`src/orchestrator/policy.py:38`)를 내는 코드가 없다. 처리기 `_requeue`(`:200-214`)만 쓰는 전이 C-8(`queued`→`candidate`)·C-9(`queued`→`queued`)·F-14(`queued`→`hypothesized`)와 `requeue_max`(`config/budgets.yaml:50`)가 딸려 있다 | `git grep -n "DecisionPoint\.REQUEUE" -- src` → `policy.py:234`(처리기 등록표) 한 줄. 요청을 만드는 것은 `tests/test_policy.py`뿐이다 |
| **`Retriever.expand`** | 인터페이스 `src/interfaces/retriever.py:38`, 구현 `fake_retriever.py:137` · `hybrid_retriever.py:212`. 그것을 부르는 노드 `retrieval.expand()`(`src/orchestrator/nodes/retrieval.py:63`, 호출 `:88`)를 아무도 안 부른다. 그래서 전이 C-5(`triaged`→`queued`, `capsule_expansion_exhausted`)와 `capsule_expansion_max`(`config/budgets.yaml:46`)가 한 번도 안 걸린다 | `git grep -n "\.expand(" -- src` → 다섯 줄 중 호출은 `nodes/retrieval.py:88` 하나다(넷은 주석·docstring — `fake_retriever.py:156` · `hybrid_retriever.py:255` · `src/tokens.py:23`·`:27`). `git grep -n "retrieval\.expand(" -- src` → `src/tokens.py:27`의 docstring 한 줄. 러너의 `retrieval.` 호출은 `retrieval.run()` 둘(`runner.py:1144` LIGHT · `:1180` EXPANDED)이다 |
| **`previous`** | `scout.run(previous=…)`(`scout.py:71`). 그래서 `_queue_previous`(`:165-169`)가 늘 바로 돌아가고 전이 C-4(`candidate`→`queued`, `schema_parse_failed` — `:147`)와 C-7의 `candidate` 행(`:144`)이 도달 불가다 | 유일한 호출 `runner.py:1155-1161`이 `previous`를 안 넘긴다. `git grep -n "previous=" -- src` → `scout.py:90`(노드가 안쪽으로 넘기는 줄) 한 줄 |

전이 번호는 `src/orchestrator/state_machine.py`의 docstring 표(`:17-48`)와 규칙(`:125-131` · `:151`)이 정의한다. ⚠ C-7의 `triaged` 행(`:129`)은 Auditor가 쓴다(`src/orchestrator/nodes/auditor.py:114`) — 살아 있다.

- **폐기하는 것은 위 표의 세 경로 전부다.** `queued`에서 **돌아오는** 길(C-8·C-9·F-14, 그리고 그 길로 돌아온 후보에서만 서는 C-4·C-7의 `candidate` 행 — `scout.py`의 `previous`), 그리고 `queued`로 **들어가는** 캡슐 확장 길(C-5·`Retriever.expand`·`capsule_expansion_max`)이다. 탈락 후보를 `queued`로 **남기는** 쪽(C-3·C-6·C-7의 `triaged` 행·F-2 등)은 이 결정과 무관하다.
- **모자란 맥락을 채우는 자리**: need_evidence 루프(결정 2)가 **같은 세션 안에서, 후보를 만들기 전에** 채운다. run을 넘는 재평가는 하지 않는다 — 새 Evidence로 `queued` 후보를 다시 보는 설계는 대체 없이 빠진다(아래 「잃는 것」 5).
- **[D10](../open-questions/D/D10.md)은 질문이 소멸해 닫힌다**(ADR 0012 승인(2026-09-25) — 승인 커밋이 닫았다 · 아래 「결과」) — 보관할 재평가 횟수가 없다. (a)·(b)·(c) 중 하나에 답한 것이 아니다([ADR 0011](0011-retire-the-2x2-condition-axis.md)이 D1을 닫은 논법이다). ⚠ D10 본문 끝의 resume·스키마 버전 문제(구버전 `state.db`를 되읽을 버전 게이트가 없다 — `SqliteStateStore._load()`가 `model_validate`로 되읽는다, `src/adapters/sqlite_store.py:85-87`)는 재평가와 별개라 이 결정으로 사라지지 않는다 — [D86](../open-questions/D/D86.md)이 승계한다. (⏵ 2026-10-10 — 판을 적은 파일에 한해 게이트가 섰다(이슈 #281). 판 0 = 게이트 전 파일은 종전대로 열려 되읽기 실패가 그 파일에는 그대로다. 위 `sqlite_store.py:85-87`은 그때의 줄 앵커이고 지금은 게이트 자신의 줄이다.)
- **[D3](../open-questions/D/D3.md)의 확장 상한(`capsule_expansion_max`)은 `max_hops`로 대체한다** — D3을 닫는 것은 그 값을 배선하는 Phase 5 PR이다. (⏵ 2026-09-26 — 이 변경이 배선했다 — D3 닫힘.)
- **폐기는 「배선하지 않는다」는 결정이다.** 코드 삭제(열거형 값·전이 행·처리기 `_requeue`·`_capsule_expansion`(`policy.py:200-214` · `:122-131`)·노드 `retrieval.expand()`(`src/orchestrator/nodes/retrieval.py:63-90`)·인터페이스 메서드·`requeue_max`·`capsule_expansion_max`)는 이 PR(코드 0줄) 밖에서 따로 한다. **같은 PR이 그 경로를 앞으로 올 일처럼 서술하는 주석·docstring도 지운다**([CLAUDE.md](../../CLAUDE.md) §2.3) — `src/tokens.py:21-28`(*"배선하는 사람이 확장도 `using_counter(...)` 안에서 돌려야 한다"*), `src/orchestrator/nodes/scout.py:6-8`(`previous`), `src/interfaces/retriever.py:31-33`(확장 상한 판정), `state_machine.py` docstring 표의 해당 행.
- 이 결정과 어긋나는 서술 — [architecture.md](../architecture.md) §3 「Queue 규칙」의 재평가 절반, [knowledge-base.md](../knowledge-base.md) §5의 `expand()` 확장 사다리 — 은 이 결정을 따라 고칠 자리다. **고치는 쪽은 둘로 갈린다.** 「Queue 규칙」의 둘째 문장(새 Evidence로 재평가)은 대체 없이 빠지는 설계라 기다릴 코드가 없다 — 승인 커밋이 취소선을 그었다(2026-09-25). §5 사다리는 need_evidence 루프로 다시 그려야 하므로 그 루프를 짓는 Phase 5 PR이 코드와 함께 고치고, 그때까지는 그 자리의 ⚠ 노트가 범위를 짓는다([README](README.md)의 0012 문단 4번 (a)). (⏵ 2026-09-26 — 이 변경이 고쳤다 — knowledge-base.md §5.)

### 7. 파인튜닝 계획을 갱신한다 — Auditor·Critic 먼저, Scout 증류는 조건부

[build-plan.md](../build-plan.md) §6 끝 줄의 *"학습하지 않는 것: Auditor의 판정 · 리즈닝 트레이스 증류 · …"*(`c301342`의 `:260`, `1787c55` 2026-09-02)와 §6 표의 FT2(Scout)·FT3(Critic, GRPO) 행(`c301342`의 `:234-235`)을 대체한다.

- **팀 방향**(Notion 「Agent Fine-Tuning 팀 가이드」·11차 멘토링 2026-09-21·파인튜닝 회의 2026-09-22 — 팀 문서): Auditor = QLoRA SFT, Critic = 구조화 비평 SFT → ORPO, Scout = 프롬프트·하네스·임계값 먼저이고 필요하면 프론티어 teacher 증류. Evidence Gate는 학습하지 않는다.
- ⚠ **Auditor SFT가 무엇을 배우는지는 갈린다** — 가이드는 CPGQL 생성으로 적고, 11차 멘토링은 「하네스 Auditor는 CPGQL을 만들지도 받지도 않는다」를 확인했다(팀 문서). 이 ADR은 그것을 정하지 않는다 — 아래 「이 ADR이 열어 두는 것」 7.
- **하네스의 책임은 셋이다.**
  1. **학습과 서빙의 입력 형식이 같다** — 같은 렌더러(결정 1의 gutter·배너)를 쓴다. FT 데이터 풀은 함수 단위라(팀 보고 550,731건 — 위 「팀 방향」의 Notion 문서, 2026-09-23 판독, run ID 없음) 함수를 배너·gutter만 두른 「퇴화 슬라이스」로 렌더하는 길이 필요하다. 짓는 것은 Phase 8이고, 데이터셋팀의 FT 레코드 형식이 온 뒤에 짓는다.
  2. **모델 응답 원문을 남긴다** — 지금은 응답 본문·`reasoning_content`·`finish_reason`이 남지 않는다(⏵ 2026-09-24 — vLLM 0.27.1 응답에서 추론을 싣는 필드 이름은 `reasoning`이다, `reasoning_content`가 아니다 — [2026-09-12 토큰 회계 보고서](../experiments/harness/2026-09-12-critic-gptoss-token-accounting-atom.md) §4가 ATOM에서 확인했다. 남지 않는다는 것은 그대로다). `ModelResult`(`src/interfaces/model_client.py:187-221`)에 응답 본문 필드가 없고(`retry_bodies`는 보낸 요청이다) `src/adapters/openai_compat.py`에 두 이름이 0건이다 → [D84](../open-questions/D/D84.md). ⏵ 2026-10-08 — `finish_reason`은 이슈 #550으로 시도별 trace에 남는다(`ModelResult.attempt_log`) — 「두 이름이 0건」은 그쪽이 더는 서지 않는다. 「응답 본문 필드가 없고」도 2026-09-26(`raw_text` 추가, `6f5560c`)부터 서지 않았다 — 성공한 시도의 원문은 `ModelResult.raw_text`가 들고 `scout_hop` 이벤트와 후속 홉의 프롬프트 로그(입력 history)로 디스크에 닿는다([D84](../open-questions/D/D84.md)의 그 ⏵). **이 문장은 안 남는 것의 닫힌 목록이 아니다 — 아직 안 남는 것의 전체는 [D84](../open-questions/D/D84.md)가 정본이다**(추론 `reasoning`과 실패한 시도의 응답 원문은 그중 둘이다). ⏵ 2026-10-11 — **스키마 소진 또는 게이트웨이 거절로 끝난 호출의 시도별 응답 원문은 이제 남는다**(`prompts/…_response[_retryK].txt` · `model_call.response_refs`, 이슈 #603). **재시도가 성공한 호출의 앞 시도 원문과 성공 응답은 여전히 안 남는다**(위 `raw_text` 경로 밖). ⏵ 2026-10-12 — 앞 시도 원문은 이제 남고(#618), 성공 응답은 `run --keep-responses ROLE`을 준 역할만 남는다(#612). ⏵ 2026-10-13 — **D84는 닫혔고 더는 「아직 안 남는 것의 정본」이 아니다.** 추론은 이제 시도별로 남는다(`AttemptRecord.reasoning` · `prompts/…_reasoning[_retryK].txt` · `model_call.reasoning_refs` — 실패·버려진 시도는 항상, 성공 최종은 `--keep-responses ROLE`을 준 역할만). 기본 run의 성공 호출 원문·추론은 여전히 안 남는다. 내보내기 형식과 남은 몫은 [D109](../open-questions/D/D109.md)가 든다.
  3. **역할별로 내보낸다(export)** — 지금 CLI 서브커맨드는 `doctor`·`estimate`·`run`·`bench`·`gc`뿐이다(`src/cli.py:860-889`).

## 결과

**이 PR이 여는 것 · 승인 커밋이 닫는 것.**

- [D10](../open-questions/D/D10.md) — 결정 6이 이 질문의 대상을 없앤다. **ADR 0012 승인(2026-09-25) — 승인 커밋이 닫았다**(아래 ⚠). 본문 끝의 resume·스키마 버전 문제는 재평가와 별개라 [D86](../open-questions/D/D86.md)으로 옮긴다.
- [D12](../open-questions/D/D12.md) — 선택지 (a): [architecture.md](../architecture.md) §3을 코드에 맞춘다(이 PR). build는 2단계 신호 도구다(`src/orchestrator/nodes/signals.py:26`의 `SIGNAL_TOOLS`). 이 ADR의 결정이 아니라 같은 PR의 정본 정렬이다. **ADR 0012 승인(2026-09-25) — 승인 커밋이 닫았다**(아래 ⚠).
- [D82](../open-questions/D/D82.md)·[D83](../open-questions/D/D83.md)·[D84](../open-questions/D/D84.md) 등재 — Scout v3 계약과 하네스 입력·스키마의 불일치(결정 1·2) · 모델이 쓴 `state`·ID를 대조하지 않아 run이 중단될 수 있음(코드 추론, 관측 없음 — ⏵ 2026-09-29 D83은 선택지 (가)로 고쳐 닫았다: 하네스가 발급 ID·`run_id`·`artifact_id`·초기 `state`를 도장 찍는다. PR [#333](https://github.com/0xM1yu/whitehat-boss/pull/333). 도장 밖에 둔 `location` 누출은 D82 닫히는 조건 ⑥으로 옮겼다) · 모델 응답 원문·`reasoning_content`·`finish_reason` 미보존(결정 7 — ⏵ 2026-10-11 `finish_reason`은 #550으로, 스키마 소진·게이트웨이 거절 호출의 시도별 원문은 #603으로 남는다. 재시도가 성공한 호출의 앞 시도 원문·`reasoning`은 여전히 안 남고, 성공 응답은 위 `raw_text` 경로 밖에서는 안 남는다 — ⏵ 2026-10-12 앞 시도 원문은 #618로, 성공 응답은 옵트인 `--keep-responses`(#612)로 남는다). ⏵ 2026-10-13 — D84는 닫혔다(닫는 조건을 (요청, 원 응답, `finish_reason`)으로 좁혔다). `reasoning`은 이제 시도별로 남고(위 결정 7의 같은 날 ⏵), 내보내기 형식과 잔여는 [D109](../open-questions/D/D109.md)다.
- [D86](../open-questions/D/D86.md) 등재 — D10에서 승계한 resume·스키마 버전 잔여(구버전 `state.db`를 되읽는 버전 게이트가 없다 — ⏵ 2026-10-10 판을 적은 파일에 한해 게이트가 섰다, 게이트 전 파일(판 0)은 열린다, 이슈 #281).

⚠ **#260은 「제안」인 채 머지해 아무것도 닫지 않았다**(sumin 리뷰 M1, 2026-09-24). 첫 판은 D10·D12를 닫고 `Closes #60`·`#62`를 걸어 둔 채 머지 순서(승인 전 머지 금지) 하나로 그것을 받쳤다 — 순서가 어긋나면 `Closes`가 이슈를 먼저 닫는다([ADR 0010](0010-harness-structure-and-model-roster.md) 「2026-09-08 갱신」 ④가 같은 이유로 D9를 미뤘다). 그래서 둘을 `열림`·`Refs`로 두었고, ADR 0012 승인(2026-09-25) — 승인 커밋(PR #275)이 닫힘과 `Closes #60`·`#62`를 발화했다. 그 커밋이 고친 자리는 [README](README.md)의 0012 문단이 적는다.

**후속 — Phase 번호.** 2026-09-24부터 이 저장소의 하네스 문서(ADR·정본·설계 문서·D·R 본문)가 「Phase N」으로 가리키는 번호는 이 목록이 정의한다. 그 전에 쓰인 실험 보고서·프롬프트 메타의 「Phase」는 다른 뜻이다(예: `docs/experiments/harness/2026-08-18-retrieval-atom.md`의 「실험 1 — … (Phase 1)」, `prompts/_retrieval/`의 `meta.yaml`). 순서·담당은 이 저장소가 들지 않는다 — 번호는 식별자이지 순서가 아니다(Phase 2가 Phase 1보다 먼저 끝났다).

- Phase 1 — 정본 리셋: 이 PR(문서만, 코드 0줄).
- Phase 2 — 열린 PR 정리: 끝났다(2026-09-24 — `c301342`까지. 슬라이서 PR #252·#255는 따로 간다).
- Phase 3 — 슬라이스를 Scout 입력으로: 결정 1(과 1-b가 정하는 Auditor 입력), D78 ①②③, D82의 입력 쪽.
- Phase 4 — tree-sitter 심볼 인덱스 v2(index-v2): 결정 1의 심볼 인덱스, [D13](../open-questions/D/D13.md)의 데이터원.
- Phase 5 — need_evidence resolver·루프: 결정 2, 결정 6의 `max_hops`, D82의 스키마 쪽, D20·D3을 닫는 자리. knowledge-base.md §5의 확장 사다리를 이 루프로 다시 그린다(결정 6 끝 불릿).
- Phase 6 — 로스터·프로필·프롬프트 배선과 스모크 전 방탄: 결정 4(`qwen3-14b` 신설 · 새 키의 `@rev`·checksum), 결정 3의 canonical 프로필, 그리고 로스터와 무관하게 스모크 전에 막아야 하는 D83(모델이 쓴 `state`·ID를 하네스가 대조하지 않는다 — 처리는 D83의 선택지 · ⏵ 2026-09-29 — (가)로 고쳤다: 하네스가 발급 ID·`run_id`·`artifact_id`·초기 `state`를 도장 찍는다, PR [#333](https://github.com/0xM1yu/whitehat-boss/pull/333)). ⚠ 결정 1의 「슬라이스 없이 v3 Scout 프롬프트를 부르는 run은 시작 전에 거부」가 Phase 3에서 들어가면 canonical 프로필은 그때부터 슬라이스 입력이 있어야 시작한다 — 이 Phase의 프로필 배선이 그 상태를 이어받아 푼다(sumin 리뷰 L2). (⏵ 2026-09-25 — 들어갔다: `harness_pool`·`_hybrid`로 모델에 닿는 run은 이제 `--slices` 없이 시작하지 않는다 — 결정 1의 같은 날 노트. `SOURCE` 렌더는 별도 PR이다.)
- Phase 6.5 — GPU 배선: 단계 단위 콜드 재기동 교대([ADR 0009](0009-stage-unit-execution-and-model-swap.md)) · 모델별 요청 설정(사고·샘플링·타임아웃) · 단계 안 동시 요청 · 기동 조건을 장비별 데이터로. 방식은 2026-09-24 조민규가 채택했다([PROJECT-CONTEXT.md](../../PROJECT-CONTEXT.md) §5 「모델 배선」 행). ⚠ **2026-09-25 추가 — 이 ADR의 승인(같은 날) 뒤에 덧붙인 번호라 손수민 승인 범위 밖이다.** 6과 7 사이 번호는 식별자일 뿐이다(위 — 순서가 아니다).
- Phase 7 — 평가 어댑터·채점기·배치 실행. **검증 벤치마크 = CyberGym Level 1**(조민규 결정, 2026-09-30 — 채팅으로 정했고 원천 링크는 저장소에 없다). 어느 벤치마크로 검증하느냐를 이 결정이 정했다. 결정 5의 「벤치마크 설계」(데이터셋·정답지·조건)는 그대로 실험팀 몫이다 — 고른 벤치마크가 무엇이든 그 안의 설계는 이 저장소가 정하지 않는다. ⚠ **2026-09-30 추가 — 이 ADR 승인(2026-09-25) 뒤에 박은 결정이라 손수민 승인 범위 밖이다**(Phase 6.5와 같은 자리) — 정본 확정은 sumin 리뷰 뒤다.
- Phase 8 — 모델 원문 기록·내보내기: 결정 7, D84. ⏵ 2026-10-13 — D84는 닫혔다. 기록(추론 포함)은 시도별로 들어왔고, 내보내기 형식과 잔여는 [D109](../open-questions/D/D109.md)다.
- Phase 9 — GPU 스모크(GPU 서버에서 팀원이 실행 — 에이전트가 돌리지 않는다).
- Phase 10 — 검색 fallback: 결정 3의 제안 초안. 결정 3의 합의가 이 용도를 고르고, Phase 9 스모크의 need_evidence 미해결률이 팀이 정한 임계를 넘을 때만 착수한다.
- Phase 11 — 결함 소탕 배치: 낡은 코드 주석 정리를 포함한다(결정 6의 코드 삭제와는 별개).

**얻는 것.** 정본이 한 방향을 가리킨다 — 슬라이스가 모델이 볼 코드이자 후보 단위다. 프롬프트가 약속한 입력을 하네스가 준다. Scout가 부족한 맥락을 되물을 길이 처음 생기고, 죽은 경로 셋이 정본에서 빠진다.

**잃는 것.**

1. **슬라이서의 기여를 따로 재는 설계** — D74가 (a)를 고른 근거 ①이다. 후보 집합과 모델 입력이 한 번에 바뀌므로 전후 차이는 원인을 못 가른다. 이 구성으로 낸 결론에는 그 교락을 같은 문장에 적는다.
2. **CPG가 못 본 줄은 모델도 못 본다** — D74 근거 ③. 되받는 길은 원본 파일을 읽는 resolver(결정 2)뿐이고, 이 결정이 약속하는 것은 그 한계를 기록에 남기는 데까지다(결정 1의 `status`). 슬라이서가 실패하면(`status: failed` — `slicer.py:426`) 입력이 없다 — 2026-09-24 등재 시점의 [D85](../open-questions/D/D85.md)(UTF-8 아닌 파일 하나에 run 전체 실패, 수정 PR #255 열림)가 그 한 사례다.
3. **Scout 좌석 모델의 품질 근거가 원본 함수 입력이다**(팀 보고, run ID 없음) — 슬라이스 입력의 품질은 미측정이다.
4. **Auditor와 Scout가 다른 코드를 볼 수 있다** — 1-b가 (i)로 가면. (⏵ 2026-09-25 — (i)로 갔다. Scout는 슬라이스 렌더를, Auditor는 슬라이스가 걸친 함수들의 원본을 본다 — 상한에 빠진 함수와 함수 밖 줄은 Auditor가 못 본다. trace `auditor_source`가 센다.)
5. **새 Evidence로 `queued` 후보를 다시 보는 설계가 대체 없이 빠진다** — [architecture.md](../architecture.md) §3 「Queue 규칙」의 둘째 문장(*"새 Evidence(신규 crash, 신규 정적분석 룰)가 들어오면 해당 후보만 재평가한다"*)이다. 배선된 적은 없지만(생산자 0 — 결정 6 표) 설계에서 빠진다. need_evidence 루프는 같은 run 안에서 모델이 요청한 맥락을 채우는 길이지(결정 2), run 밖에서 들어온 새 근거로 다시 보는 길이 아니다. 다시 필요해지면 새 ADR이다.
6. **실험 기록이 저장소 밖으로 나간다**(결정 5). `docs/experiments/`가 막으려던 어긋남을 저장소의 검사와 PR 리뷰가 다시 못 잡는다 — 그 폴더가 생긴 계기가 2026-08-18에 메신저 첨부로만 있던 ATOM 보고서 두 건이 다섯 군데 갈린 일이고, 그중 셋은 어느 모델을 실제로 서빙했는지·실험이 언제 끝났는지가 양쪽에 없어 생긴 오독이었다([그 README](../experiments/README.md) 머리). 줄이는 수단은 ADR·D 요약이 그 두 필드와 run ID를 싣는 것이다(결정 5의 인용 규칙). 그리고 Notion 페이지는 git 이력이 없어 줄이 언제 쓰였는지를 `git log -L`로 못 잰다 — 그래서 결정 5가 판독 날짜를 같은 문장에 적게 한다. (⏵ 2026-09-26 — **이 항목의 대부분이 없어졌다**: 하네스를 돌린 run 기록과 하네스 조사는 저장소에 남는다(결정 5의 ⏵ 표). 남는 것은 실험팀 실험 쪽이고, 줄이는 수단은 그 인용 규칙 그대로다.)

**되돌리는 비용.** 지금은 문서뿐이다 — 이 결정의 코드는 한 줄도 없다(아래 「검증」). Phase 3 뒤에도 슬라이스를 안 쓰는 경로가 그대로 남도록 짓는 것이 결정 1의 조건이라, 되돌리기가 프로필 선택이 된다. (⏵ 2026-09-25 — 결정 1의 입력 쪽 코드가 들어왔다: Phase 3a(PR #283) · 3b(PR #284) · 3c. 슬라이스를 안 쓰는 경로는 그대로 남았다 — `run`에 `--slices`를 안 주면 옛 경로라, 지금 되돌리기는 그 플래그를 빼는 것이다.) 결정 6은 코드 삭제 PR 전까지 되살리는 비용이 지금과 같다 — 생산자가 원래 없다.

## 이 ADR이 열어 두는 것

| # | 무엇 | 결정권 | 기한 | 그때까지의 기본값 |
| --- | --- | --- | --- | --- |
| 1-b | Auditor 입력 표현 | 손수민 리뷰 | 2026-09-26 | (i) 원본 소스. ⏵ 2026-09-25 — 정해졌다: 넓은 (i) + 크기 상한(팀 합의 — 위 1-b) |
| 3 | 3단계의 쓰임 | 팀(손수민 리뷰) | 2026-09-26 대면 | resolver만 · `retriever: hybrid` 끔 |
| 4 | Scout 세 좌석 모델 | 실험팀 → PM | 2026-09-30 | registry 지금 값 — injection은 `qwen3-14b`를 연 뒤 그 키. ⏵ 2026-09-24 — 조민규가 확정했다: `seccoderx-8b`·`seccoderx-8b`·`qwen3-14b`(PROJECT-CONTEXT.md §1) ⏵ 2026-09-25 — memory가 다시 바뀌었다: `qwen3-14b`·`seccoderx-8b`·`qwen3-14b`(memory·boundary·injection 순 — 실험팀 최종 권고, 조민규. PROJECT-CONTEXT.md §1) |
| 7 | Auditor SFT가 배울 것 | 팀 | 2026-10-04 전 | 벤치마크의 Auditor는 Finding 검증자다 |

## 대안

- **(A) council 권고(2026-09-23) — tree-sitter를 유지하고 어댑터를 둔다.** 기각: 조민규가 같은 날 반대로 정했다. council은 세션 안의 다관점 검토이고 기록은 저장소에 없다.
- **(B) D74 (a)를 지킨다 — 슬라이스는 후보 열거에만.** 기각: 풀 프로필이 부르는 Scout v3가 슬라이스 입력을 전제하므로(`config/models.yaml:667`·`:690` → `prompts/scout_*/v3`) (a)를 지키려면 프롬프트를 되돌려야 하고, 그때까지 불일치(D82)가 돈다. #196의 분업(PROJECT-CONTEXT.md §2 「tree-sitter를 왜 계속 두는가」 — 위 「왜」 표)과도 반대다.
- **(C) D74 (b) — 슬라이스를 `related` 칸 하나로 싣는다.** 기각: 대상 원문과 겹쳐 실려 토큰이 붓고([D74](../open-questions/D/D74.md) 선택지 (b)), `SOURCE`가 슬라이스라는 v3 선언과는 여전히 어긋난다.
- **(D) need_evidence를 `Retriever.expand`로 푼다 — 죽은 확장 경로를 살린다.** 기각: `MissingEvidence`는 `finding_id`가 필수라(`src/schemas/finding.py:48`) Finding이 없는 후보 단계의 요청을 못 담고, 두 구현은 `need.missing_id`만 본다(`fake_retriever.py:174` · `hybrid_retriever.py:296`).
- **(E) requeue를 배선한다 — D10의 (a)·(b).** 기각: 생산자가 없고, 맥락 부족은 NE 루프가 같은 run 안에서 푼다. run을 넘는 재평가는 횟수 보관(D10 (a)·(b))을 끌고 온다 — 소비자 없는 비용이다. ⚠ D10 본문 끝의 resume 스키마 버전 문제는 재평가의 부속이 아니다 — 재평가를 버려도 resume를 설계하는 날 다시 선다. 그래서 D10을 닫으며 그 문단을 [D86](../open-questions/D/D86.md)으로 옮긴다(결정 6).
- **(F) 심볼 인덱스를 Joern CPG에서 뽑는다.** 기각(2026-09-24 조민규 결정): tree-sitter를 유지하고 ATOM 실행에서 실제로 부족할 때 — 예컨대 need_evidence 미해결이 타입·대입 쪽으로 몰릴 때(Phase 9 스모크 뒤) — 다시 본다. 실험팀 「Scout RAG baseline」도 심볼 인덱스를 tree-sitter로 만든다 — 매크로가 CPG에 안 들어가고 빨리 만들 수 있어서다(팀 문서).

## 검증

**결정 6만 지금 참인 불변식이고, 코드 조건이 있는 나머지는 전부 지금 미충족이다** — 이 ADR은 코드가 따라갈 정본이지 코드의 서술이 아니다. (⏵ 2026-09-25 — 결정 1의 네 조건은 Phase 3a·3b·3c 뒤 선다 — 아래 표 결정 1 행의 ⏵. 1-b도 같은 날 선다 — 그 행의 ⏵. 나머지는 이 문장 그대로다. ⏵ 2026-09-26 — 결정 2의 네 조건도 이 변경 뒤 선다 — 아래 표 결정 2 행의 ⏵. 나머지(결정 3·4·5·7)는 이 문장 그대로다.) 각 Phase의 PR은 자기 줄을 테스트로 잠그고, 배선을 되돌리면 빨개지는지 확인한다([CLAUDE.md](../../CLAUDE.md) §2.5).

| 결정 | 참이 되는 조건 | 2026-09-24 (`c301342`) |
| --- | --- | --- |
| 1 | `git grep -n slice_criterion -- src` ≥ 1이고 `FACT_KEYS`(`src/orchestrator/prompt_loader.py:56-65`)에 그 키가 있다 · `Candidate.slice_id`가 있다 · `src/`가 `slicer*.py`를 import하지 않는다 · 슬라이스에서 만든 `Evidence`가 없다 | 미충족 — `git grep -n -i slice -- src config` 0건. ⏵ 2026-09-25 — 네 조건이 Phase 3a(PR #283) · 3b(PR #284) · 3c 뒤 선다: `git grep -n slice_criterion -- src`가 비지 않고 `FACT_KEYS`에 그 키가 opt-in으로 있다(슬라이스 타깃에만 실린다) · `Candidate.slice_id`(3b) · `src/`의 `slicer` import 0건 · 슬라이스로 만드는 `Evidence` 0건(`Evidence(`를 만드는 `src/` 파일 어디에도 슬라이스가 안 들어간다). 결정 1의 다른 불릿 — 슬라이스 없이 v3 Scout 프롬프트를 부르는 run의 시작 전 거부 — 은 이 칸의 조건 밖이고 세 PR에 없다(⏵ 같은 날 3d가 3c 위에 넣었다 — 위 결정 1 불릿의 노트). ⏵ 같은 날 #288이 마지막 조건을 출처 테스트로 잠갔다 — 게이트가 받은 Evidence ⊆ 도구 러너 반환분(`test_the_gate_sees_only_evidence_the_tool_runner_returned`) |
| 1-b | 고른 표현과 Auditor 프롬프트의 `representation` 선언이 같다 | 결정 전. ⏵ 2026-09-25 — **충족**: 넓은 (i)을 골랐고 디스크의 Auditor 프롬프트 전부(**판 목록을 여기 적지 않는다** — 그 검사가 `prompts/auditor/`를 `iterdir()`로 돌아 **디스크에 있는 판 전부**를 자동으로 잠근다. ⏵ 2026-09-29 v4 · ⏵ 2026-10-03 v6까지 그대로 적용됐다)의 입력 선언(`meta.yaml`의 `input`, 없으면 `source`)이 `source`다 — `test_every_auditor_prompt_declares_the_original_source_the_runner_gives_it`가 잠근다(`input: slice`로 바꾸면 빨개진다) |
| 2 | `requested_context`가 네 키 객체다 · `src/`가 `triage_need_evidence`를 읽는다 · `max_hops`가 설정에 있다 · 홉마다 trace 이벤트가 남는다 | 미충족 — `list[NonEmptyStr]`(`src/schemas/scout_response.py:46`) · 0건 · 0건 (⏵ 2026-09-26 — PR #302가 앞 셋의 자리를 채운다: `RequestedContext` 객체 · `git grep -n -w triage_need_evidence -- src` ≥ 1(`ScoutSignal.NEED_EVIDENCE`) · `git grep -n max_hops -- config` ≥ 1. 홉마다 나는 trace 이벤트는 그대로 0건이다 — 루프는 Phase 5다) (⏵ 2026-09-26 — 홉마다 나는 trace 이벤트도 이 변경이 채운다: `EventKind.SCOUT_HOP`(`src/orchestrator/trace.py`), hop0을 포함해 신호가 있는(v3) 좌석마다 `_emit_scout_hop`(`src/orchestrator/runner.py`)이 낸다. **결정 2의 네 조건이 모두 찼다.**) |
| 3 | canonical 프로필의 `retriever`가 `hybrid`가 아니다 | canonical 프로필이 아직 없다(Phase 6) |
| 4 | registry에 `qwen3-14b`가 있다 · 새 키에 `@rev`와 실 checksum이 있다 | 미충족 — 0건 · `config/models.yaml:664-665`·`:687-688`·`:715-716` |
| 5 | 보관 셋의 머리에 보관 표시가 있다 | 문서로만 선다 — 같은 PR이 단다. 코드 조건이 없다 |
| 6 | `src/`에 `DecisionPoint.REQUEUE`를 내는 코드가 0 · 러너의 `retrieval.expand(` 호출이 0 · `scout.run`에 `previous`를 넘기는 호출이 0 | **충족** — 위 결정 6 표. 코드 삭제 뒤에는 그 이름들이 `src/`에서 0건이 된다 |
| 7 | `ModelResult`에 응답 원문 필드가 있다 · `export` 서브커맨드가 있다 | 미충족 — `src/interfaces/model_client.py:187-221` · `src/cli.py:860-889` ⏵ 2026-10-08 — 앞 절은 `raw_text`(2026-09-26, 성공한 시도의 원문)로 성립했다. `export`가 없어 이 행은 여전히 미충족이다 |
