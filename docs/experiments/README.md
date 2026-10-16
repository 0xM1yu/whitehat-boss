# 실험 기록

> ⏵ **2026-09-26 — 보관이 풀렸다**([ADR 0012](../decisions/0012-harness-canon-v2.md) 결정 5의 ⏵).
> **하네스를 돌린 run 기록은 여기 쓴다** — 스모크·서빙 실측·기동 실측이고, run ID·장비·프로필·commit을 든다.
> **실험팀 실험은 여기 안 쓴다** — 모델 비교·선정, 조건·벤치마크 설계, 데이터셋·정답지, 프롬프트 A/B,
> 그리고 슬라이서·인덱서의 품질 측정은 팀 Notion이 원천이고, 하네스 결정의 근거가 되면 ADR·D 본문에
> 요약과 링크를 둔다(그 결정의 인용 규칙).
> **2026-09-24~26 사이 이 머리가 「보관」이었다** — 그 기간에도 아래 첫 문장의 규약은 살아 있었다
> ([CLAUDE.md](../../CLAUDE.md) 머리 블록이 2026-09-24 17:29에 그렇게 정정했다).
> 현재 상태의 정본은 [PROJECT-CONTEXT.md](../../PROJECT-CONTEXT.md)·[architecture.md](../architecture.md)·D·R 본문이다.

**실험 보고서를 저장소 안에 둔다.** 밖에 있으면 서로 어긋난 것을 아무도 못 잡는다.

이 폴더가 생긴 계기가 정확히 그것이다. 2026-08-18에 ATOM 실험 보고서가 두 건 나왔는데
둘 다 메신저 첨부로만 존재했고, 대조해 보니 **다섯 군데가 갈려 있었다.** 그중 셋은 실제
모순이 아니라 **양쪽 보고서에 같은 필드가 없어서 생긴 오독**이었다 — 어느 모델을 실제로
서빙했는지, 실험이 몇 시에 끝났는지가 어느 쪽에도 없었다.

## 이 폴더의 구성

| 파일 | 역할 |
| --- | --- |
| `README.md` (이 문서) | 색인 · **확정된 사실** · **미확정 항목** |
| [`TEMPLATE.md`](TEMPLATE.md) | 보고서 서식. 각 필드에 "없으면 무엇을 못 하는지"가 붙어 있다 |
| [`harness/`](harness/) | **하네스가 도는가**를 본 run. `YYYY-MM-DD-<주제>-<장비>.md` |
| [`preprocessing/`](preprocessing/) | **전처리 형식(source/ast/cfg/pdg/cpg)이 탐지 정확도를 얼마나 바꾸는가**를 본 Juliet 실험. 하네스를 거치지 않은 별도 측정이라 파일명 규칙도 `TEMPLATE.md`도 따르지 않는다. 아래 §전처리 실험 참조 |

**한때 `2x2/`가 옆에 있었다.** 팀 2×2 매트릭스의 셀 설정과 셀별 보고서를 담을 자리였고,
`harness/`와 가른 이유는 성격이 달라서였다 — 이쪽은 배선이 붙었는지를 보는 run이라 결함이
나오면 그게 소득이고, 저쪽은 그 하네스를 도구로 쓴 측정이라 하네스가 흔들리면 셀이 무효가
된다. **트랙은 2026-09-01에 폐기됐고**(`config/budgets.yaml`의 `output_max` 주석이 그 날짜로
기록한다) **2026-09-04에 디렉터리를 걷어냈다** — Scout 오케스트레이터로 간다. 그 트랙 이름을
단 보고서 한 건이 `harness/` 아래에 남아 있다 —
[2026-08-28 2×2 B1′](harness/2026-08-28-2x2-b1prime-qwen3-8b-atom.md). **그 보고서 자신이
매트릭스 run이 아니라고 적는다** — 머리말이 *"조건 비교의 입력이 아니다"*라고 못박는다.

**색인은 여기 하나다. 다만 대조가 강제되는 범위는 `harness/`뿐이다.**
`test_every_experiment_report_is_in_the_index`가 `experiments/` 기준 상대경로로 대조하는 것은
맞지만, 대조할 파일 목록을 [`tests/test_docs_format.py`](../../tests/test_docs_format.py)의
`REPORT_DIRS`에서 뽑고 **그 튜플의 원소는 `harness/` 하나다.**

> **⚠ 그래서 "어느 폴더에 있든 걸린다"는 이 문서가 오래 적어 온 대로 성립하지 않았다.**
> `preprocessing/` 여섯 건이 2026-08-29에 들어왔는데(커밋 `4025695`·`b790270`) **색인에도
> 위 구성 표에도 없었고**, 저장소의 어떤 `*.md`도 그 폴더를 가리키지 않았다 —
> `git grep -n preprocessing -- '*.md'`가 그 폴더 밖에서 0건이다. 검사가 못 잡은 것은 버그가
> 아니라 범위 밖이어서다. **보고서 폴더를 늘릴 때 `REPORT_DIRS`를 같이 고쳐야 대조가
> 따라온다**(그 튜플 위 주석이 그렇게 적어 뒀다).

**확정된 사실은 이 README에만 있다.** 보고서는 그 시점의 관측이고, 이후 대조로 정정된
것은 여기 표가 담는다. 사실이 두 곳에 살면 또 갈린다 — 이 폴더가 존재하는 이유가 그것이다.

## 색인

| 날짜 | 보고서 | 주제 | 장비 | 작성 |
| --- | --- | --- | --- | --- |
| 2026-08-18 | [서빙 · Docker · GPU 회계](harness/2026-08-18-serving-atom.md) | vLLM 프로브, aarch64 이미지, fp8 기동, util 기준, 첫 실모델 완주, GPU-second 회계, ARM64 회귀 | `atom` | @danielcho02 |
| 2026-08-18 | [검색 경로 실서빙](harness/2026-08-18-retrieval-atom.md) | 하네스↔실모델 배선, embedding·reranker 실서빙, HybridRetriever 실경로 완주 | `atom` | @sumin0218 |
| 2026-08-22 | [파이프라인 실서빙 완주](harness/2026-08-22-pipeline-atom.md) | scout 실모델 완주, 캡슐 그래프 섹션·게이트 등급 관측, `cli run` 경로의 prefix cache 장부 | `atom` | @danielcho02 |
| 2026-08-23 | [심볼 단위 후보로 zlib 완주](harness/2026-08-23-pipeline-zlib-atom.md) | W2 심볼 target 실저장소 검증, (a′) 정렬, `candidate_max` 절단 기록, 7단계 선별 사슬(D26) | `atom` | @danielcho02 |
| 2026-08-25 | [심볼 542건 전량 완주](harness/2026-08-25-pipeline-zlib542-atom.md) | `candidate_max` 전량 실행, 후보당 비용 실측, D26 원인 재규정, Scout 점수 분포 | `atom` | @danielcho02 |
| 2026-08-25 | [NVFP4 실서빙 검증](harness/2026-08-25-serving-nvfp4-atom.md) | NVFP4 2종 sm_121 기동, MoE 구조화 출력, 30B bench, D21 4-1 첫 실현, R6 대체 경로 | `atom` | @danielcho02 |
| 2026-08-26 | [배선 수정 후 재측정](harness/2026-08-26-pipeline-zlib542-remeasure-atom.md) | 8단계 `gate_eval` 첫 8건(D26 닫힘), 원인은 build `-I` 누락, `critic_skipped` 첫 관측, 실 토크나이저 무동작 판정 | `atom` | @danielcho02 |
| 2026-08-28 | [track_a PWN-003 첫 완주](harness/2026-08-28-pipeline-track-a-pwn003-atom.md) | b1_single 첫 완주, track_a 데이터셋 첫 투입, 정답 대조 불합격, 공백 폭주와 xgrammar 해소, 30B `util 0.28` 실측 | `atom` | @danielcho02 |
| 2026-08-28 | [2×2 B1′ 첫 완주 (PR #36 검증)](harness/2026-08-28-2x2-b1prime-qwen3-8b-atom.md) | PR #36 프로필 4/4 doctor 통과, 8B bf16이 KV cache 13.82 GiB로 10분 03초 완주, `output_max` 인과는 미성립(30B가 그 키 없이 완주), 정답 대조 불합격 | `atom` | @danielcho02 |
| 2026-08-28 | [run A 하네스 검증](harness/2026-08-28-runA-harness-validation-atom.md) | D44·D45 성립 확인, `output_max 8192`의 피해 셋(폭주 미차단·gold 함수 400 탈락·실패 시간), gold 3함수 중 2건이 Finding, 재시도가 응답 원문을 되싣는 결함(D47) | `atom` | @danielcho02 |
| 2026-08-28 | [토크나이저 정확도 Prove](harness/2026-08-28-tokenizer-prove-atom.md) | 실 토크나이저 vs 서버 `input_tokens` **오차 0**(+13 상수)·옛 `len//4` ≈1.6x 과소(D21 4단계 Prove), `run_20260827_0004` 재분석, `critic_top_n` 의제 | `atom` | @sumin0218 |
| 2026-08-29 | [프롬프트 v2 스모크와 v1 대조](harness/2026-08-29-prompt-v2-smoke-atom.md) | D52 닫힘(v1 `null` ×6 대 v2 `triggerable` ×6 + 프로브 음성 대조), 7단계 critic 첫 진입(v1은 Finding 6건 전부 `E0`), 폭주가 run 시간의 60.7%를 순손실로 먹는다, D47 닫힘(400 0건), 스키마 필드 순서 위반 0건, `fail 0`인 run에도 폭주가 있다(D55) | `atom` | @danielcho02 |
| 2026-09-02 | [동시성 16·32 재측정](harness/2026-09-02-bench-concurrency-atom.md) | 16·32의 HTTP 400·timeout 소거(네 판 실패 0), **배치 처리량이 캐시 온도의 함수(#128)**, **캐시 OFF 곡선 1.81배·16 포화**, vLLM이 `user-1004.slice` **밖**에 있음, FLASHINFER 선택돼도 무사(확정 #3 ② 반증), context 스윕이 서버 상한을 모름(#126) | `atom` | @danielcho02 |
| 2026-09-03 | [모델 교대 — 잠재우기 실측](harness/2026-09-03-model-swap-sleep-atom.md) | build-plan §8-6 닫힘(교대 된다 — 잠재우기가 약 51 GiB 반환·왕복 2~6초 대 콜드 238초), `level 1`이 `level 2`와 구별 안 됨(§7 전제 반증), **`nvidia-smi --query-compute-apps`가 작동**(`[N/A]`는 `--query-gpu`뿐 — `mem_gb_ctx8k` 경로), 기준선의 51 GiB는 페이지 캐시 | `atom` | @danielcho02 |
| 2026-09-04 | [검색 둘을 포함한 다섯 상주 시도](harness/2026-09-04-five-model-residency-atom.md) | 검색 둘을 포함한 **네 서버 동시 상주**(가중치 합 27.66 GiB 대 KV 64.35 GiB — 제약은 상주 개수가 아니라 KV 예산), 32768 다섯은 **4/5**에서 CUDA OOM, **기동을 막은 것은 페이지 캐시**(`posix_fadvise`가 루트 없이 여분 81 → 108 GiB), **critic(devstral) 첫 기동 — registry의 `nvfp4`가 틀렸고 실제 `fp8`·VLM으로 해석됨**, scout 처리량 첫 실측(단일 35.78 · batch32 388.46), auditor `tok_s_batch32` 0 해제(66.42 · 적중률 0.5673 동반 · #133), 리랭커 순위는 실경로 모양으로 **미검증** | `atom` | @danielcho02 |
| 2026-09-04 | [실서빙 셋 첫 파이프라인 완주 시도](harness/2026-09-04-pipeline-zlib-serving-atom.md) | 7단계에서 죽었고([D65](../open-questions/D/D65.md)) **완주했어도 결과가 빈다** — Finding 150건 중 **148건이 `evidence_refs` 0개**라 게이트가 `p0`으로 단락(E0 148·E1 2), `path`는 **150/150 빔**. 술어를 실은 gate_eval은 4건뿐이고 그 넷은 `static_ok`·`has_repro_spec` True, `path_ok`·`location_ok` False. ⚠ critic `checksum` 0이라 비교표 밖 | `atom` | @danielcho02 |
| 2026-09-09 | [`gpt-oss-20b` 온보딩 첫 관문](harness/2026-09-09-gpt-oss-20b-onboarding-atom.md) | sm_121에서 MXFP4 **기동 성공**(MoE backend는 **MARLIN**, 가중치 14.91~16.06 GiB / checkpoint 12.82의 1.16~1.25배), Harmony vocab이 모델 밖 별도 에셋이라 `TIKTOKEN_ENCODINGS_BASE` 없이는 전 요청 500, **이 스키마·이 serve 설정에서 structured output 실패** — `xgrammar`는 5/5 오답(`reason`이 코드 분석이 아니라 「어떤 답변을 채점하는」 내용이었다)·`guidance`는 5/5 HTTP 500인데 `response_format` 없이는 **3/3 정답**. ⚠ 2026-09-09 정정: 다른 serve 설정·다른 어휘의 run이 3/3을 냈으나 축 넷이 함께 바뀌어 원인 미확정(보고서 머리). Critic 자리 판정은 ADR 0010 리뷰로 | `atom` | @danielcho02 |
| 2026-09-11 | [D67 — 스키마 required가 채움을 가르는가](harness/2026-09-11-d67-required-schema-atom.md) | **[D67](../open-questions/D/D67.md) 갈래 ② 확정** — `stock` 스키마에서 `path`·`evidence_refs`가 **0/20**인데 `required`로 올리면 성공한 **13/13**에서 채운다. 09-04의 프롬프트를 **원문 그대로 재생**하고 스키마만 바꿨다. **미상 evidence ID 0/13** — 강제해도 환각하지 않았다. 대가로 [D46](../open-questions/D/D46.md) 공백 폭주가 터지는데(꼬리 공백 12,849~13,199자 · 전부 `output_tokens == max_tokens`) **서버 `disable_any_whitespace`로 사라진다**(5/5 · 진단 10/10) — **D46 선택지 (a)의 첫 실측**. `backend=xgrammar`만으로는 재발하므로 귀속은 그 옵션 단독. ⚠ 정상 응답이 **332~522토큰**(상한의 1/8)이라 `output_max` 인상은 처방이 아니다. ⚠ `run_id` 없음(비교표 밖) · n=5 | `atom` | @danielcho02 |
| 2026-09-11 | [gpt-oss-20b 구조화 출력 — 축 다섯을 가른 9팔](harness/2026-09-11-gpt-oss-structured-vocab-flag-atom.md) | **아홉 팔 전부 무너진다.** 어휘(넷) · `reasoning_parser` · **enum 순서** · **속성 순서** · 백엔드를 갈랐고 **전부 기각**이다. **결정적 관측**: 같은 JSON 안에서 `reason`이 *"clear use-after-free"*라 쓰고 `verdict`가 **`safe`**다 — `reason`을 **앞에** 놓아 정답을 먼저 쓰게 해도 그렇다(「추론 전 커밋」 **기각**). enum 순서를 뒤집어도 의미를 따라가 **위치 편향 기각**. 유효 33시행 중 **28(85%)이 「문제 없음」 쪽**. **자유서술은 27/27 정답** — 틀리는 건 enum 토큰 하나다. ⚠ **`outlines`도 5/5 HTTP 500**(09-09 `guidance`와 같은 자리) — **쓸 수 있는 백엔드가 `xgrammar` 하나뿐이라 「구현 탓인가」는 대조가 없다**. ⚠ 미측정: `enable_in_reasoning` · **enum 제약 제거** · 케이스 축. ⚠ 마지막 팔의 **라벨이 서버를 잘못 가리킨다**(보고서 메타). ⚠ `run_id` 없음(비교표 밖) · R8을 닫지 않는다 · n=5 · 케이스 1종 | `atom` | @danielcho02 |
| 2026-09-12 | [Critic 미발화와 `gpt-oss-20b` 출력 토큰 회계](harness/2026-09-12-critic-gptoss-token-accounting-atom.md) | **Critic 0회의 연쇄 확정** — `evidence_refs=[]` 120/120 → `gate.evaluate` 첫 분기 E0 → 정책이 `queued`로 전이 → 7단계는 `hypothesized`만 고른다(`state.db`가 `queued` 120/120, `vllm_8006.log`에 `chat/completions` 0건). ⚠ **문턱은 「E2 이상」이 아니라 「E0이 아닐 것」** — E1은 `Route.HOLD`로 Critic에 간다. **`gpt-oss-20b`의 출력이 추론과 본문으로 갈린다** — 실 Critic 프롬프트(`prompt_tokens` **646~654**)에서 **본문은 76~126토큰**이고 나머지가 추론이다(`high` 추론 484~**16,381** · `medium` 69~227 · `low` 12~23, `/tokenize`로 각각 셌다). 하네스 실제 설정(4096·`high`·`Decision`) **16콜 실패 0**·비폭주 최대 1,508 → **`output_max_by_role.critic` 값은 안 정한다**. **vLLM 0.27.1의 필드는 `reasoning`이고 `reasoning_content`는 없다** — 09-09 §5의 「왜 비는가」는 계측 오류였고 그 필드로 낸 관측은 무효. auditor 실패 7/127은 [D46](../open-questions/D/D46.md) 공백 폭주(꼬리가 탭·개행뿐)인데 **서버 셋 다 `disable_any_whitespace=False`로 떴다**. ⚠ `run_id` 없는 프로브(비교표 밖) · 판정 정확도 미측정. D67 팔은 09-11의 **재현**이지만 거기서 하나 나왔다 — 그 옵션이 꺼진 서버에서 `stock` 4/5인데 **`required`·`min_items`가 0/5**라 **D46 (a)가 D67 수정의 선행 조건**이다 | `atom` | @danielcho02 |
| 2026-09-12 | [Critic `DecisionDraft` 출력 예산](harness/2026-09-12-critic-decisiondraft-budget-atom.md) | `DecisionDraft`(필드 넷)에서 **`output_max` 4,096이 안 터진다**(`finish_reason=length` **0/18** · 최장 2,863 · 여유 1,233) · **JSON 계약 18/18** — 09-09 온보딩의 *"이 스키마·이 serve 설정에서 structured output 실패"*와 **스키마·serve 설정이 둘 다 다르므로** 그 판정을 뒤집지 않는다. ⚠ **판정 정확도 실측이 아니다**(케이스 3종 손제작 · 케이스당 n=6 · 모델 하나 · `seed` 미고정) · `run_id` 없음(비교표 밖) · **원문이 ATOM 홈에만 있다** | `atom` | @danielcho02 |
| 2026-09-13 | [풀 세 자리 토크나이저 갈림](harness/2026-09-13-d71-tokenizer-divergence-atom.md) | **공유 캡슐을 첫 멤버(qwen) 토크나이저로 잴 때 갈림은 캡슐 합 +4.0% · 조각 최대 +10.6%** — 갈리는 자리는 **`scout_boundary`(mistral) 하나뿐**이고 두 qwen 자리(`scout_memory`·`scout_injection`)는 여섯 조각 전부 토큰 수가 **완전히 일치**한다. 방향은 대체로 mistral이 더 세는 쪽(합 +4%)이라 첫 멤버 기준 예산이 mistral에 **과소** = [D71](../open-questions/D/D71.md)이 경고한 spurious `BudgetExhausted`. **이 실측이 [#194](../open-questions/D/D71.md) 옵션 (A) 마진 `0.90`을 정했다**(헤드룸 1/0.90≈1.111 > 조각 최대 1.106). ⚠ **토큰 수만** 쟀다(GPU·판정 정확도 밖) · 캡슐 본문 **모양**의 조각 6개지 실제 run 캡슐이 아니다 · `run_id` 없음(비교표 밖) · 실서빙 재검은 build-plan §8 swap에 붙는다 | `atom` | @sumin0218 |
| 2026-09-13 | [D63 (a) — 리랭커 4b를 우리 입력 모양에서](harness/2026-09-13-d63-reranker4b-input-shape-atom.md) | **[D63](../open-questions/D/D63.md) (a) 첫 실측(대상 4b)** — 오늘 실경로 질의(식별자 하나 `parse_header`)에서 취약 3건이 무관 4건 위로 못 온다(전체-8 **raw 0/3** · 진짜 취약이 7위, 무관이 1위)인데 **자연어 질의는 3/3** → **4B에서 질의 모양이 판정을 가른다**(크기 축 미측정 · 방향은 09-08 검색기 결과와 같으나 그건 검색기 MRR이다). production 관문 `top_n=5`로 읽으면 Q1 raw는 취약 2/3(V1 탈락·무관 1위)로 완화되나 열화는 실재. **instruction 틀이 식별자 질의 순위를 바꾼다**(Q1 0/3→3/3) → D63 (b) 레버 신호(서버 동작은 미확정·틀 포맷 추정). ⚠ 문서 8건·사람이 고름·온전한 함수(실경로보다 쉬움) · `run_id` 없음(비교표 밖). 배선(0.6b→4b)은 **보류** → ⚠ **2026-09-20에 그 보류를 넘어 배선했다** — 두 풀 프로필만(`local_balanced_pool`·`local_balanced_pool_hybrid`), 나머지 셋(`local_balanced`·`local_balanced_hybrid`·`b1_single`)은 그대로 0.6b다. **이 보고서의 판정이 바뀐 것이 아니라 그 판정을 알고 §1을 따른 것이고**, 근거·범위는 [D63](../open-questions/D/D63.md)의 2026-09-20 블록이 든다 | `atom` | @sumin0218 |
| 2026-09-13 | [D63 후속 — 리랭커 instruction 양식](harness/2026-09-13-d63-reranker-template-atom.md) | **서버는 Qwen3에 템플릿을 안 붙인다**(vLLM 0.27.1 소스 — `get_score_template`은 `jina_vl`만, `is_original_qwen3_reranker`는 점수법만). 정식 채점은 `/rerank`가 아니라 **생성+`P(yes)`**인데, `/rerank`로 넣는 **부분 양식(`<Instruct>/<Query>/<Document>`)이 정식 생성 채점과 ≈**(실경로: 맨질의 FAIL·부분양식 PASS≈생성 PASS) → **client 재작성 불필요, `/rerank`에 양식만 씌운다**(이 PR 배선). 배제셋 ②·미확인② 해소. ⚠ 8건·압축 코드라 지시적 · 실조각·32건·기준선은 배선 후 · `run_id` 없음(비교표 밖). ⚠ **2026-09-20 — 이 보고서의 두 문장을 현재형으로 읽지 마라**: *"지금 프로필의 리랭커는 `qwen3-reranker-0.6b`"*는 11 프로필 중 **둘에서 더 이상 아니고**(두 풀 프로필이 4b), *"0.6b→4b 교체는 그 검증을 통과한 뒤다"*는 그날의 권고인데 **통과 전에 넘었다**([D63](../open-questions/D/D63.md)의 2026-09-20 블록). 보고서 본문은 날짜 기록이라 고치지 않는다 | `atom` | @sumin0218 |
| 2026-09-14 | [Joern 슬라이서 실서버 검증](harness/2026-09-14-joern-slicer-live-atom.md) | Joern 4.0.605 실서버에서 C/C++ 회귀와 zlib 362후보를 전량 처리해 `partial`·slice 203건·flow 절단 293건·누락 파일 3건을 실측하고, 최신 회귀의 반복 동일성과 cap 동작을 확인 | `atom` | @danielcho02 |
| 2026-09-14 | [Joern 슬라이서 공개 스모크 표본 3종](harness/2026-09-14-joern-slicer-smoke-samples-atom.md) | 같은 runner 표본에서 최종 slice 55/29/146과 알려진 GT 범위 줄 17/50/199를 사후 대조. 01·02는 전 후보 처리, 03은 415개 뒤 JVM heap OOM으로 21개 미처리라 PM 71/28/167과의 차이를 판정 보류 | `atom` | @danielcho02 |
| 2026-09-15 | [슬라이서 slice-v2 실서버 검증](harness/2026-09-15-joern-slicer-v2-atom.md) | **표본 3종이 모두 완주했다**(`queries_aborted=0`·`candidates_not_processed=0`) — sample_01 `150/68` · sample_02 `52/30` · sample_03 `436/143`. **중단·재개가 연속 실행과 같은 `slices`를 낸다**(fixture `slices_equal: true`, 재개 trace의 `flows`가 2회가 아니라 **1회**). sample_03은 600초 기한에 걸린 1차를 **재개로 이어 완주**했고 그 구간은 완료 후보 331건을 **재질의 0회**로 건너뛰었다. **[#205](https://github.com/0xM1yu/whitehat-boss/issues/205)가 지목한 `src/tftpcmd.c` switch 누락이 사라졌다**(`case`·`default`·`break` 누락 0) → [D77](../open-questions/D/D77.md) **닫힘**. **이 검증이 결함 둘을 잡았다** — 버전 배너의 timestamp가 재개 계약을 매번 깨뜨린 것, 문맥 질의가 **JVM 문자열 상수 상한(65,535B)** 을 넘겨 컴파일되지 않은 것. ⚠ 모델·GPU 없음(탐지 정확도 아님) · `run_id` 없음(비교표 밖) · 재개 **바이트 동등성은 fixture 규모에서만** · 절단된 Case A의 `analysis`는 CPG 재생성 사이에서 같지 않다(§4.5) · PM 기준 `GT 줄` 정의 미확정이라 축소 성능 차이로 읽지 않는다 | `atom` | @danielcho02 |
| 2026-09-21 | [로스터 여섯 자리의 가중치·KV 단가와 교대의 시간 단가](harness/2026-09-21-roster-memory-kv-atom.md) | **A안 로스터의 실측이 찼다** — 여섯 자리 전부 가중치와 KV 토큰 단가를 얻었고, 마지막 빈칸이던 `qwen3-reranker-4b`가 **8.12~8.15 GiB**다. 이제 남은 미실측은 `nemotron-3-super-120b-a12b` 하나이고, 그 키는 registry 미등재다. **KV 토큰 단가가 자리마다 5.9배 갈리고 방향이 직관과 반대다** — devstral(24B) **160** · 리랭커(4B) **144** 대 `qwen3-coder-30b-a3b`(30B) **48** · `gpt-oss-20b` **27.1** KiB/token으로 **작은 모델이 더 비싸다**. 가르는 것은 파라미터 수가 아니라 **KV헤드 × 층수 × `kv_cache_dtype` × 슬라이딩 윈도**이고, 구조식 `2 × kv헤드 × head_dim × dtype바이트 × 층`이 **다섯 모델에서 실측 역산과 맞았다**(`gpt-oss-20b`만 `sliding_window: 128` 때문에 구조식의 56%). ⚠ **`kv_cache_dtype: auto`는 가중치 양자화에서 추정할 수 없다** — NVFP4 둘은 `fp8_e4m3`(1바이트)인데 **fp8 가중치인 devstral은 bf16**(2바이트)다. **4단계(A안 피크) 최소 ≈ 61 GiB** — 2026-09-04 §8이 같은 셋에서 본 **99 GiB는 요구량이 아니라 그때 준 `util`**이었다(그 보고서 *"제약은 KV 예산 배분"*의 수치판). **교대로 가면 단가가 시간이다** — 콜드 기동 **16~186초**, 4단계 셋 순차 **≈ 6.8분**(ADR 0009의 「왕복 2~6초」는 잠재우기/깨우기라 다른 수다). 재현 넷: devstral 25.83↔25.48 · 30b 17.76↔18.28 · 8b 7.06↔6.38 · embedding 1.76↔1.62. **NVFP4는 `--attention-config.backend TRITON_ATTN`을 CLI 인자로 줘야 `ninja`로 안 죽는다**(env는 무효). ⚠ **동시 상주는 안 쟀다**(한 번에 하나씩) · 처리량·정확도 밖 · 잠재우기/깨우기 왕복 안 쟀다 · `run_id` 없음(비교표 밖). ⚠ **2026-09-24 — 이 행의 「A안」·「남은 미실측」은 교체 전 로스터 기준이다.** 로스터는 2026-09-21에 교체됐고 Auditor A/B는 그때 소멸했다([PROJECT-CONTEXT.md](../../PROJECT-CONTEXT.md) §1 · #240). 보고서 본문은 날짜 기록이라 고치지 않는다 | `atom` | @danielcho02 |
| 2026-09-22 | [Case B operator 앵커·WRITERS 실측](harness/2026-09-22-case-b-operator-anchors-atom.md) | PR #243의 cap·좁힘 근거. operator 모집단이 폭증한다 — WavPack에서 인덱스 접근 **3,042**(쓰기만 550)·add/sub **3,309**(포인터 피연산자 278). 포인터 판정은 **`*`만으론 typedef 포인터를 놓쳐** star∨메모리접근 두 축으로 간다(pcre2 `mb->end_subject - eptr`가 star 198엔 없고 메모리접근 293엔 있다). 새 질의 후보 수 sample_09 815·25 1,910·08 3,169(전부 앵커별 cap 2000 이하). **동일 CPG baseline↔new(sample_09): GT sink 486이 baseline 0 → new 1**(`pointer_arithmetic`). sample_25는 매크로 메서드 `FilterGradientBPP` 본문이 CPG에 없어 미포착(#244), sample_08은 인덱스 읽기라 writes-only 유계의 한계. WRITERS는 sample_04 필드포인터 memset을 새로 잡는다. ⚠ **후보 생성만 쟀다**(슬라이스·판정 없음) · 표본 3~5개(40 전수 아님) · 탐지 recall 아님 · `run_id` 없음(비교표 밖) | `atom` | @sumin0218 |
| 2026-09-24 | [새 로스터 역할별 실서빙 스모크 — Scout 풀 · Auditor](harness/2026-09-24-roster-smoke-atom.md) | **[ADR 0012](../decisions/0012-harness-canon-v2.md) 결정 4의 새 키 둘(`seccoderx-8b`·`qwen3.6-35b-a3b`)과 injection 잠정 후보 Qwen3-14B(registry 밖)가 처음 떴다** — 셋 다 sm_121에서 기동했고 Scout는 31/31이 스키마를 통과했다. `seccoderx-8b`와 Qwen3-14B는 한 장비에 함께 올라간다. 정본 injection `deepseek-r1-distill-qwen-32b`는 안 띄웠다. Auditor는 그 풀과 함께 못 올린다(계산). **Auditor 템플릿은 사고를 열지만 완주한 판은 사고 없이 답했다** — `--reasoning-parser`를 걸면 사고가 4,096토큰 안에 안 끝나 Finding이 0/7이다. parser를 빼고 `xgrammar`·`disable_any_whitespace`로 띄우면 36/36인데, 문법이 첫 토큰부터 걸려 **사고 없이 답한다**(품질 영향은 안 쟀다). **호출 하나가 `_TIMEOUT_S` 300 s에 걸려 run 전체가 멈췄다** — 서버는 생성 중이었다(13.3 · 8.1 tok/s, 이슈 #261). Scout 계약 위반(reject인데 1.0 · 첫 코드 형식 이탈 · `location` 누락)은 조용히 저장·승격된다([D82](../open-questions/D/D82.md)·[D83](../open-questions/D/D83.md)). Auditor `missing_evidence` 26/36은 해제 경로 없는 blocking이 됐다([D65](../open-questions/D/D65.md)). E1 32건은 blocking만 9 · GRAPH 근거 없음만 6 · 둘 다 17이고, Critic은 넷 다 반박했으며, **E3는 0**이다. ⚠ 실서빙 키 `checksum` 0(비교표 밖) · 머지하지 않는 브랜치 · 후보 12 · 정답 대조 없음 · ADR 0012 결정 5가 승인 전이라 이 폴더에 적었다(⏵ 2026-09-25 승인) | `atom` | @danielcho02 |
| 2026-09-26 | [슬라이스 입력 실서빙 스모크 — Auditor · Scout 풀](harness/2026-09-26-slice-smoke-atom.md) | **Phase 3(슬라이스 입력)을 실모델로 처음 돌렸다.** ① **1-b는 입력 몫 안에 든다** — Auditor 실호출 147건에서 서버가 센 `input_tokens` 최대 15,437, 입력 몫 20,480 초과 **0건**(여유 5,043). ⚠ **논거는 틀렸었다** — 글자/토큰 비가 1.22~3.32로 벌어지고 **글자 최대 프롬프트가 토큰 최대가 아니다**(37,081자=13,944토큰 vs 20,917자=15,437토큰). 글자 상한은 폭주 차단이지 토큰 상한이 아니다 — 상수 주석과 ADR 0012 결정 1-b를 이 실측으로 고쳤다. 슬라이스 147개 중 **103개가 함수 둘 이상**에 걸쳤다(최대 25) — 넓히기 전이면 하나만 봤다. 상한이 5개 슬라이스에서 함수 249개를 뺐다. ② **Scout는 컨텍스트를 넘는다** — 실 호출 33건 중 12건이 400으로 거절됐고 한 run은 9/9가 거절돼 **후보 0·Finding 0**이다([D87](../open-questions/D/D87.md)). 원인이 슬라이스 렌더가 아니다 — 거절된 프롬프트 50,949자가 FACTS 13,081·Capsule 8,621·**렌더 2,212**·고정 접두 ≈27,000이다. Scout 스키마는 **21/21이 첫 시도 통과**. 실패한 Auditor 호출 하나가 310.9 s라 **기본 타임아웃 300 s였으면 run이 멈췄다**(#261). ⚠ 탐지 품질 미측정(정답 대조 없음 · 한쪽이 늘 Fake) · 사고 켠 Auditor 미측정 · 실 Scout은 11개만 · 실서빙 키 `checksum` 0(비교표 밖) · 머지하지 않는 브랜치 | `atom` | @danielcho02 |
| 2026-09-26 | [index-v2 — 표본 셋의 모델 입력 불변 · Scout 풀 실서빙](harness/2026-09-26-index-v2-atom.md) | **index-v2는 표본 셋에서 모델 입력을 안 바꾼다** — 슬라이스 241개를 main(index-v1)과 PR(index-v2)로 태운 fake run의 프롬프트 **412/412 바이트 동일**, `symbols`·간선 동일. 헤더를 넣어도 표본 헤더에는 함수 정의가 0개라 캡슐이 안 움직였고, sample_03 매크로의 69%가 헤더에서 왔다. `index.json` 2.2~2.6배. v2 이미지로 Scout 풀 실서빙(운영 창 40,960) 24/24 · 스키마 첫 시도 26/27. ⚠ 줄 수가 수천인 슬라이스 둘은 **40,960 창도 넘는다**(렌더 60,181·184,248자, index-v2와 무관). ⚠ 실서빙 키 `checksum` 0(비교표 밖) · 머지하지 않는 브랜치 · 탐지 정확도 아님. (⏵ 2026-09-26 저녁 — 헤더에 함수 정의가 있는 평가 표본 sample_24에서는 **바뀐다**: 프롬프트 Scout 53/207 · Auditor 23/94 — 보고서 §7. 같은 측정에서 동시에 돈 run 둘이 컨테이너 이름을 겹쳐 도구가 조용히 실패하는 하네스 결함이 드러났다 — #303) | `atom` | @danielcho02 |
| 2026-09-26 | [vLLM 0.27.1이 실제로 내는 400 본문과 러너 분류](harness/2026-09-26-vllm-400-bodies-atom.md) | **[D88](../open-questions/D/D88.md)의 전제를 실응답으로 쟀다** — 크기 문구(`maximum context length`)를 싣는 400은 컨텍스트 초과 하나뿐이다. 출력 상한 > 창 · 사고 예산 + parser 없음 · 템플릿 예외 · 요청 본문 검증 · 사고 예산 + V2 러너는 전부 400이고 그 문구가 없다. **본문 검증은 422가 아니라 400이다.** 하네스 어댑터가 받은 `detail`(전부 512자 미만이라 안 잘림)을 PR #298의 러너 분류에 넣어 여섯 거절 모두 기대대로 갈렸다. ⚠ 하네스 run이 아니다(어댑터 직접 호출) · 가중치는 Qwen3-0.6B이고 템플릿만 Qwen3.8-27B 것 · `qwen3.8-27b` 실모델 미측정 · 경우당 1건 | `atom` | @danielcho02 |
| 2026-09-27 | [need_evidence 루프(Phase 5 RAG) 실서빙 스모크 — Scout 풀](harness/2026-09-27-ne-loop-smoke-atom.md) | **resolver는 실제 Scout 신청서 48개 중 40개(83%)에 원본 블록을 돌려준다**(직접 호출 · 중앙 825자 · 6개는 색인이 없던 sample_07의 프로브 색인). **설계 그대로는 루프가 한 번도 안 돌았다** — 84좌석 중 hop0 need_evidence 5(6%)이고, 설계 run의 2건은 동료 확인(`peer_review`)으로 건너뛰었다. 스모크 전용 스위치로 루프에 넣은 10좌석에서 **판정이 바뀐 것은 1**(review→reject, GT와 반대 방향)이고 NE 둘은 받고도 `repeat_request`로 섰다(함수 포인터 호출 대상 · 변수의 값). 추가 호출 11건 전부 성공 · wall 99~438 s · 창 초과 0. **인덱서가 데이터 배열 헤더에서 300 s를 넘겨 색인이 없었고(#314) 그 run이 「못 찾음」으로 기록됐다(#315).** ⚠ 입력이 GT 경로 슬라이스(팀원 제공, 슬라이서 커밋 미기재 · 전부 양성 · `entry`가 정답 sink) — 탐지 정확도·운영 NE 비율 아님 · 실서빙 키 `checksum` 0(비교표 밖) · 머지하지 않는 브랜치 | `atom` | @danielcho02 |
| 2026-09-28 | [need_evidence 루프 실서빙 스모크 — 정상 표본](harness/2026-09-28-ne-loop-smoke-benign-atom.md) | 정상 표본 다섯을 ATOM에서 슬라이싱해 라벨 함수에 걸치는 슬라이스만 Scout 풀에 올렸다(셋 · 여덟 타깃 · 24좌석). Scout-only에서는 **라벨 함수가 직접 타깃인 파싱 판정 8개 중 review 3개**(xdr boundary · vmxnet3 memory와 boundary)였고, 나머지 승격 6개는 같은 xdr 파일의 라벨 없는 다른 함수 다섯에서 나왔다. 재실행 `harness_pool` 0004~0006에서는 Scout→Auditor→Critic을 실제 서빙했다: `Compute`은 E0 `queued`, vmxnet3 memory는 Critic rebuttal 뒤 `queued`, vmxnet3 boundary는 Critic upheld 뒤에도 repro spec 없어 `hypothesized`; xdr는 GPU 예산 7,353/7,200 s 소진으로 Critic 미호출 상태의 `hypothesized`다. 0001~0003은 4단계 교대 훅 실패·모델 호출 0으로 제외. ⚠ 조건 B(동료 확인 끔) · 슬라이서 산출 세 표본 모두 `partial`(xdr는 7,200 s 상한) · `stopped_after` run 포함 · 탐지 정확도·오탐률 아님 · Scout·Auditor serving checksum 0(비교표 밖) · 머지하지 않는 브랜치 | `atom` | @danielcho02 |
| 2026-09-29 | [단계 단위 콜드 재기동 교대 종단 스모크](harness/2026-09-29-stage-swap-smoke-atom.md) | **`harness_pool`이 단계마다 필요한 모델만 띄우고 내리며 완주했다** — ADR 0012 Phase 6·6.5 PR 다섯(#332·#334·#329·#333·#330)의 합성 트리에서 교대 네 번(Scout 320 s · Auditor 445 s · Critic 135 s · 내림 1 s)이 전부 `ok=true`이고 run wall 4,963 s의 18%였다. 네 키의 서빙 창이 registry `context_limit`과 같았다(Auditor **262,144** — 창·KV는 ATOM 첫 실측(KV 636,746토큰) · 적재 51.1 GiB · 359 s · 준비 442 s · 4.27 tok/s(09-28 스모크 Auditor 여섯 호출의 4.17~4.29와 같은 자리, 계산값) — ⚠ Auditor `request_timeout_s` 7,500 s가 전제한 4.5보다 낮다: 32,768 ÷ 4.27 ≈ 7,674 s, 계산값 · 보고서 §3·§7). 모델 호출 7건 전부 첫 시도 성공 · 400·타임아웃 0 · `issued_override` 0 · 감시자 개입 0(최저 16.7 GiB) · run 끝에 서버 기록 0·감시자 정지. ⚠ **Auditor 기동 전 메모리 판정은 0.4 GiB 안팎의 여유로 통과했다**(게이트 비교값 115.2 ≥ 114.8 GiB — 둘 다 0.1 GiB 반올림 · 반올림 전 여유 0.39~0.49 GiB는 계산값 — 선택지는 보고서 §4, PR #334). ⚠ 슬라이스 1 · 좌석 3 — 탐지 정확도 아님 · Auditor v3 입력 3.8k토큰(창 262,144의 1.4%)이라 큰 입력의 호출 시간·GPU 예산에 답하지 않음(옛 창 53,248 기준 입력 몫 우려 — 여유 5,043은 [2026-09-26 스모크](harness/2026-09-26-slice-smoke-atom.md) — 는 이 창에서 해당 없음) · Scout·Auditor `checksum` 0(비교표 밖) · 머지하지 않는 브랜치 | `atom` | @danielcho02 |
| 2026-10-02 | [CyberGym 오라클 왕복 — 채점 경로가 실재하는가](harness/2026-10-02-cybergym-oracle-roundtrip-win11wsl.md) | **채점 경로는 실재한다** — 저자 서버(commit `c6fe202`)를 로컬 `127.0.0.1:8666`에 띄워 참조 PoC 12바이트를 `/submit-vul`에 던져 `exit 77`, 키 넣은 `/submit-fix`에서 `0`, sqlite 행 `vul_exit_code=77 · fix_exit_code=0`으로 성공 조건을 충족했다. 키 없는 private 엔드포인트는 **404**이고 Windows 경로 바인드 마운트가 작동한다. 과제 이미지 셋(`n132/arvo:1065-{vul,fix}` · `cybergym/oss-fuzz:368076871-vul`) 모두 공개되어 있다. **그러나 판정이 결정적이 아니다** — 같은 입력 10회에서 `fix`가 `0` 5회·**`139` 5회**, `vul`이 `77` 8회·**`139` 2회**다. `139`는 libFuzzer 배너 전 SIGSEGV(출력 0바이트)라 입력과 무관하고, **무해 12바이트**(ASLR 끈 `-vul`에서 `0`×5로 무해를 먼저 입증)가 `-vul`에서 **20회 중 8회 `139`** = 성공 조건상 「크래시」로 집계된다. 그래서 판별력이 **정답 ≈0.40 대 오답 ≈0.28**(표본 비율에서 나온 계산값)이다. 원인은 Docker VM 커널 **`vm.mmap_rnd_bits = 32`** — MSan 섀도 매핑이 간헐 충돌한다. ASLR을 끈 대조(컨테이너 플래그만)에서 **`vul` 77×10 · `fix` 0×10**으로 결정적이고 판별된다. ⚠ 모델 호출 0건 · `run_id` 없음(비교표 밖) · 과제 `arvo:1065` 하나(MSan) · n=10·20이라 모비율 아님 · 병렬 제출 거동 0건 · `exit 300`을 응답이 `0`으로 덮는 것은 **소스 대조만**(실측 0건) · **처방까지 들어갔다** — 2026-10-02 사용자 결정으로 Docker 리눅스 VM의 `vm.mmap_rnd_bits`를 32 → 28로 내렸고(저자 코드·컨테이너 플래그 **무변경**), 같은 네 조합이 **40/40 기대와 같다**(`139` 0건 · 판별력 정답 1.00 대 오답 0.00). ⚠ **그 값은 영속하지 않는다** — Docker Desktop 재시작으로 32로 돌아가므로 **처방이 아니라 캠페인 전 카나리 게이트가 본체다** · 팀원 장비 0건 · 28이 ASan·`oss-fuzz` 과제에도 충분한지 0건 | `win11wsl` | @danielcho02 |
| 2026-10-03 | [과제 설명문을 쓰고 있나 — 코드 경로 전수와 신호량](harness/2026-10-03-description-usage-survey.md) | **「설명문을 안 쓴다」는 반만 맞다** — `run` 안에서는 입구 둘(`--task-dir`·`--description-file`)에서 캡슐 `vuln_description` 섹션까지 배선이 서 있고 Scout·Auditor·PoV 프롬프트 바이트에 실린다(테스트 핀 하나가 세 역할을 동시에 문다). 끊긴 것은 둘 — ① **그것을 쓰라고 말하는 프롬프트 문장이 0건**(문형 다섯으로 전수: `vuln_description` 0 · `과제 설명` 0 · `description` 51건 전부 판본 메타·출력 스키마 필드·외부 문서 금지 · `설명` 4건 전부 반대 방향 지시 · 보고 계열 0 — 손 훑기 델타 0). 모델은 `vuln_description` **라벨과 본문은 본다** ② **슬라이서는 안 읽는다**(#407 이사 뒤 `tools/slicer/` 12파일 + 보조 넷에서 `description` 2건이 둘 다 `argparse(description=__doc__)` · CLI 인자 0개). [build-plan.md](../build-plan.md)는 파일 전체에서 `설명` **0회**. 번들 n=2 실측: 79 B·13단어 / 198 B·28단어(둘 다 개행 0 한 줄 · 각 디렉터리 정확히 두 파일) → 가장 좁은 캡슐 상한 1,800(`light 4000 × 0.90 ÷ 2`)에서 근사 19·49토큰으로 **절단 조건 미달**. 🔴 **역량은 미측정 — 설명문이 점수를 바꾸는지 잰 run 0건**(길이 분포·실 토크나이저·`vuln_description_truncated` 실관측도 0). 가능한 소비자 둘은 프롬프트 지시(S9·실험팀)와 슬라이서 소비(대여 호스트 필요)로 **둘 다 이 묶음 밖**이라는 것이 결론 | **없음** — run 0건·모델 호출 0건·GPU 미사용(⚠ 파일명 끝 `survey`는 장비 라벨이 아니라 주제의 꼬리다) | @danielcho02 |
| 2026-10-03 | [CyberGym 배관 증명 — PoC 바이트가 실제 오라클에 닿는가](harness/2026-10-03-cybergym-plumbing-proof-win11.md) | 🔴 **배관 증명 · 역량 미측정**(전 역할 Fake). **배관은 성립한다** — `pov_poc/0001..0008.poc`(68 B × 8, 고유 sha256 1개) → `POST /submit-vul` **8건 전부 200·실패 0건**(`cached` **미적중 1 · 적중 7**) → 서버 DB `poc_records` 행 1건. DB 행과 장부가 **같은 `poc_hash`/`poc_sha256`·`poc_id`**를 든다 ⚠ **신원 대조이고 판정값 대조가 아니다** — 장부는 판정값을 들지 않아 `vul_exit_code`는 sqlite로 직접 읽었다. 앞 차단이던 **`submit.sh` 부재가 걷혔다** — 번들에 없던 그 파일을 **저자 틀(`task/arvo_task.py:82-88`)의 치환 다섯으로 조작자 쪽에서 만들어** 제출 스위치(우리가 센 검사 여덟, 그 함수엔 `unreachable`·`--mask-map` 게이트가 더 있다)를 통과시켰다. 변조 사본 셋이 빨개지는 것은 봤으나 **일회성·미커밋이고 `cli.py:699` 거부 경로를 타지 않았다** — 영속 핀은 `tests/test_oracle_cli.py:238-240`. ❌ **왕복(vul→fix→조회)은 여전히 0건** — vul 성공 후보가 8건이라 최종 PoC를 지정하지 않아(`final_reason: policy_undecided`) `fix` 다리와 최종 `query_verdict`가 0이다. ⚠ 단 **제출 전 캐시 판별용 `/query-poc`는 8건 돌았다**(404×1·200×7). 🔴 **중간 차단이 새로 드러났다** — `input_tokens 200,000`을 **204,963**이 넘겨 run이 **대상 9/47**에서 끝났다(`stopped_after: 4`) → 7·8단계 미도달 → 바이트 0개. ⚠ **6단계 `upheld=false`는 예산 탓이 아니라 모든 run의 정상 상태다**(Critic 앞 · 완주 run과 payload 동일). 상한을 **로컬·미커밋 제거**(diff sha256 `7dcf886b…` 보존, run 뒤 되돌림, `repo_dirty=true`)하자 완주했다. 게이트는 **단계별 재등급**(6단계 11건 E2 → 7단계 8건 E3 → 8단계 같은 8건 E4)이고 **분포가 아니다** — 최종 분포는 run `report.md`의 **E3 8 · E2 3**. ⚠ **8단계 E4는 `FakeToolRunner`의 상수 `exit_code=1`**이고 같은 run 오라클은 `vul_exit_code 0`이라 **어긋난다**. 완주 원장 `input_tokens` **510,732**·`output_tokens` **7,079**는 **계산값**이라 상한도 비용도 못 된다 → 새 상한 값을 제안하지 않는다(이슈 #424). ⏵ 2026-10-03 밤 — **#427이 상한을 `0`(= 상한 없음)으로 내려 #424가 닫혔다**(위는 `1fa2182`의 사진). 카나리 **실 Docker 첫 실행** 40/40 통과(`139` 0건, `mmap_rnd_bits=28`). 🔴 우리 68 B(`A`×64 + 리틀엔디안 `0xDEADBEEF`)의 `vul_exit_code`는 **0**(어제 참조 PoC 12 B는 77) — Fake 산출물이라 **「점수 0」으로 쓰지 않는다** · 역량 **0 미측정** · 다른 과제 0건 · 마스킹 0건 · 32에서의 카나리 실패 0건. **첫 판이 틀렸던 자리 아홉을 보고서 §10에 남겼다**(적대적 리뷰 2인, 둘 다 FAIL) — 머지 뒤 리뷰가 잡은 여덟(제출 횟수를 PoC 수로 읽음 · 「값에 의존하는 테스트를 못 찾았다」가 문자열 전수의 결과였음 등)을 같은 절의 둘째 표에 더했다 ⏵ **같은 날 밤 §11이 붙었다 — 둘째 과제 계열**(`oss-fuzz:368076871`, `cybergym/oss-fuzz` 이미지)**에서 다시 돌렸다.** PR #427이 상한을 `0`(= 상한 없음)으로 내려 **override 없이 `repo_dirty: false`로 완주**했고(`stopped_after: null` · 도달 단계 0~9 · `budget` 이벤트가 `charge` 1,560건뿐, `exhausted`·`blocked` **0건**) 제출 **8건**(**고유 PoC 1개** · `cached` 미적중 1·적중 7)·실패 0, 장부와 DB가 같은 `poc_id 48055248…`·`poc_hash 7276234e…`를 든다. ⚠ 그 해시는 **첫 계열과 같다** — Fake PoC는 결정론이라 과제가 달라도 바이트가 같다. 🔴 **번들이 58.7배**(28.9 MB → 풀면 153 MB)라 원장이 `input_tokens` **24,030,713**(**계산값**)이고 **옛 상한의 120배**다 — 예산 수정이 필요했다는 것이 이 수로 증명된다. 🔴 **후보가 8인 이유 — `critic_top_n`(모든 프로필 공통 상한)에 닿았다**(Finding 31 → `critic_skipped` 23 → `critic` 8 → `pov` 16 → PoV 시도 8 · 고유 PoC 1). 이 run은 Fake Critic이 올라간 8건을 전부 `upheld`로 돌려줬다. ⚠ 「승격이 둘 이상이면 후보가 둘 이상」은 **n=2 관측**이고 승격→PoC 1:1은 **미확인**이다. 그 시점의 지정 규칙은 후보가 둘 이상이면 등급을 읽지 않고 거부했고, `dev_fake`에서 8단계 `gate_eval`이 같은 8건에 전부 E4를 준 것(동점)은 **기록**일 뿐이다 — 원인 후보(Fake 도구의 상수 · 지정 시점 등급 경로, PR #460 리뷰)를 확정하지 않았으므로 **등급 변별력은 미실측**이다. 🔴 §7의 두 칸을 닫았다 — 「다른 과제」는 **1건**(단 **N=2**, 셋째 계열 `oss-fuzz-latest:`는 저자 서버가 `vul` 제출부터 400으로 거부하고 Level 1 포함 여부는 이 run 시점에 **미실측**이었다 — 그 뒤 이슈 #431 코멘트가 0/1,507을 적는다), 「`fix == 0`의 1차 출처」는 **있다**(저자 이슈 #15의 `vul not in [0, 71, 300] and fix == 0`) — 🔴 **대신 우리 `vul` 쪽에 `71`(OOM)이 없어 성공을 과대 보고한다** ⏵ **2026-10-04 고쳤다** — 술어가 `(0, 71, 300)`이고 이 문장은 그 전의 상태다(이슈 #430). 카나리 도구는 이 run의 `repo_commit`(`05a80d5`) 시점에 이 계열을 **0회** 돌았고(그 커밋은 `arvo`만 알았다) 판별력은 **손 프로브 n=3**(직접 `docker run`)으로만 봤다 — 그 뒤 값은 PR #463 본문이 낸다. 왕복은 **두 계열 모두 0건** · 역량 **0 미측정**. **이 절의 첫 판이 틀렸던 자리를 보고서 §11.8에 남겼다**(적대적 리뷰 1인) | `win11` | @danielcho02 |
| 2026-10-04 | [CyberGym 운영 run — 첫 실모델·실도구 완주, 점수는 못 냈다](harness/2026-10-04-cybergym-operational-run-vastai-remote.md) | **원격 교대 종단이 처음 성립**했다(서빙은 vast 원격 · 하네스·도구·오라클은 로컬 · 기동 준비 25~139 s · 내림 약 5 s). 4슬라이스 run 둘이 완주했으나 **PoC 0건 · `oracle_final reason=no_poc`**(오라클 제출 0) — Auditor가 도구 Evidence 둘을 받고도 `evidence_refs=[]`로 냈고 세 Finding이 전부 부정 결론(확신 0.05~0.10)이다. 인용 거절은 타당했다 — `semgrep` 본문이 절단돼 히트가 안 보인다(#481). 실도구 넷 중 `clang-tidy`·`build`는 이 번들에서 컴파일되지 않는다(`uint8_t` 미정의 19건). ⚠ **첫 판의 「2단계 Evidence가 Finding에 안 붙는다」는 틀렸다**(이슈 #475 정정 — 잇는 코드는 있고 돌았다) · `grade=None`도 없는 키를 조회한 값이었다(#478 철회). ⚠ 점수·비교표 아님 · 서빙 키 `checksum` 0 · `repo_commit`이 로컬 합성(#417·#460·#463 미머지) · Auditor v6·`SOURCE` 옵션 0(main은 이후 v7) · 슬라이스 4/220 · 과제 `arvo:1065` 하나 · Critic·PoV 0회 · **#417 머지 뒤에 읽는다** | `vastai-remote` | @danielcho02 |
| 2026-10-06 | [PoV 좌석 단독 프로브 — `feyospace-v1-small`이 처음 돌았다](harness/2026-10-06-pov-seat-probe-vastai.md) | **PoV 좌석이 처음 떴다**(준비 319 s · 가중치 51.1 GiB · KV 516,532토큰 · 디코드 **17.2~18.1 tok/s** 단일 스트림 — 전부 이 좌석 첫 실측). 홉 넷이 전부 스키마를 통과했고(`inspect` 1 · `gen` 3) 도구를 요청해 실행됐으며 PoC가 **20 → 400 → 8,192바이트**로 조립됐다. 중간 `gen` 둘이 오라클 `vul`에 제출돼 판정이 다음 홉 프롬프트에 `oracle_vul exit_code=0`으로 실렸다 — **PoV v3 되먹임이 끝까지 돈 첫 사례**다. 🔴 **그러나 `submit` 0건** — `max_hops: 3`을 다 써 `max_hop`으로 끊겨 최종 후보가 0건이다(결함 1 · S7 선행). 사고가 홉마다 상한을 거의 다 써 홉 하나가 23~28분이다. ⚠ **완주 아님**(세션 백그라운드 한도 90분에 하네스가 종료돼 `run_manifest.json`이 없다) · n=1(proven 8 중 1건만 루프) · Scout·Auditor·Critic은 Fake이고 `tool_runner: fake`라 도구 관측이 합성값 · **임시 프로필**(커밋 안 함) · 점수·비교표 아님 | `vastai-remote` | @danielcho02 |
| 2026-10-08 | [첫 실서빙 풀체인 run — 점수 1인 PoC를 만들고 버렸다](harness/2026-10-08-first-real-serving-run-arvo1065-vastai-remote.md) | **실모델·실도구·실채점기로 0~8단계를 완주한 첫 run**(8시간 05분 · 후보 12 · Finding 5 · PoV 시도 5 · 채점기 제출 7). 🔴 **PoV가 판정식상 점수 1인 PoC를 만들었는데 하네스가 버렸다** — 6바이트 `<html>`이 취약 빌드에서 MSan `use-of-uninitialized-value`(`file_replace` `funcs.c:478:24` · 미초기화 값은 `rm` = 과제 설명이 말한 `pmatch`)를 내 `vul exit 77`이고, **수동** fix 다리가 `0`이라 `77 ∉ [0,71,300]`과 `fix == 0`을 충족한다. 그런데 제출 7건이 전부 중간 `gen`이라 `designatable: false`이고 모델이 `submit`을 끝까지 안 내 `final: none` · `reason: no_poc` → **점수 0**(#549). 기제 둘 — 프롬프트가 `oracle_vul`을 **원시 종료 코드**로 주면서 어느 값이 크래시인지 안 알려주고, `submit`은 **마지막 `gen`** 을 집는다. **슬라이스에 정답이 없었는데 맞혔다**(4조각 전부 `apprentice.c` · `regexec`·`pmatch` 0건) — 설명과 채점기 되먹임만으로 도달했다. 처음 잰 것: **Auditor 도구 루프 v10이 실모델에서 돈다**(`lookup` 사용 · `probe` 0건 · 홉 511·1,792초 · 출력 12,084·44,532토큰) · 디코드 **23.6~25.1 tok/s**(종전 16.2의 1.5배, ⚠ 다른 GPU) · PoV가 퍼즈 진입점을 찾고 실 채점기에 제출. 🔴 막은 것 셋 — 로컬 `poc` 도구가 매 홉 `exit 127`(#513·#507)이라 관측이 불변이고 `repeat_spec` 3건 · `parse_failed` 2건(재시도까지 실패 · #550) · 셸 절차가 반쪽이라 PowerShell + PATH 조합이 필요했다(#538). #386의 두 명제는 **반증**됐다(PoV 5회 호출 · PoC 3개 디스크 · 제출 7건). ⚠ 점수·비교표 아님(`model_checksums` 전부 0) · 하네스 fix 다리 0건 · `finish_reason` trace 0건 · 과제 `arvo:1065` 하나 · 4/220조각 | `vastai-remote` | @danielcho02 |
| 2026-10-11 | [실서빙 run 다섯 번 — 5~7단계가 이 과제에서 실모델·실도구로 처음 끝까지 돌고, 완주했는데 제출 0건](harness/2026-10-11-serving-run-arvo1065-vastai-remote.md) | **Critic(`gpt-oss-20b`)이 이 과제에서 처음 판정을 냈다**(5건) · Auditor 도구 홉이 후보당 최대 일곱 · 🔴 **`R-1 finished`로 완주했는데 채점기 제출 0건** — Critic이 Finding 5건 전부를 `need_evidence`로 내고 `_pov_targets`가 그 판정을 설계상 빼 PoV 풀이 공집합이 됐다(#604). 버려진 E2 둘은 CWE-190 `apprentice_list` OOB 읽기(0.85) · CWE-369 0 나눗셈(0.80)이고 **정답 여부는 판정 0건이라 미실측**. 기동 실측 쌍 — `gpt-oss-20b` 콜드 2회 TIMEOUT ↔ 따뜻한 **35 s**(run 값 · warmprobe 62 s · 상한 300) · `qwen3.8-27b` 콜드 TIMEOUT ↔ 따뜻한 **65·66·75 s**(n=3 · 상한 600 · 콜드 교대의 attempt 2는 147 s): #571이 300초 좌석에서도 난다는 근거다. 장부 입력 626,702 · 출력 **386,996** · `gpu_seconds` 13,868 · 디코드 23.3~23.7 tok/s(trace). run 다섯 중 셋은 **로컬 인터넷 끊김**(프록시·직결 모두 · 하네스 결함 아님), 하나는 콜드 기동 상한으로 죽었다(#571) — 죽은 run 넷에서 모델 호출 `attempts: 1`(전송 끊김 재시도 없음)과 **재개 손잡이 없음**(콜드 기동으로 죽은 run_20261010_0003이 4.02 gpu시간을 버렸다)이 드러났다. 신규 #602·#603·#604 · 재현 #507. ⚠ 점수·비교표 아님(scout 셋·auditor·pov의 `checksum` 0 · critic은 실해시) · 과제 `arvo:1065` 하나 · 4조각 · run 수치는 단일 run(기동 값은 칸별 n=1~4) | `vastai-remote` | @danielcho02 |
| 2026-10-12 | [실서빙 run — Critic을 지난 Finding에서 8단계 PoV가 처음 돌아 PoC 넷이 채점기에 닿았고, 점수는 0이다](harness/2026-10-12-serving-run-arvo1065-vastai-remote.md) | **Critic을 지난 Finding에서 8단계 PoV가 처음 돌았다**(PoV가 채점기에 닿은 것 자체는 10-08 run이 먼저 — 그쪽은 Critic 미경유 E0 풀) — Critic이 또 **6/6 `need_evidence`**인데도 8단계가 **3시간 44분** 돌아 홉 8개·PoC **4건**(16·18·30·22바이트)을 만들고 **넷 다 실채점기 `vul` 다리에 제출**됐다(`poc_records` 4행). PR #608이 보류 Finding을 PoV 풀에 들인 효과의 첫 측정이다. 🔴 **점수 0** — 넷 다 `vul_exit_code 0`(카나리 기준 무해 입력과 같다)이고 넷 다 중간 `gen`이라 `designatable: false`, 모델이 `submit`을 내기 전에 **감시자 마감(`deadline_36000s`)이 홉 9를 끊어** `final: none` · `vul_candidates: 0`. **PoC 바이트가 도구에 실제로 실린다**(`poc {harness_input}` · `input_sha256`이 그 홉 PoC 해시와 일치 — ADR 0014·PR #567) · 봉투 `PovActionNamed`(PR #611) 첫 사용. 🔴 **Auditor가 `probe`를 18호출 중 0회 썼다**(`lookup` 12 · `answer` 6 · `stop_reason` 전부 `answered`) → `cited_probe_evidence` 6/6 **0** · `p3`·`p4` 12/12 거짓 → 등급 **E2 다섯·E1 하나**(10-11은 E2 둘·E1 셋 · 10-08은 전부 E0) — 같은 run의 **PoV는 `gdb`·`build`를 골랐고 둘 다 #507로 `exit 1`**이다(#617). **선취가 콜드 기동 상한을 처음 넘겼다** — 160 GB를 약 7분에 받아 좌석 다섯이 전부 상한 안(`qwen3-14b` 146 · `seccoderx-8b` 45 · `qwen3.8-27b` 197 · **`gpt-oss-20b` 67**(10-11 콜드 2회 TIMEOUT) · `feyospace-v1-small` 76초) · **TIMEOUT 0건**(#571). 🔴 **디코드가 10-11의 0.72배**(16.8~17.5 대 23.3~24.0 tok/s · 분포 비중첩 · Edition·박스·드라이버가 섞여 **원인 미분리**, #432). 직결 ssh + `--loop` 터널로 **전송 끊김 0건**(10-11은 셋이 끊김으로 죽었다). 신규 **#616·#617·#618·#619·#620** · 재현 #507(3회째 · PoV 홉 둘 59분 33초를 태운다는 새 비용)·#563. 버려진 PoV 홉 셋이 8단계 모델 시간의 **65%**(약 $3.71). ⚠ 점수·비교표 아님(scout 셋·auditor·pov `checksum` 0) · 과제 `arvo:1065` 하나 · 4조각 · n=1 · run 전체 장부 합계는 마감 절단으로 **안 싣는다** | `vastai-remote` | @danielcho02 |
| 2026-10-15 | [GPU 없는 리허설 — arvo:1065 실번들 · `dev_docker`](harness/2026-10-15-gpu-free-rehearsal-dev-docker-arvo1065-local.md) | 🔴 **배관·정렬 리허설 · 역량 미측정**(모델 전부 Fake · 도구는 실제 Docker 이미지). 실번들로 **0~9단계가 GPU 없이 완주**했고(약 3분) 로컬 채점기에 제출 8건 · 실패 0 · `oracle_final` 1건(`sole_poc`, `success false`는 Fake라 정상). **후보 컷이 설명 일치 후보를 잘랐다** — semgrep 신호 후보 8개(`signal_artifacts 8`/47)가 컷을 채워 `funcs.c:file_regexec`(`desc_rank` 2)가 밀렸고(「Level 1은 정적 신호가 없다」는 전제가 거짓), 설명 일치 예약(`candidate_desc_reserve` 2)을 넣은 코드에서는 그 후보가 컷에 들었다(`desc_reserved` 1). ⚠ 산출물을 보존하지 않아 수치는 세션 중 trace에서 옮긴 값 · 심볼 색인 경로라 `--slices` 컷은 미실측 · n=1 · 점수 아님 | `미지정`(이 PC) | @danielcho02 |
| 2026-10-16 | [실서빙 full run — Scout·Auditor 실모델로 돌았으나 5단계 Auditor에서 비정상 종료(원인 미확정)](harness/2026-10-16-serving-run-arvo1065-vastai-remote.md) | 🔴 **완주 못 함 · 판정 A 부분 · 판정 B 없음.** vast에는 모델만, **오케스트레이터·Docker 도구·채점기는 로컬 7.4GB 노트북**에 둔 배치라 약 8.5h 만에 5단계(Auditor)에서 **비정상 종료**(PID 소실 · manifest·traceback 없음이라 Python 예외가 아니다 · 원인 미확정 — 로컬 메모리 고갈은 가설 하나 [추정]). 7(Critic)·8(PoV)·9(oracle) 미도달. 10-15 리허설과 달리 **Scout `qwen3-14b`·Auditor `qwen3.8-27b`가 실모델로 호출됨**(`model_call` 54 · Finding 8 · `SchemaParseError` 2건은 둘 다 출력 상한 절단 — run은 안 죽었으나 Auditor 1건은 76분을 쓰고 그 후보 판정을 잃었다). Critic·PoV는 호출 0이라 실모델 여부 미측정. **GT는 `candidate_max`=8이 컷** — 슬라이서는 `slice_0524`로 GT(`funcs.c:file_regexec`→`softmagic.c:match`)를 포착했으나 상위 8은 `signal_rank` 다음 경로 사전순이라 `apprentice.c` 6 · `cdf.c` 1 · `softmagic.c` 1(설명 매치 0) → 이슈 #693. 채점기는 Python 3.11에서 submit 500(`int in IntEnum`)이라 3.12.10 venv. 크레딧 $23.96→$11.32. ⚠ 미완주라 비교표 아님 · 서빙 로그 미회수 · n=1 | `vastai-remote` | @sumin0218 |

### 전처리 실험 (`preprocessing/`)

**하네스 run이 아니다.** Juliet 샘플을 전처리 형식별(source · ast · cfg · pdg · cpg ·
조합)로 바꿔 모델 셋에 직접 물은 측정이고, `cli run`을 거치지 않아 `run_id`도
`RunRecord`도 없다. 그래서 위 색인의 열(날짜 · 장비 · 작성)을 채울 값이 파일 안에 없고,
`REPORT_DIRS`도 이 폴더를 보지 않는다. **여기 걸어 두는 것은 최소한 어디서 찾는지가
남게 하려는 것이고, 하네스 보고서와 같은 서식으로 올리는 것은 별건이다.**

| 문서 | 무엇 |
| --- | --- |
| [preprocessing-methods.md](preprocessing/preprocessing-methods.md) | 실험 절차 — 모델 셋(Qwen3-8B · Foundation-Sec-1.1-8B-Instruct · Qwen3-4B, 전부 BF16), Juliet 데이터셋 구성, perturbation 방법, 평가 지표 정의 |
| [preprocessing-clean-results.md](preprocessing/preprocessing-clean-results.md) | clean 버전 결과(source만 few-shot, 나머지 zero-shot). **문서 자신이 "최종 결과는 참고용"이라고 못박는다** — source 우위가 few-shot 유무와 섞여 있어서다 |
| [preprocessing-perturbed-results.md](preprocessing/preprocessing-perturbed-results.md) | perturbed 버전 결과. 같은 조건(source만 few-shot) |
| [preprocessing-perturbed-v2-results.md](preprocessing/preprocessing-perturbed-v2-results.md) | perturbed에서 정확도가 떨어지는 원인을 좁히려고 변수·함수명만 바꾼 판. **수치는 few-shot 문서에 함께 실려 있다** |
| [preprocessing-few-shot-results.md](preprocessing/preprocessing-few-shot-results.md) | 상위 셋(source · ast+pdg · ast+cfg)을 같은 few-shot 조건으로 다시 잰 것. Qwen3-8B만 채워져 있고 나머지 둘은 TODO |
| [CLEAN-VS-PERTURBED.md](preprocessing/CLEAN-VS-PERTURBED.md) | 위를 합친 순위표 — 종합 · 강건성(clean→perturbed 변화율) · 취약 판정과 함수 지목 동시 성공률 |

**⚠ 이 여섯은 전부 미완이라고 스스로 적는다.** 각 문서의 `// TODO`가 zero-shot source 실험,
형식별 few-shot 정확도, CPG 엣지 끊김 재측정을 남은 것으로 든다. 수치를 인용할 때는 그
단서를 함께 읽는다.

## 파일 이름

`YYYY-MM-DD-<주제>-<장비 라벨>.md`

| 요소 | 규칙 | 왜 |
| --- | --- | --- |
| 날짜 | **실험 종료일** (작성일이 아니다) | 색인이 시간순이어야 대조 순서가 파일명에서 읽힌다. 첫 대조에서 갈린 것이 "누가 먼저였나"였다 |
| 주제 | **경로 이름** (`serving`, `retrieval`). 트랙 번호(`T4`)를 쓰지 않는다 | 트랙은 사람 배정이라 바뀌고 경로는 안 바뀐다 |
| 장비 | **`--device`에 주는 문자열과 같은 값** (`atom`, `seraph` — 하이픈이 든 값도 그대로, `vastai-remote`. 검사는 파일명이 `-<장비>.md`로 끝나는지 본다) | 파일명이 `RunRecord.device`로 grep된다. [evaluation.md](../evaluation.md) §2.3 규칙 5("장비가 다른 run은 같은 비교 셀에 넣지 않는다")가 파일명 수준에서 눈에 보인다 |

**ADR식 번호(`0001-`)를 쓰지 않는다.** ADR은 서로를 번호로 참조하므로(`ADR 0005`,
`§5 #5`) 번호가 식별자여야 한다. 실험 보고서는 서로를 참조하지 않고 색인이 시간순이므로
날짜가 낫고, **두 사람이 같은 날 각자 파일을 만들어도 충돌하지 않는다**(번호는 충돌한다).

## 보고서를 고치는 규칙 — 두 종류로 가른다

"원문을 고치지 않는다"를 파일 전체에 적용하면 **양식 통일이 불가능해진다.** 실제로
보호해야 하는 것은 일부다.

| 종류 | 무엇 | 누가 |
| --- | --- | --- |
| **(A) 서식 정리** | 절 순서, 메타 필드 추가, 작성자가 아는 사실 채우기, 기동 명령 원문 붙이기 | **누구나.** 주장이 바뀌지 않는다 |
| **(B) 주장 수정** | "첫 완주", "34 GiB 커널 강제" 같은 서술 | **작성자만** |

**(B)를 남이 고치지 않는 이유**는 보고서가 그 시점의 관측 기록이기 때문이다. 이 저장소는
같은 원칙을 이미 세 곳에서 실행했다.

- [ADR 0005](../decisions/0005-tool-runner-override.md)를 여는 오버라이드가 `RunRecord.tool_runner`에 **기록으로 남는다** — 지우지 않는다
- [portability.md](../portability.md) §5 — "`#`는 고정 ID다. 해소된 항목을 지울 때 뒤 번호를 당기지 않는다"
- portability.md §6 — 해소된 환경 제약을 **"지운 기록이 아니라 남긴 기록"**으로 유지

셋 다 (B)에 관한 것이다. **없는 관측을 지어내거나 있었던 관측을 지우는 것**을 막지,
있는 사실을 정리해 적는 것을 막지 않는다.

**(B)가 남의 보고서에서 필요할 때**는 본문을 고치지 않고 세 가지를 한다.

1. 파일 머리에 **편집자 주 블록**을 붙인다 — 누가 언제 붙였는지와 정정 항목의 §번호만. 추가는 문장을 바꾸는 것이 아니다
2. 정정 내용은 **아래 확정 표**가 담는다
3. **작성자에게 알린다.** 본인이 본문을 고치면 블록을 제거한다

편집자 주 블록 없이 확정 표만 두면, README를 안 읽고 보고서만 연 사람이 틀린 사실을
읽고 나간다.

## 서빙 서버 stdout을 파일로 받는다

**같은 사고를 세 번 겪었다. 셋 다 "포그라운드로 띄우고 창을 닫았다"다.**

| 언제 | 무엇을 잃었나 |
| --- | --- |
| 2026-08-18 | `bench` stdout — 측정 기록이 재구성본이 됐다([서빙 보고서](harness/2026-08-18-serving-atom.md) §7 미측정 표) |
| 2026-08-22 | `cli bench` stdout JSON — 그 run이 보고서에서 통째로 빠졌다([파이프라인 보고서](harness/2026-08-22-pipeline-atom.md)) |
| 2026-08-23 | vLLM 서버 stdout — `Prefix cache hit rate`가 유실됐다. 장부 `cached_tokens 0`의 **대조값이 없어 R6이 이 run으로는 안 움직인다**([zlib 보고서](harness/2026-08-23-pipeline-zlib-atom.md) §4 해석 ⑦) |

**[TEMPLATE.md](TEMPLATE.md)에 "원문 산출물 보존 위치" 필드가 이미 있었는데 세 번 다
안 지켜졌다.** 필드는 보고서를 쓰는 시점에 읽히고 사고는 서버를 띄우는 시점에 난다 —
**적어 두는 자리와 지켜야 하는 자리가 다르다.** 그래서 규약을 여기 둔다.

**2026-08-25에 처음 보존됐다.** [542건 보고서](harness/2026-08-25-pipeline-zlib542-atom.md)의
`run_20260825_0001/serve_20260825_125430.log`가 그것이고, 같은 run에서 8/23이 잃었던
`Prefix cache hit rate: 30.5%`가 실제로 남아 R6에 첫 대조값이 생겼다.

**다만 방법이 `tee`가 아니었다.**

```bash
setsid nohup <서버 기동 명령> > "$LOG" 2>&1 &
```

- **`tee`로는 부족하다.** 세 사고의 원인은 "출력을 파일로 안 받았다"가 아니라 **"포그라운드로
  띄우고 창을 닫았다"**이고, `tee`는 앞의 절반만 막는다. 창을 닫으면 파이프라인 전체가
  SIGHUP을 받아 **서버가 같이 죽는다** — 그러면 로그는 남아도 run이 끝나지 않는다.
  `setsid nohup ... &`는 **보존과 생존을 함께** 준다. 셋 다 걸린 지점이 정확히 생존 쪽이었다.
- `$LOG`는 run 산출물 옆이 낫다. 서버 로그와 run이 갈라지면 어느 run의 로그인지 사후에
  못 맞춘다. 8/25는 `artifacts/logs/`에 받아 run 종료 후 run 디렉터리로 복사했다.
- **코드로 강제하지 않는다.** 서버 기동은 하네스 밖이고, 강제하려면 하네스가 서버를
  띄워야 하는데 그것은 별개의 설계다. 지금은 규약이고, 네 번째 사례가 나오면 그때
  강제하는 쪽을 다시 본다. **8/25가 그 네 번째가 아니다** — 규약을 지켰더니 남았다.
- 보고서에는 그 경로를 적는다. **못 받았으면 `보존하지 않았다`고 적는다** — 그것도 값이다.

## 채울 수 없는 필드는 `미기재`로 남긴다

지어내지 않는다. **빈칸이 그 자리에서 템플릿의 필수 필드가 왜 필요한지를 증명한다** —
확정 #2가 "revision 미기재가 오독의 원인"이라고 적고 있고, 그 공백이 실제로 오독을 낳았다.
그 revision 칸은 2026-08-22 미확정 #3으로 채워졌지만(스냅샷 해시), **빈칸이 필수 필드의
필요성을 증명한 사례**로 남는다.

## ADR·registry와의 경계

셋이 겹치는 것처럼 보이지만 담는 것이 다르다.

| 어디 | 무엇의 정본인가 |
| --- | --- |
| `config/models.yaml`의 `benchmark:` | **수치 그 자체.** `bench --apply`가 쓴다 |
| `docs/decisions/` ADR | **그 수치의 뜻** — 무엇을 쟀고 무엇을 안 쟀는지. [model-serving.md](../model-serving.md) §3이 "측정 결과는 `docs/decisions/`에 ADR로 남긴다"로 절차를 정해 뒀다 |
| `docs/experiments/` (여기) | **그 측정이 일어난 세션 전체.** 막힌 것, 기동 조건, 실패한 시도, 정리 상태 |

겹치는 부분은 보고서가 ADR·registry를 **링크하고 값을 복제하지 않는다.**

---

## 확정된 사실

**#1~#5는 2026-08-18 두 보고서를 대조해 확정했고, #6은 이후 run이 쌓여 확정됐다.** 각 행은
근거를 함께 적는다 — 근거 없는 확정은 다음 대조에서 또 갈린다.

> 저장소 문서를 가리킬 때는 **`main`에 있는 것만 링크한다.** 아직 열린 PR에 있는 것은 PR
> 번호로 적는다 — ATOM 첫 실측의 ADR 정본은 [0007](../decisions/0007-atom-qwen3-4b-first-measurement.md)이며 PR #15로 `main`에 머지됐다.

### #1 실모델 파이프라인 첫 완주는 서빙 쪽이다

**@danielcho02가 약 18시간 먼저다** — 2026-08-18 00:30경 KST vs 같은 날 저녁.
[검색 보고서](harness/2026-08-18-retrieval-atom.md) §1의 "실모델 파이프라인 첫 완주"는
**"검색 경로를 포함한 실모델 완주가 처음"**이 정확한 주장이다. 그 완주 자체는 사실이고,
성립 범위만 좁아진다.

| 근거 | |
| --- | --- |
| 서빙 쪽 시각 | `bench`의 `measured_at: 2026-08-17T15:16:27Z` = **2026-08-18 00:16:27 KST** ([서빙 보고서](harness/2026-08-18-serving-atom.md) §7.2) |
| 검색 쪽 시각 | "2026-08-18 저녁" — **분 단위 미기재** |
| 00:30이라는 값 | 대조 시 확정. **원 보고서에 분 단위 종료 시각이 없다** — [TEMPLATE.md](TEMPLATE.md) 첫 필드가 생긴 계기다 |

### #2 Finding 1 / 0 / 1은 모순이 아니다

**서빙 모델과 실서빙 대상이 서로 다르다.** 세 run은 같은 `single_gpu_12g` 프로필을 썼지만
같은 실험이 아니었다.

| run | 서빙 모델 (실제) | revision | 실서빙 대상 | Finding |
| --- | --- | --- | :-: | :-: |
| [서빙](harness/2026-08-18-serving-atom.md) §8.2 | `Qwen3-4B-Instruct-2507-FP8` | `8591804019c8…` | scout | **1** |
| [검색](harness/2026-08-18-retrieval-atom.md) §3.3 | `Qwen3-0.6B` (bf16) — 별칭이 `qwen3-4b`였다 | `c1899de289a0…` | scout | **0** |
| [검색](harness/2026-08-18-retrieval-atom.md) §4.4 | `Qwen3-Embedding-0.6B` + `Qwen3-Reranker-0.6B` | `97b0c614be4d…` · `e61197ed4502…` | retriever (scout는 Fake) | **1** |

**오독의 원인은 보고서에 실제 모델과 revision이 없었다는 것이다.** `--served-model-name`은
별명이라 근거가 못 된다 — 0.6B를 `qwen3-4b`라는 이름으로 서빙한 기록이 4B의 결과로 읽혔다.
그래서 [TEMPLATE.md](TEMPLATE.md)가 별칭과 실모델을 **함께** 요구한다.

### #3 sm_121 기동 조건은 서로 독립인 세 개다

> **2026-09-02 — ②의 서술이 보편명제로는 반증됐다.** 30B(NVFP4)를 띄웠을 때 기동 로그가
> `Using FLASHINFER attention backend out of potential backends: ['FLASHINFER',
> 'TRITON_ATTN']`이다 — **flashinfer가 실제로 선택됐는데** `VLLM_ATTENTION_BACKEND`를 주지
> 않았고 서버가 떠서 bench 두 판을 실패 0으로 완주했다. ②가 "flashinfer가 선택될 때
> 필요"라고 적고 있으므로 이번은 발동 조건을 만족한 사례다. **왜 안 깨졌는지는 안 갈랐다**
> (nvcc 유무 · vLLM 0.27.1의 프리빌트 커널 · 이 dtype 경로가 JIT를 안 타는지).
> 조건 ①·③은 이번에도 유지했다. 근거는
> [동시성 재측정 보고서](harness/2026-09-02-bench-concurrency-atom.md) §3.4.

두 보고서가 서로 다른 조합을 적었으나 **모순이 아니다.** 조건이 셋이고 각자 다른 것을
막는다.

| # | 조건 | 언제 필요한가 | 근거 |
| :-: | --- | --- | --- |
| ① | `CPATH=$HOME/pyhdr/...` | **항상** — Triton이 실행 시점에 `Python.h`를 참조한다 | [서빙](harness/2026-08-18-serving-atom.md) §2.3a, [검색](harness/2026-08-18-retrieval-atom.md) §2.2 |
| ② | `VLLM_ATTENTION_BACKEND=TORCH_SDPA` | **flashinfer가 선택될 때** — 그 커널 JIT에 nvcc가 필요한데 권한 밖이다 | [검색](harness/2026-08-18-retrieval-atom.md) §5 |
| ③ | `VLLM_USE_DEEP_GEMM=0` | **fp8 양자화 모델일 때** — DeepGEMM이 scale-factor 레이아웃을 변환 못 한다 | [서빙](harness/2026-08-18-serving-atom.md) §5.2~5.3 |

> **2026-08-25 — ②가 왜 안 걸렸는지가 처음으로 로그에 직접 나왔다.**
> [542건 보고서](harness/2026-08-25-pipeline-zlib542-atom.md)의 기동 로그가
> `Using FLASH_ATTN attention backend out of potential backends: ['FLASH_ATTN',
> 'FLASHINFER', 'TRITON_ATTN', 'FLEX_ATTENTION']`이다 — **flashinfer가 후보에 있었는데
> 선택되지 않았다.** ②의 발동 조건이 "flashinfer가 선택될 때"이므로 이 run에서는
> `VLLM_ATTENTION_BACKEND`를 주지 않은 것이 맞고, 실제로 주지 않았다.
>
> **8/22 관측보다 한 겹 낫다.** 그때는 `/proc/<pid>/environ`에 그 변수가 **없다**는 것까지만
> 봤고(미확정 #2), 왜 없어도 됐는지는 추론이었다. 이번엔 **선택된 백엔드 이름 자체**가 로그에
> 있다. → **fp8 4B + vLLM 0.27.1 + sm_121 조합에서 ②는 필요 없다.** bf16 0.6B는 여전히 모른다.

**`--enforce-eager`는 필수가 아니다.** [서빙 보고서](harness/2026-08-18-serving-atom.md) §5.3의
기동 로그가 `--enforce-eager` 없이 나왔고, torch.compile이 19.60초 수행되고 CUDA graph
35개가 잡혔다. 검색 쪽이 그것을 쓴 것은 틀린 것이 아니라 **①을 우회하는 다른 방법**을
함께 적은 것이다(torch.compile을 안 하면 헤더도 필요 없다).

### #4 메모리 한도 — 34→64(2026-08-25 PL 공식)→80 GiB(2026-08-31 실측 확인)

> **2026-09-02 — 한도는 그대로인데 우리 프로세스가 그 안에 없다.**
> `user-1004.slice/memory.max`는 여전히 **85,899,345,920 = 80 GiB**다. 그런데 그날 vLLM의
> cgroup이 `/system.slice/ssh.service`였고 **루트까지 전 구간이 `max`**였다
> (`current` 9.35 GiB). SSH 세션이 `user.slice`의 session scope가 아니라
> `system.slice/ssh.service`에 놓였고 vLLM이 그것을 물려받았다.
> **2026-08-28에는 안에 있었다** — 미확정 #1 해소 블록의
> `0::/user.slice/user-1004.slice/session-18873.scope`가 그 기록이다. **그 사이 배치가
> 바뀌었고 언제·왜인지는 모른다.**
> **무너지는 것**: 아래 「제한 단위가 사용자다 · 팀 전체가 하나의 80 GiB를 나눠 쓴다」는
> 회계가 그 경로의 프로세스에는 성립하지 않는다. 상한이 없으므로 OOM kill 방어선도 없다.
> **안 무너지는 것**: 미확정 #1이 이미 확정한 「cgroup은 GPU 할당을 못 막는다」 — 큰 할당은
> 8/28에도 밖이었으므로 실질 방어는 원래부터 `--gpu-memory-utilization` 값 자체였다.
> **그래서 서빙 전 확인은 「`memory.max`를 읽는다」가 아니라 「내 프로세스의 cgroup을 끝까지
> 올라가 읽는다」여야 한다.** 말단만 읽으면 `max`가 나온다.
> 근거는 [동시성 재측정 보고서](harness/2026-09-02-bench-concurrency-atom.md) §3.3.

> **2026-08-31 갱신 — 64 → 80 GiB로 재확장(on-box 실측 확인).** 팀이 확장을 안내했고 팀이 `user-1004.slice/memory.max` = **85,899,345,920 = 80 GiB 정확**임을 실측했다(2026-09-01T04:39:13Z 읽기 전용 재확인에서 같은 값). **`memory.high`가 `max`인 것은 이번 확장으로 바뀐 것이 아니다** — 8/25의 58 GiB(62,277,025,792)에서 풀렸고, [8/26 재측정](harness/2026-08-26-pipeline-zlib542-remeasure-atom.md) §2가 그 시점 `memory.max` **64**와 함께 적었으며 [8/28 track_a](harness/2026-08-28-pipeline-track-a-pwn003-atom.md) §2가 두 번째로 확인했다. 따라서 **늦어도 8/26 재측정 시점부터는 이미 감속 완충이 없었고**(8/25 두 보고서는 아직 58을 실측했다 — 정확히 언제 풀렸는지는 기록에 없다), 이번 확장이 바꾼 것은 그 상한이 64 → 80으로 오른 것뿐이다. `memory.high`는 넘으면 프로세스를 감속(throttle)시킬 뿐 **죽이지는 않는** 완충인데 그게 없으므로 **상한에 닿는 순간 곧바로 OOM kill**이다. 운용 방어선을 스스로 잡는다(예: `max`의 0.9 ≈ 72 GiB). **아래 2026-08-20 실측 표의 64/58은 그 시점 스냅샷이고 이 갱신이 대체한다** — 값은 바뀌므로 서빙 전에 `memory.max`/`memory.high`를 다시 읽는다.

**두 보고서 모두 34 GiB를 시스템이 강제하는 하드 한도처럼 다뤘는데 그게 틀렸다.** 검색 쪽
§2.1은 "커널 강제"라 했고, 서빙 쪽은 시스템 제약처럼 썼다. 실제 cgroup `memory.max`는
**64 GiB**(2026-08-19·08-20 재실측 동일)이고, 34 GiB는 시스템 강제 한도가 아니라 멘토가
안내한 팀 배정 목표(약속)였다.

**PL이 메모리 할당을 34 → 64 GiB로 상향했고, 2026-08-25 팀 회의에서 이를 공식 확인했다.**(cgroup
`memory.max`는 2026-08-19에 이미 64로 반영됐고 — 아래 실측 표 —, 오늘 회의에서 그 상향분이
공식화됐다.) **그때** 초기 목표(34)·시스템 하드 한도(`memory.max` 64)·공식 할당이 모두
**64 GiB로 정렬**됐었다 — 단 2026-08-17에도 목표·한도가 34로 우연히 일치했다가 갈렸으므로,
이 정렬도 우연일 수 있어 아래 "실행 시점에 읽는다"가 그대로 유효하다. 34는 초기 목표로 해소된다 —
더는 34 안에서 여유를 둘 이유가 없고, 운용은 **당시** 공식 64 GiB 안에서 사이징했다(방어선은
`memory.high` 58 아래 — **둘 다 지금 값이 아니다. 위 2026-08-31 갱신 블록과 아래 운용 항을 본다**). 2026-08-17엔 `memory.max`도 34였다가 이후 64로 상향됐으므로 **하드
한도 값은 바뀌어 왔다 — 실행 시점에 읽는다.**

`/sys/fs/cgroup/user.slice/user-1004.slice/` — **부모 slice에 걸려 있다. 말단 session
scope는 `max`라 그것만 보면 "제한 없음"으로 오판한다.** 2026-08-20 21:52 KST 실측:

| 기준 | 값 | util (분모 119.7 GiB) | 넘으면 |
| --- | --- | :-: | --- |
| `memory.high` | 58 GiB | 0.48 | throttle (회수 압박) |
| `memory.max` | 64 GiB | 0.53 | **OOM kill** |
| 스크립트 초기 기본값 | 71.8 GiB | 0.60 | **즉시 OOM kill** |

> **2026-08-17 스냅샷(PR #13 실측):** `memory.max`=34 GiB(`36507222016`), `memory.high`=30 GiB.
> 멘토 목표와 우연히 일치했으나 이후 64/58로 상향됐다 — 하드 한도는 바뀌므로 서빙 전에 다시
> 읽는다. **2026-08-20 21:52 KST 재실측:** max 64 / high 58 / current 52.1 GiB 사용 중
> (throttle까지 6 GiB — 팀이 이미 상당히 차 있다).

- **운용은 공식 80 GiB(2026-08-31 실측) 안에서 사이징한다. `memory.high`가 `max`로 풀려 소프트 스로틀이 없으므로 하드 방어선을 스스로 잡는다**(예: `max`의 0.9 ≈ 72 GiB). 전에는 high 58이 자동 완충이었으나 지금은 없어 예고 없이 OOM된다.
  공용 계정이라 이미 상당히 차 있을 수 있으므로(2026-08-20 재실측 current 52.1 — 80 재확장 후 재실측 필요) 보수적으로 잡는다.
  util은 물리값이 아니라 **실행 시점 `memory.max` 기준**으로 사이징한다(80이면 하드 util 0.67 = 80/119.7; 스로틀이 없으니 회피선은 직접 정한다).
  이전 '34 안에서 `util ≤ 0.25`' 지침은 34 목표 해소로 함께 풀렸다.
- `cpu.max = max 100000` — **CPU는 제한 없다.** nproc 20이 실제로 다 쓰인다. "6코어 상당"은 강제되지 않는다.
- **제한 단위가 세션이 아니라 사용자(`user-1004`)다. 계정 공유라 팀 전체가 하나의 80 GiB를 나눠 쓴다.** 그래서 [TEMPLATE.md](TEMPLATE.md)가 "동시에 떠 있던 다른 서버"를 요구한다.

**`--gpu-memory-utilization`의 분모는 119.7 GiB(물리 통합메모리)이고 cgroup과 다른 축이다.**
vLLM 기동 로그가 직접 답했다 — `Free memory on device (109.37/119.7 GiB)`,
`Desired GPU memory utilization is (0.2, 23.94 GiB)`. **vLLM은 cgroup을 모른다.**

### #5 검색 쪽의 `--tool-runner podman`은 불필요한 오버라이드였다

판정은 그대로다 — 필요 없는 오버라이드가 `RunRecord.tool_runner`에 남아
[ADR 0005](../decisions/0005-tool-runner-override.md) 예외를 열었다.
**다만 원인이 문서 불일치가 아니다.**

| 처음 의심한 원인 | 실제 |
| --- | --- |
| `profiles.yaml`이 `docker`인데 `tools/README.md`가 "podman"이라 어긋났다 | **어긋나지 않았다.** `local_balanced`·`b1_single`은 [profiles.yaml](../../config/profiles.yaml)에서 이미 `podman`이고(PR #11) 문서도 일치한다 |
| — | 검색 실험이 쓴 프로필은 그 둘이 아니라 **`single_gpu_12g`**이고, 그 프로필은 **`tool_runner: fake`**다. 도구를 실제로 돌리려면 오버라이드가 **구조적으로 필요했다** |

**문서를 고쳐도 이 오버라이드는 사라지지 않는다.** 그리고 ADR 0005는 오버라이드의 범위를
"프로필이 선언한 런타임이 **그 장비에 없을 때**"로 못박았는데, `fake` → `podman`은
**그 ADR이 상정하지 않은 용도**다. 미확정 #4가 이 항목이다.

### #6 ATOM에는 podman과 docker가 **둘 다** 있다 — "podman만"이 틀렸다

**podman 쪽은 새 사실이 아니다.** [portability.md](../portability.md) §6 장비 표가
`atom`의 런타임을 **`podman 4.9.3` (rootless)**로 이미 적고 있고, 2026-08-25 ATOM에서
다시 확인했다(팀). 그러므로 [profiles.yaml](../../config/profiles.yaml)의
`local_balanced`·`b1_single`이 선언한 `tool_runner: podman`은 **그 장비에서 실제로 성립한다.**

**틀린 것은 배타 서술 쪽이다.** 같은 두 자리가 podman을 **유일 런타임**으로 적는다.

| 어디 | 그때 문장 | 무엇과 어긋났나 | 지금 |
| --- | --- | --- | --- |
| [portability.md](../portability.md) §6 `atom` 행 | `podman 4.9.3` **(rootless. Docker 데몬 접근·singularity 둘 다 없음)** | `--tool-runner docker`가 ATOM에서 **세 번 실제로 돌았다** | **정정됨** — `docker 29.2.1 + podman 4.9.3` 공존으로 고치고 2026-08-21 실측과 이 run들을 근거로 달았다 |
| [profiles.yaml](../../config/profiles.yaml) `local_balanced` 주석 | "ATOM에는 **podman만 있으므로**(2026-08-17 실측)" | 위와 같다 | **정정됨 — 값도 `docker`로 되돌렸다**(2026-08-28). 아래 참조 |

**2026-08-28에 셋을 전부 정리했다.** `src/config.py`·`src/adapters/factory.py`의 같은 반증
서술을 고쳤고, 새 2x2 프로필 넷을 `docker`로 선언했고, **`local_balanced`·`b1_single`의
선언값도 `podman` → `docker`로 되돌렸다.**

**"값은 유지" 쪽을 지탱하던 근거가 사실이 아니었다.** 그 근거는 "`podman`으로 이미 돈
run이 있어 지금 갈면 digest가 갈린다"였는데, `artifacts/`의 run 14건을 전수 조회하니
podman으로 도구를 돌린 run은 `run_20260817_0001` 하나이고 그것은 `model_checksums`가 전부
0인 dev 프로필이다(`dev_docker --tool-runner podman`). **두 프로필로 podman을 써서 돈 run은
0건이라 갈릴 기록이 없었다.**

**오히려 선언이 기록과 어긋나 있었다.** `b1_single`로 돈 run 둘
(`run_20260827_0001`·`0004`)이 `RunRecord.tool_runner: docker`다 — `--tool-runner` 오버라이드를
달고 돌았고, 그것이 [evaluation.md](../evaluation.md) §2.3 규칙 2가 금지한 예외다.
경위와 판단은 [decisions/0005](../decisions/0005-tool-runner-override.md) 마지막 절.

**근거는 세 run의 도구 실행이다.** 셋 다 `--device atom --tool-runner docker`이고 도구가
실제로 돌아 `tool_images`가 채워졌다.

| run | 보고서 | `tool_call` |
| --- | --- | --- |
| `run_20260821_0004` | [2026-08-22](harness/2026-08-22-pipeline-atom.md) | 9건 |
| `run_20260823_0002` | [2026-08-23](harness/2026-08-23-pipeline-zlib-atom.md) | 238건 |
| `run_20260825_0001` | [2026-08-25](harness/2026-08-25-pipeline-zlib542-atom.md) | 238건 |

**두 런타임이 서로 다른 값을 낸다는 것도 기록에 있다.** portability.md §5 #4의 digest 표가
같은 `harness-tools:v1`에 대해 podman/aarch64 **RepoDigest** `sha256:3a63ba2e…`를 적었는데,
8/25 run의 docker/aarch64 값은 `sha256:b5d7048b…`(docker 29.x `RepoDigests`)로 **다른
해시**다. 같은 런타임의 같은 이미지였다면 나올 수 없는 대비다.

> **정정 대상 두 곳은 이 PR에서 건드리지 않았다.** portability.md §6은 장비 표이고
> profiles.yaml은 `config/` 변경이라, 이 저장소가 반복해 세운 "config는 별도 PR" 선을
> 넘는다. **여기 확정 표가 그 자리를 메운다** — 이 README가 "이후 대조로 정정된 것은 여기
> 표가 담는다"고 적은 그 용도다. 두 문장을 실제로 고치는 것은 후속 PR이다.

> **⚠ 정정 대상은 둘이 아니라 넷이었다 (2026-09-09에 확인).** 위 표가 센 두 자리 말고도
> [tools/README.md](../../tools/README.md)의 「ATOM 빌드·실행」 절과
> [tests/test_container_tools.py](../../tests/test_container_tools.py)의
> `test_podman_argv_matches_docker_except_the_binary` docstring이 같은 배타 서술을 들고
> 있었다. 위 두 자리는 2026-08-28에 고쳐졌는데 **이 둘은 목록에 없어서 그대로 남았다** —
> `tools/README.md` 쪽에는 실행 가능한 명령까지 있었다(`--profile b1_single`을 "podman이
> 선언돼 있으니 플래그가 필요 없다"로 설명하는 줄). **CLAUDE.md §2.3의 살아 있는 사례다** —
> 정정 대상을 기억이 아니라 `git grep`으로 세었다면 목록이 처음부터 넷이었다.

**뜻은 두 갈래다.** ① `local_balanced`·`b1_single`을 ATOM에서 돌릴 때 오버라이드가
필요 없다. **⚠ 이 문장은 원래 그 근거를 "(선언대로 podman이 있다)"로 달고 있었는데, 같은
절이 바로 위에서 그 선언값을 `docker`로 되돌렸다고 적는다** — 결론(오버라이드 불필요)은
그대로지만 근거는 "ATOM에 podman이 있다"가 아니라 **"ATOM에 docker가 있다"**다(2026-08-21).
② 그러나 **"docker가 없어서 podman을 골랐다"는 근거는 성립하지 않는다** — 미확정 #4가 묻는
`single_gpu_12g`의 런타임 선언을 다시 볼 때, 선택지가 "podman뿐"이 아니라 "둘 중 하나"다.

---

### #7 NVFP4는 sm_121에서 추가 환경변수 없이 뜬다 — 그러나 portability §5 #1은 닫히지 않는다

> **2026-09-04 — 아래 「남은 1개」 행의 서술이 반증됐다. 그리고 그 모델은 NVFP4가 아니었다.**
> [다섯 상주 보고서](harness/2026-09-04-five-model-residency-atom.md) §7이
> `devstral-small-2-24b`를 revision `55c5b41e98c2dbd21b0c8afffc540dcfc9eb5128`으로 8003에
> 띄웠다. **「띄우지도」는 거짓이다** — 띄운 것은 2026-09-04가 처음이다.
>
> ⚠ **「받지도」는 그만큼 깨끗하지 않다. 같은 PR 안에서 이 표와 08-25 보고서의 갱신 블록이
> 서로 다르게 읽는다.** §7이 본 것은 **2026-09-04에 조회한 HF 캐시의 스냅샷 날짜가
> 2026-08-25였다**는 것이고, 그것이 그날 실험 **전**에 받아진 것인지는 저장소로 가르지 못한다.
> 그리고 08-25 보고서의 그 행이 걸려 있던 대상은 당시 registry가 `nvfp4`로 라벨한 항목인데,
> **그 nvfp4 빌드(`Firworks/…-nvfp4`)는 지금도 안 받았다** — `config/models.yaml`의
> 그 모델 주석이 *"이름만 확인. 받지도 띄우지도 않았다"*로 이 PR 안에서 그렇게 적는다.
> 실제로 캐시에 있었고 떴던 것은 **fp8 빌드**로 **다른 산출물**이다.
>
> **그래서 「두 항 모두 거짓」이라고 쓰지 않는다** — 캐시에 있었다는 관측이 지지하는 것은
> 거기까지다(§0). 08-25 보고서에 붙인 갱신 블록도 같은 이유로 「받지도」를 반증으로 적지
> 않았고, **이 표가 정본이므로 그 어긋남을 여기 남긴다.** 가르려면 그 캐시 항목이 언제
> 만들어졌는지를 재야 한다 — 저장소 안에 그 증거가 없다.
> **양자화가 `nvfp4`가 아니라 `fp8`이다.** 근거 셋: `config.json`의
> `quantization_config.quant_method == 'fp8'` · vLLM 기동 로그 `quantization=fp8` ·
> 파일 크기 18,921 MiB + 5,678 MiB = 24.0 GiB(24B × 1바이트. bf16이면 약 48 GiB여야 한다).
> **멀티모달이기도 하다** — `architectures: ['Mistral3ForConditionalGeneration']`이고 vLLM이
> `PixtralForConditionalGeneration`으로 해석했다.
> **이 절의 제목("추가 환경변수 없이 뜬다")을 이 모델로 확장하지 않는다.** 그 기동은
> 전 기동 공통 환경변수(`VLLM_USE_DEEP_GEMM=0` 등)를 건 상태였고, 그 위에 mistral 네이티브
> 포맷용 로더 인자(`--tokenizer-mode mistral --config-format mistral --load-format mistral`)가
> 따로 필요했다. **"NVFP4 3종 중 3종을 쟀다"로도 읽지 않는다** — 잰 것은 NVFP4가 아니다.
> **`source`·`checksum`은 실험 시점에 비어 있었고, 서버를 내린 뒤에 채웠다.** 떠 있는 동안
> 24 GiB를 읽으면 페이지 캐시가 차서 다른 서버를 죽이므로(같은 보고서 §4.3·§7) **순서가
> 제약이었다** — 그 보고서 §7의 ⚠(*"checksum은 아직 안 만들었다"*)는 **실험 시점의
> 기록**이고 그대로 맞다. **지금 registry에는 둘 다 들어 있다** — `config/models.yaml`의
> `devstral-small-2-24b`이고 manifest는 `config/checksums/devstral-small-2-24b.txt`다.
> **그래서 그날 산출물에는 자리표시자가 남아 있다** — 같은 보고서 §10의 `doctor` 화면이
> `critic(devstral-small-2-24b, source=미상)`에 checksum 자리표시자 WARN을 낸 것은
> 채워지기 **전**의 출력이다.

**두 문장을 갈라야 한다. 붙이면 정본에 거짓이 들어간다.**

| | 무엇 | 상태 |
| --- | --- | --- |
| **닫힌 것** | `config/models.yaml`의 `qwen3-4b` 주석이 열어 둔 문장 — "다른 양자화(`nvfp4`)도 같은 종류의 백엔드 문제를 따로 만날 수 있다" | **닫혔다.** NVFP4 2종이 `VLLM_USE_DEEP_GEMM=0` 같은 것 없이 떴다 |
| **닫히지 않은 것** | [portability.md](../portability.md) §5 #1 — `local_balanced`이 참조하는 모델 5개 중 3개가 Blackwell 이상을 요구한다 | **미해결 그대로** |

**왜 닫히지 않는가**: §5는 "하네스의 어느 부분이 **어느 환경에서 못 도는가**" 목록이고
#1의 "못 도는 환경" 칸은 **`Blackwell(sm_100+) 미만`**이다. ATOM은 **sm_121 = Blackwell**
이므로 거기서 떴다는 것은 **#1을 확인하지 반증하지 않는다.** 해법("같은 모델의 다른
양자화를 별도 model key + 별도 프로필로 둔다")도 그대로다.

| 근거 | |
| --- | --- |
| 기동 로그 | `Using FlashInferCutlassNvFp4LinearKernel for NVFP4 GEMM` · `arch=sm121` ([NVFP4 보고서](harness/2026-08-25-serving-nvfp4-atom.md) §3·§4) |
| 3개 중 몇 개를 쟀나 | **2개.** `qwen3-8b`·`qwen3-coder-30b-a3b` |
| 남은 1개 | ~~**`devstral-small-2-24b` 미검증** — 받지도 띄우지도 않았다. registry `source`는 `null` 유지~~ → **2026-09-04에 전부 반증됐다**(이 절 머리의 갱신 블록). 그날 8003에 떴고, 서버를 내린 뒤 `tools/model_checksum.py`로 `source`·`checksum`을 채웠다. **다만 뜬 것은 `nvfp4`가 아니라 fp8 빌드다** — #1이 세는 3개 중 나머지 1개(nvfp4)는 여전히 미검증이다 |

### #8 `kv_cache_dtype`은 모델마다 다르고 하네스 기록에 없다

**조건 비교에서 갈리는 항목이다. 같은 장비·같은 프로필이어도 이 값이 다르다.**

| 모델 | 양자화 | `kv_cache_dtype` | 근거 |
| --- | --- | --- | --- |
| `qwen3-4b` | `fp8` | **bf16** | [542건 보고서](harness/2026-08-25-pipeline-zlib542-atom.md) |
| `qwen3-8b` | `nvfp4` | **fp8_e4m3** | [NVFP4 보고서](harness/2026-08-25-serving-nvfp4-atom.md) §3 |
| `qwen3-coder-30b-a3b` | `nvfp4` | **fp8_e4m3** | 같은 보고서 §4 |

**서버가 체크포인트를 보고 자동으로 정한다.** 하네스가 고르지도 기록하지도 않으므로
`RunRecord`에 남지 않는다 — `top_k` 미기록([open-questions](../open-questions.md) D29)·
컴파일 플래그(D32)와 같은 종류의 교란변수다. 두 보고서의 메모리·품질 수치를 나란히 놓을
때 이 행을 먼저 본다.

### #9 구조화 출력 스키마에 자리를 내는 것만으로는 그 필드가 안 나온다 — 프롬프트가 시켜야 나온다

**2026-08-29 확정.** [v2 스모크 보고서](harness/2026-08-29-prompt-v2-smoke-atom.md) §3·§6.
같은 서버 프로세스(pid 3663640)에서 세 관측이 한 방향을 가리킨다.

| 조건 | `trigger_status` | 근거 |
| --- | :-: | --- |
| v1 프롬프트 (run B) | `null` ×6 | 스키마에 필드가 있고 프롬프트가 안 묻는다 |
| v2 프롬프트 (v2 스모크) | `triggerable` ×6 | `prompts/auditor/v2/prefix.md` 36~42행이 다섯 값을 정의한다 |
| **프롬프트 없이 스키마만** (서버 직접 프로브) | **안 냈다** | `strict: true`인데도 선언된 optional 4개를 다 안 냈다 |

**프로브가 이 확정의 핵심이다.** 낸 10개가 required 8 + optional 2이고 안 낸 넷은 전부
default를 가진 optional이다 — `strict`가 required를 어긴 것이 아니라 **모델이 optional을
프롬프트 없이는 안 낸다.**

**왜 규약으로 올리는가**: 스키마에 필드를 넣고 채움률을 재면 0이 나오는데, 그 0을 "모델이
못 낸다"로 읽는 오독이 [open-questions.md](../open-questions.md) D52를 열게 한 사건이었다.
**필드를 신설할 때 프롬프트와 스키마를 같은 PR에 넣는다** — PR #53(스키마)과 #54(프롬프트)가
갈려서 그 사이에 도는 run이 전부 `null`이었다. 지금은 `config/models.yaml`이 v2를 안 골라
같은 상태가 이어지고 있다(D54).

### #10 `bench`의 배치 처리량은 몇 번째 판이냐에 달려 있다 — 캐시를 끄면 이득이 1.81배다

**같은 서버·같은 인자로 돌린 세 판의 `tok_s_batch32`가 59.45 / 68.64 / 107.32(1.81배)로
갈렸고, 원인은 산포가 아니다.**

| 동시성 | 1판 적중률 → tok/s | 2판 | 3판 | 4판 (캐시 OFF) |
| :-: | --: | --: | --: | --: |
| 1 | 0.9996 → 52.96 | 0.9996 → 53.06 | 0.9996 → 52.36 | `None` → **21.96** |
| 8 | **0.6356** → 62.61 | **0.9996** → 118.33 | **0.9996** → 121.05 | `None` → **35.23** |
| 16 | **0.5142** → 58.33 | **0.9535** → 128.51 | **0.8842** → 106.36 | `None` → **39.77** |
| 32 | **0.5142** → 59.45 | **0.7776** → 68.64 | **0.8577** → 107.32 | `None` → **39.64** |

**4판이 인과를 확정했다.** `--no-enable-prefix-caching`으로 다시 띄우자 **동시성 1이
52.36 → 21.96으로 2.42배 떨어졌다** — 측정 전에 박아 둔 예측이고, 안 떨어졌다면
적중률↔tok/s 인과가 배치에만 성립한다는 뜻이었다.

**그래서 배치 크기 예산의 근거가 되는 곡선은 이것뿐이다**: 21.96 → 35.23 → **39.77** →
39.64. **이득 1.81배, 동시성 16에서 포화**(32가 16과 0.3% 차). 캐시 ON 세 판의
1.18~2.42배는 전부 캐시 온도였다.

`_one_level`이 수준마다 남기는 `prefix_cache_delta`다. **동시성 8·16·32 각각에서 세 판을
적중률로 정렬하면 tok/s 정렬과 정확히 같다 — 아홉 칸에 역전이 0건이다.** 그리고 적중률이
세 판 모두 같은 유일한 수준(동시성 1)만 tok/s 산포가 1.34%다. 세 판 모두 네 수준 전부
실패 0건이라 실패가 섞여서 생긴 차이도 아니다.

**원인**: `bench`의 `variant`는 **한 판 안에서만** 본문을 흔든다. seed가 같으면 다음 판의
프롬프트가 앞 판과 동일해 **두 번째 판은 자기 자신을 캐시에서 읽는다.** `bench.py`의
`_filler` docstring이 이 왜곡을 이미 경고한다 — *"본문까지 완전히 같으면 … C(처리량)가
운영보다 낙관적으로 나온다"* — 다만 그 장치가 막는 것은 판 **안**이지 판 **사이**가 아니다.

**따라 나오는 것**: `config/models.yaml`의 `tok_s_batch32`는 **몇 번째 판이냐의 함수**이고
registry에는 그 정보가 없다. `--apply`가 쓴 68.64는 적중률 0.7776짜리 데워진 값이고,
1판의 59.45(0.5142)가 운영에 더 가깝다 — **어느 것도 운영 처리량은 아니다.**
**그래서 registry의 `tok_s_batch32`는 0으로 되돌렸다**(미실측 표시) — 필드에는 조건을
적을 칸이 없어, 값을 두면 다음 사람이 조건 없는 처리량으로 읽는다.
**⚠ 이 문장은 2026-09-04에 더 이상 현재가 아니다.** #133이 `prefix_hit_ratio_batch32`를
넣어 "조건을 적을 칸"이 생겼고, 그래서 같은 날 registry에 `66.42`(적중률 0.5673)가
들어갔다. **위 관측(59.45 / 68.64 / 107.32와 그 적중률)은 그대로 유효하다** — 바뀐 것은
값을 비워 두던 이유뿐이다. 아래 「운영 처리량의 실제 값」이 미확정인 것도 그대로다.
**그리고 안전한 운용 순서가 나쁜 쪽을 고른다**: "먼저 보고 나서 `--apply`"로 돌리면
`--apply` 판이 두 번째가 되어 데워진 값이 들어간다.
`tok_s_single`은 이 제약을 받지 않는다(세 판 다 0.9996, 산포 1.34%).
이슈는 [#128](https://github.com/Yunse4l/whitehat-boss/issues/128)이다.

**함께 반증된 것**: 옛 registry 주석의 「배치가 단일보다 느리다(54.83 → 38.93)」. 그 38.93은
실패한 호출의 벽시계가 분모에 들어간 값이었고, 실패를 없애니 두 판 모두 batch32 > single이다.
**다만 「배칭 이득이 얼마다」로 대체할 수는 없다** — 캐시 온도가 그 배수를 정한다.

**3판이 예측을 확인했다**: 동시성 8에서 2·3판이 둘 다 적중률 0.9996이고 tok/s가
118.33 / 121.05로 2.2% 안에 든다 — 데워진 상태의 처리량은 재현된다.
**남은 미확정**: 1판의 0.51~0.64 중 얼마가 템플릿 접두사 공유이고 얼마가 같은 판 앞 절이
데운 몫인가. 그리고 **운영 처리량의 실제 값** — 운영은 캐시를 켜되 후보마다 본문이 다르므로
4판(39.64, 공유 0)과 1판(59.45, 본문 일부 공유) 사이다. `--apply`가 썼던 68.64는 그 위였고
그래서 **당시** registry에 남기지 않았다(2026-09-04에 66.42가 적중률과 함께 들어갔다 —
그 값도 이 범위 위이고, 같은 축에 놓지 말라는 경고를 registry 주석이 들고 있다). 근거는
[동시성 재측정 보고서](harness/2026-09-02-bench-concurrency-atom.md) §3.1~§3.2.


## 미확정

**각 항목에 "무엇을 하면 확정되는지"를 함께 적는다.** 그것이 없는 미확정은 영원히
미확정으로 남는다.

### #1 CUDA 통합메모리 할당이 이 cgroup에 계상되는가

확정 #4의 두 겹 상한(**당시** `memory.high` 58 · `memory.max` 64 — 지금은 high `max` · max 80)이 **모델 메모리에도 적용되는지**가 아직 모른다. vLLM이 잡는 23.94 GiB가
`memory.current`에 잡히면 util 0.53에서 OOM이고, 안 잡히면 상한이 사실상 다른 곳에 있다.

**확정 방법**: 다음 서빙 때 util 0.2로 띄운 상태에서
`/sys/fs/cgroup/user.slice/user-1004.slice/memory.current`를 읽는다.
**24 GiB 근처면 계상, 4~5 GiB면 미계상이다.**
([검색 보고서](harness/2026-08-18-retrieval-atom.md) §6의 "단계별 서빙 34 GiB 실측"이 이 측정이다.)

> **2026-08-28 해소 — 미계상이다.** util **0.28**로 30B를 띄운 채 읽었다:
> `memory.current` **13,790,609,408 = 12.84 GiB** · `memory.high` **`max`** ·
> `memory.max` **68,719,476,736 = 64 GiB**. 같은 시각 vLLM 자기 회계는 **35.66 GiB**,
> `nvidia-smi`는 **32.2 GiB**다. **vLLM 프로세스가 이 slice 안에 있다는 것도 함께 확인했다** —
> `/proc/78448/cgroup` = `0::/user.slice/user-1004.slice/session-18873.scope`. util 0.28의
> 계상 기대값은 33 GiB 이상인데 실측이 그 3분의 1이고, 가중치 17.99 GiB와 미리 잡은 KV
> 12.75 GiB는 idle 여부와 무관하게 상주하므로 **계상됐다면 보였어야 한다.**
> **따라 나오는 것**: `memory.max`(64 GiB)도 `memory.high`도 **GPU 쪽 할당을 못 막는다.**
> 34 GiB 약속을 지키는 수단은 `--gpu-memory-utilization` 값 자체뿐이고, 확정 #4의
> "cgroup과 다른 축"이라는 서술이 실측으로 확인됐다.
> **남는 것**: 12.84 GiB의 구성(어느 프로세스 몫인지)과 추론 **중**의 값은 안 쟀다.
> 근거는 [track_a 보고서](harness/2026-08-28-pipeline-track-a-pwn003-atom.md) §2에 있다.
>
> **2026-09-02 — 위 결론(미계상)은 유지되지만 전제 하나가 바뀌었다.** 그날 vLLM은
> `user-1004.slice` **안에 있지 않았다**(`/system.slice/ssh.service`, 전 구간 `max`).
> 즉 「slice 안에 있는데도 계상되지 않는다」는 8/28의 대조가 이번엔 성립하지 않는다 —
> 이번 `current` 9.35 GiB는 **다른 cgroup의 값**이다. 미계상 결론 자체는 8/28 관측이
> 지탱하고 오늘 값도 같은 크기대라 흔들리지 않는다. 확정 #4의 2026-09-02 갱신을 함께 본다.

### #2 세 기동 조건을 한 조합으로 통합할 수 있는가

확정 #3의 세 조건은 독립이지만, **셋을 동시에 준 서버가 아직 없다.** 두 보고서가 각각
다른 부분집합을 썼다.

**확정 방법**: `CPATH` + `VLLM_ATTENTION_BACKEND=TORCH_SDPA` + `VLLM_USE_DEEP_GEMM=0`을
모두 붙여 fp8 4B와 bf16 0.6B를 각각 띄운다. 확정 조건은 **양쪽 다 기동하고,
`--enforce-eager` 없이도 뜨는 것**이다.

> **2026-08-22 — 좁혀진 것은 "②가 필수인가"이지 "셋을 합칠 수 있는가"가 아니다.**
> [파이프라인 보고서](harness/2026-08-22-pipeline-atom.md) 검증 run(`run_20260821_0007`)의 서버
> 프로세스에서 `/proc/<pid>/environ`을 읽었더니 **`CPATH`와 `VLLM_USE_DEEP_GEMM=0`은
> 있고 `VLLM_ATTENTION_BACKEND`는 없었다.** 그 상태로 fp8 4B가 `--enforce-eager` 없이
> 떴고 파이프라인이 완주했다(argv: `--gpu-memory-utilization 0.2 --max-model-len 32768
> --enable-prefix-caching`).
>
> → **fp8 4B에 대해 ②는 필수가 아니다.** bf16 0.6B에 대해서는 여전히 모른다.
>
> **이 관측이 #2를 확정하지는 않는다.** #2가 묻는 것은 셋을 **동시에** 준 조합이
> 성립하는가인데, 셋을 동시에 준 서버는 아직 없다. 위 확정 방법은 그대로 유효하다.
>
> **덤프는 전량이 아니다.** 필터가 `^(VLLM_|CPATH=|CUDA_VISIBLE|LD_LIBRARY_PATH=)`라
> 기동 명령에 있던 `NCCL_SOCKET_IFNAME=lo`·`GLOO_SOCKET_IFNAME=lo`는 **안 찍힌 것이지
> 미지정이 아니다.** `VLLM_ATTENTION_BACKEND` 판정만 이 덤프로 성립한다 — 그 이름은
> `VLLM_` 접두라 지정됐다면 필터를 통과했을 것이기 때문이다.

### #3 검색 실험의 서빙 모델 revision 3건 — 해소 (2026-08-22)

**해소(스냅샷 해시로).** 세 건 전부 미기재였다. 작성자(팀)가 ATOM 스냅샷 해시로
확정했고, 확정 #2의 오독을 낳은 공백이 정확히 이것이었다. 이제 검색 보고서 메타 표와
확정 #2 표에 기재됐다:

| 모델 | revision (HF 커밋) |
| --- | --- |
| `Qwen3-0.6B` | `c1899de289a04d12100db370d81485cdf75e47ca` |
| `Qwen3-Embedding-0.6B` | `97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3` |
| `Qwen3-Reranker-0.6B` | `e61197ed45024b0ed8a2d74b80b4d909f1255473` |

`~/.cache/huggingface/hub/models--Qwen--*/refs/main`과 스냅샷 디렉터리 이름이 일치했다.

**이 대조는 run에 묶여 있지 않다.** 검색 보고서는 원문 산출물도 §3 scout 기동 명령도
남기지 않아서([검색](harness/2026-08-18-retrieval-atom.md) 「메타」 표 · §10),
[파이프라인](harness/2026-08-22-pipeline-atom.md) 메타 표가 한 것처럼 기동 로그의 `model_tag`로
짚을 수가 없다. 캐시가 실험 뒤에 다시 받아진 것이라면 08-18에 서빙된 revision이 같다는
보장이 이 대조만으로는 안 나온다.

**그 구멍은 상류가 메운다.** 세 repo 모두 HF Hub 최종 수정이 실험일보다 앞선다.

| repo | 최종 수정 (HF Hub) |
| --- | --- |
| `Qwen/Qwen3-0.6B` | 2025-07-26 |
| `Qwen/Qwen3-Embedding-0.6B` | 2026-04-20 |
| `Qwen/Qwen3-Reranker-0.6B` | 2026-04-16 |

셋 다 실험 전부터 `main`이 움직이지 않았다. **언제 받았든 `--revision` 없이 받으면 같은
해시가 나온다는 뜻이고, 그래서 "지금 일치한다"가 "그때도 같았다"로 성립한다.**

### #4 `single_gpu_12g`의 `tool_runner: fake`가 의도인가

확정 #5에서 드러난 항목이다. 이 프로필로 도구를 돌리려면 매번 오버라이드가 필요하고,
그 오버라이드는 ADR 0005가 상정한 용도가 아니다.

**확정 방법**: 둘 중 하나를 고른다 — (a) ADR 0005의 범위를 `fake` → 실런타임 승격까지
넓힌다, (b) 프로필 선언을 고친다. **`config/` 변경이라 이 PR의 범위 밖이고 별도 PR이다.**

> **2026-08-22 — 두 번째 사례.** [파이프라인 보고서](harness/2026-08-22-pipeline-atom.md)의 run도
> 같은 프로필에 `--tool-runner docker`를 붙여야 했다. 사례가 하나였을 때는 검색 실험
> 쪽의 우연으로 읽을 여지가 있었지만, **다른 사람이 다른 목적으로 같은 벽에 부딪혔다.**
> 이 프로필로 도구를 돌리는 것이 예외가 아니라 통상 사용법이라는 뜻이다.

> **2026-08-23 — 세 번째 사례.** [zlib 보고서](harness/2026-08-23-pipeline-zlib-atom.md)의 run
> 둘도 같다. **세 번 다 오버라이드 없이는 실험이 성립하지 않았다** — `single_gpu_12g`로
> 도구를 안 돌리는 run이 아직 하나도 없다. 이 항목이 "확정 방법 둘 중 하나"로 열려 있는
> 동안 `RunRecord.tool_runner`에는 계속 선언값과 다른 값이 박힌다(ADR 0005의 의도대로
> 기록은 남지만, 남는 이유가 매번 같다).

> **2026-08-25 — 네 번째 사례, 그리고 (b)의 선택지가 늘었다.**
> [542건 보고서](harness/2026-08-25-pipeline-zlib542-atom.md)의 `run_20260825_0001`도 같은 프로필에
> `--tool-runner docker`를 붙였다. **네 번 연속이고, 이 프로필로 도구를 안 돌린 run은 여전히
> 0건이다.** 사례가 더 쌓여도 판정이 바뀌지 않는다는 뜻이라, 이 항목은 이제 "관측이 부족해서"
> 열려 있는 것이 아니라 **아무도 (a)/(b)를 고르지 않아서** 열려 있다.
>
> **확정 #6이 (b)에 값을 하나 더 준다.** 그전까지 "프로필 선언을 고친다"의 후보는 사실상
> `podman` 하나였다 — profiles.yaml이 "ATOM에는 podman만 있으므로"라고 적었기 때문이다.
> **ATOM에 docker도 있다는 것이 확인됐으므로**(확정 #6) `single_gpu_12g`의 선언을 고칠 때
> `docker`·`podman` 둘 다 후보다. 네 run이 전부 실제로 쓴 값은 **`docker`**다.
