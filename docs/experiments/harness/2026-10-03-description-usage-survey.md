# CyberGym 과제 설명문을 쓰고 있나 — 코드 경로 전수와 신호량

> **모델을 쓰지 않았다. 하네스도 돌지 않았다.** 잰 것은 **저장소의 코드가 `description.txt`를
> 어디까지 데려가는가** 하나이고, 과제 번들 둘에서 **설명문의 바이트·단어 수**를 셌다.
>
> **경로 성립 판정이고 성능 측정이 아니다.** 「설명문을 쓰면 점수가 오르는가」는 **이 보고서가
> 재지 않았다**(§5). 아래 어디에도 그 숫자가 없다.
>
> 주제는 이슈 [#377](https://github.com/0xM1yu/whitehat-boss/issues/377)
> ([본문](../../open-questions/D/D97.md))이다. 그 본문이 이미 든 관측(설명이 고친 위치를
> 가리키는 비율 · 슬라이싱 시간 · GT 포착)을 **다시 재지 않았고**, 여기서 더한 것은 **코드 경로
> 전수**와 **프롬프트 전수**, 그리고 **번들 신호량**이다.

## 메타

| 항목 | 값 |
| --- | --- |
| 실험 종료 시각 | `2026-10-03 KST` (분 단위 미기재 — 측정이 파일 읽기와 `git grep`뿐이라 종료 시각이 기록되는 자리가 없다) |
| 실제 서빙 모델 | **없다** — 모델 호출 **0건** |
| `repo_commit` | `16c1df46130dd5a1e17931b4ff11256b63ccb98f` (clean · `origin/main`과 같다) |
| `run_id` | **없다** — 하네스 run을 돌리지 않았다. 비교표 밖 |
| run 산출물 경로·보존 여부 | **없다** — run이 없다. 아래 수치는 전부 이 커밋의 추적 파일과 과제 번들에서 재현된다 |
| `tool_runner` | **해당 없음** — 도구 단계를 타지 않았다 |
| `--device` | **주지 않았다** — run이 없어 `RunRecord.device`가 생기지 않는다. ⚠ **그래서 파일명 끝의 `survey`는 장비 라벨이 아니라 주제의 꼬리다** — 색인 규약(`YYYY-MM-DD-<주제>-<장비>.md`)에 어긋나는 자리이고, 여기에 장비를 적으면 돌지 않은 장비를 적는 셈이라 비워 뒀다 |
| `tool_images` digest | **해당 없음** — 컨테이너 0건 |
| `--gpu-memory-utilization` | **해당 없음** — GPU 미사용 |
| 동시에 떠 있던 다른 서버 | **0건** |
| 원문 산출물 보존 위치 | **보존할 산출물이 없다** — 측정 명령과 결과를 §4·§6에 전사했다 |
| 서빙 서버 stdout | **해당 없음** — 서버를 띄우지 않았다 |
| 과제 번들 위치 | `eval/datasets/`(gitignore). **읽기만 했다** — 아카이브를 풀지 않았고 그 디렉터리에 쓰지 않았다 |

## 1. 명제

> **측정한 명제 —** 「하네스가 CyberGym 과제 설명문(`description.txt`)을 읽어 모델 입력까지
> 데려가는가, 그리고 그것을 쓰라고 지시하는 자리가 있는가.」

**「설명문을 안 쓴다」는 반만 맞다.** `run` 안에서는 **캡슐까지 간다** — 읽히고, 예산을 미리 떼고,
섹션으로 붙고, Scout·Auditor·PoV 프롬프트의 바이트에 실린다. 끊긴 것은 **그 뒤**다.
그래서 이 보고서는 **이어진 곳을 먼저** 적는다(§2), 끊긴 곳은 그다음이다(§3).

## 2. 이어진 곳 — 배선은 이미 돼 있다

### 2.1 파일 → `RunSpec`

| 자리 | 코드 | 무엇 |
| --- | --- | --- |
| 파일명 상수 | [`task_loader.py:25`](../../../src/orchestrator/task_loader.py) | `DESCRIPTION_FILENAME = "description.txt"` |
| 읽기 | `task_loader.py:88-96` | `read_description(task_dir) -> str or None`. 없으면 `None`을 돌려준다(지어내지 않는다) |
| 묶음 | `task_loader.py:100` | `load_task(task_dir, work_dir) -> (소스 루트, 설명)` |
| 프로덕션 입구 ①: 번들 | [`cli.py:924`](../../../src/cli.py) | `--task-dir` 분기가 `read_description(args.task_dir)`를 **따로** 부른다 |
| 프로덕션 입구 ②: 파일 | `cli.py:960-964` | `--description-file`이 그 파일을 직접 읽는다. 없는 경로면 `exit 2` |
| `RunSpec`에 담김 | `cli.py:974` | `vuln_description=description` — CLI→spec 배선은 이 한 줄이다 |
| 선언 | [`runner.py:469-471`](../../../src/orchestrator/runner.py) | `RunSpec.vuln_description: str or None` |
| 넘김 | `runner.py:792` | `vuln_description=spec.vuln_description` |
| run 문맥 | [`context.py:191-194`](../../../src/orchestrator/context.py) | `RunContext.vuln_description: str or None` |

**`load_task`의 프로덕션 호출처는 0건이고 그것은 의도된 것이다.** `cli.py:852-856`·`:922-923`의
주석이 사유를 적는다 — 설명은 **해제보다 먼저** 읽어야 해서 `read_description`과 `safe_extract`를
**따로** 부른다. 호출처가 테스트뿐인 것은 `task_loader.py:6`도 같이 적는다.

> ⚠ **「의도된 것」이 「무해하다」는 아니다.** 그 0건이 다른 자리에서는 결함으로 집계돼 있다 —
> [build-plan.md](../../build-plan.md) §11.4의 정답 자료 가드(PR #397) 행이
> *"🔴 **호출처 0건** — `--task-dir` 분기는 `load_task`를 안 불러 이 가드를 안 거친다"*라 적고,
> [#377 본문](../../open-questions/D/D97.md)의 「선행」 항목이 같은 것을 든다.
> **이 보고서는 그 둘을 인용만 하고 새로 재지 않았다.**

### 2.2 `RunContext` → 캡슐 섹션

[`retrieval.py`](../../../src/orchestrator/nodes/retrieval.py)가 한 자리에서 붙인다 —
Retriever 구현(fake·hybrid)과 무관하다(`:112-113`의 사유).

| 자리 | 코드 | 무엇 |
| --- | --- | --- |
| 섹션 종류 | `retrieval.py:42` | `VULN_DESCRIPTION_KIND = "vuln_description"`. `known_facts`를 재쓰지 않는 사유를 `:37-41`이 적는다(그 섹션은 고정 64토큰 자리표시자라 가변 길이를 넣으면 회계가 틀린다) |
| 섹션 조립 | `retrieval.py:45-66` | 남은 예산에 드는 **줄까지만** 담고 `tokens`를 본문에서 센다. 빈 줄은 버린다. 한 줄도 못 담으면 `None` |
| 예산 | `retrieval.py:88` | `budget = ctx.config.thresholds.budget_for(level)` — 노드가 숫자를 박지 않는다 |
| 풀 마진 | `retrieval.py:92-93` | Scout 풀 세 자리가 다 프로필에 있으면 `budget = int(budget * 0.90)` |
| **자리를 미리 뗀다** | `retrieval.py:98-103` | `_vuln_description_section(ctx.vuln_description, budget // 2)` → 그만큼을 Retriever 예산에서 뺀다(`:108`). 뒤에 붙이면 남은 예산이 0이라 조용히 빠지기 때문이다 |
| 붙임 | `retrieval.py:114-117`·`:147-150` | `capsule.transition(sections=[...기존..., 설명])` |
| 잘림을 숨기지 않는다 | `retrieval.py:122-144` · [`trace.py:345`](../../../src/orchestrator/trace.py) | 담긴 줄 < 빈 줄 아닌 줄이면 `vuln_description_truncated` 이벤트(`kept`·`total` 동반) |

### 2.3 캡슐 → 프롬프트 바이트

**두 경로로 실린다.** 둘 다 섹션 **종류 이름과 본문**을 함께 낸다.

| 경로 | 코드 | 모델이 보는 모양 |
| --- | --- | --- |
| 산문 — `{{CAPSULE_SECTIONS}}` | [`scout.py:661-689`](../../../src/orchestrator/nodes/scout.py) (`describe_sections`), 부르는 곳 `scout.py:419`·[`auditor.py:163`](../../../src/orchestrator/nodes/auditor.py) | `- vuln_description (N tok, M건)` 다음 줄에 본문이 들여쓰여 붙는다 |
| 사실 블록 — `{{FACTS}}` | [`prompt_loader.py:329-339`](../../../src/orchestrator/prompt_loader.py), 키는 `prompt_loader.py:84`의 `FACT_KEYS` | `capsule_sections: [{kind, tokens, items, lines}]` JSON |

**양쪽에 싣는 것은 중복이 아니라 계약이다** — `body.md`가 *"아래 JSON이 참조 가능한 사실
전부다"*라 못박으므로(`prompts/pov/v3/body.md:17`), 사실 블록에 없는 것은 모델이 무시해도 되는
값이 된다(`describe_sections` docstring·`prompt_loader.py:326-328`).

**지금 서빙 로스터에서 어느 역할이 어느 경로로 보는가** — `config/profiles.yaml`의 자리와
`config/models.yaml`의 `prompt_template_version`을 묶어 쟀다.

| 역할 | 모델 키 | 프롬프트 판 | `{{CAPSULE_SECTIONS}}` | `{{FACTS}}` |
| --- | --- | --- | --- | --- |
| `scout_memory` | `qwen3-14b` | v3 | 있다 | 있다 |
| `scout_boundary` | `seccoderx-8b` | v3 | 있다 | 있다 |
| `scout_injection` | `qwen3-14b` | v3 | 있다 | 있다 |
| `auditor` | `qwen3.8-27b` | v4 | 있다 | 있다 |
| `pov` | `feyospace-v1-small` | v2 | **없다** | 있다 |

**PoV는 사실 블록으로만 본다** — `prompts/pov/v2/body.md`·`v3/body.md`의 자리표시자가
`{{FINDING}} {{TARGET}} {{SOURCE}} {{FACTS}}` 넷이고 `{{CAPSULE_SECTIONS}}`가 없다.
그래도 `pov.py:46-86`의 `_render_prompt`가 `build_facts(..., capsule=capsule)`을 주므로
`capsule_sections`에 설명이 실린다.

### 2.4 PoV가 받는 캡슐은 Auditor의 것이다

| 자리 | 코드 | 무엇 |
| --- | --- | --- |
| 넣는 곳 | `runner.py:2504` (`_step_auditor`) | `state.finding_capsules[finding.finding_id] = expanded` |
| 꺼내는 곳 | `runner.py:1418` (`_run_pov`) | `capsule = state.finding_capsules.get(finding.finding_id)` — `None`이면 그 자리에서 이벤트를 내고 멈춘다(`:1419-1420`) |

즉 PoV는 **EXPANDED 캡슐**을 받고, 그 안에 설명 섹션이 들어 있다.

### 2.5 테스트가 이 경로를 문다 — 핀 수

`tests/`에서 센 핀이다. **「파일당 적중 수」와 「검사 함수 수」를 따로 센다** — 둘을 섞으면
커버리지가 과장된다.

| 무엇 | 어디 | 적중 줄 | 검사 함수 |
| --- | --- | --- | --- |
| `vuln_description` | `tests/test_nodes.py` | 16 | 6 (`:659`·`:674`·`:718`·`:763`·`:794`·`:827`) |
| `vuln_description` | `tests/test_pipeline_e2e.py` | 9 | 5 (`:306`·`:323`·`:351`·`:616`·`:951`) |
| `--description-file`·`description_file` | `tests/test_pipeline_e2e.py` | 10 | 3 (`:323`·`:351`·`:616`) |
| `task_dir`·`task-dir` | `tests/test_pipeline_e2e.py` | 39 | 미분류 — 설명문 전용이 아니라 번들 분기 전체를 문다 |

**가장 센 핀은 `test_pipeline_e2e.py:306`이다** — `test_vuln_description_reaches_scout_auditor_and_pov_prompts`가
마커 문자열을 넣고 **남은 프롬프트 파일에서 그 마커를 찾는다**. 세 역할 중 하나라도 주입이 빠지면
빨개진다. §2.3의 「프롬프트 바이트에 실린다」가 그 핀으로 강제된다.

## 3. 끊긴 자리

### 3.1 프롬프트가 「그것을 읽으라」고 말하는 곳이 0건

**전수 명제 —** *「어떤 프롬프트 텍스트가 모델에게 과제 설명(캡슐 `vuln_description` 섹션)이
무엇인지 알려주거나 그것을 쓰라고 지시한다.」*

문자열이 아니라 **문형**으로 넓혔다(CLAUDE.md §2.5).

| 형 | 패턴 | `prompts/` 적중 | 그중 명제에 해당 |
| --- | --- | --- | --- |
| A — 섹션 종류 이름 | `vuln_description` | 0 | 0 |
| B — 한국어 지시형 | `과제 설명` | 0 | 0 |
| C — 라틴 전부(대소문자 무시) | `description` | 51 (41 파일) | **0** |
| D — 보고·제보 계열 | `제보`·`신고`·`리포트`·`보고서`·`취약점 보고` | 0 | 0 |
| E — 맨 한국어 | `설명` | 4 | **0** |

**C의 51건 분류** — 34건은 `meta.yaml`의 `description:` 필드(프롬프트 **판본의 설명**이고 모델이
안 본다). 17건은 `prefix.md`인데 셋은 *"do not treat a related document, CWE/CVE description,
patch, write-up, or retrieval label as direct …"*(외부 문서를 근거로 삼지 말라는 **금지**이고
과제 설명이 아니다 — `auditor/v4:42`·`v4_t1:42`·`v5:47`), 나머지 14건은 **모델이 낼 출력 스키마의
필드 이름**이다(`code_evidence[].description`·`conditions[].description`, "Keep all descriptions
short", "never append a title or description", `single/v2:79`의 자기 출력 안정성).

**E의 4건 분류** — 전부 *"설명·주석·코드펜스 없이"*·*"설명 문장을 붙이지 않는다"* 꼴로 **반대
방향 지시**다(`critic/v1/prefix.md:3`·`pov/v2/prefix.md:47`·`pov/v3/prefix.md:95`·`scout/v1/prefix.md:38`).

**손 훑기 —** §2.3 표의 서빙 판 다섯(`scout_*` v3 ×3 · `auditor` v4 · `pov` v2)의 `body.md`
자리표시자 목록을 **직접 열어** 대조했다. 설명을 이름으로 부르는 자리표시자는 없고, 섹션은
`{{CAPSULE_SECTIONS}}`·`{{FACTS}}`라는 **일반 통로**로만 들어간다. **검출기 0 대 손 훑기 0 —
델타 0.**

**그래서 참인 명제는 이것까지다.** 모델은 **`vuln_description`이라는 라벨과 그 본문을 본다**
(§2.3의 렌더 모양). **그것이 과제가 준 취약점 설명이라고 말해 주는 프롬프트 문장은 없다.**
그 라벨만으로 모델이 무엇을 하는지는 **이 보고서가 재지 않았다**(§5).

### 3.2 슬라이서는 설명문을 안 읽는다

**전수 명제 —** *「슬라이서 코드가 과제 설명문을 입력으로 받거나 읽는다.」*

슬라이서는 #407로 [`tools/slicer/`](../../../tools/slicer/slicer.py)로 이사했다 — 추적 파일 12개.
그 디렉터리와 **밖의 슬라이서 보조 도구 넷**(`tools/merge_slicer_shards.py`·`tools/run_slicer_recorded.py`·
`tools/slicer_query_timings.py`·`tools/probe/joern_slicer_live.py`)을 같이 훑었다.

| 형 | 패턴 | `tools/slicer/` | 보조 넷 | 명제에 해당 |
| --- | --- | --- | --- | --- |
| A | `description` (대소문자 무시) | 1 | 1 | **0** |
| B | `설명` | 0 | 0 | 0 |
| C | `vuln_description`·`description.txt`·`task_dir` | 0 | 0 | 0 |

**A의 2건은 둘 다 `argparse.ArgumentParser(description=__doc__)`다** —
`tools/slicer/slicer.py:862`·`tools/probe/joern_slicer_live.py:106`. CLI **도움말 문구**이고
과제 설명과 무관하다. **CLI에 설명문을 받는 인자가 없다.**

이것은 [#377 본문](../../open-questions/D/D97.md)의 「설명이 안 닿는 곳」이 적은 것과 같고,
**이사(#407) 뒤의 경로에서 다시 재서 같았다.**

### 3.3 `docs/build-plan.md`에 설명문 서술 0건

§11(CyberGym Level 1 채점 사슬)에 설명문 관련 서술이 있는지 봤다. **0건이다** — 절 단위가 아니라
**파일 전체**에서 `설명`이 **0회**, `description`·`설명문`·`과제 설명`·`description.txt`도 **0회**다.
§11.1~§11.7을 직접 읽어 대조했고 전부 PoC 바이트 출구·오라클·승격 문 쪽이다.

## 4. 신호량 실측 — n=2

`eval/datasets/`의 과제 번들 둘. **읽기만 했다.** 🔴 **설명문 본문은 전재하지 않는다** —
과제 입력이다.

| 과제 | 번들 파일 | 바이트 | 단어 | `splitlines()` 줄 | 개행 바이트(`0x0A`) | `0x0D` |
| --- | --- | --- | --- | --- | --- | --- |
| `arvo-1065` | 2개 (`description.txt`·`repo-vul.tar.gz`) | 79 | 13 | 1 | 0 | 0 |
| `oss-fuzz-368076871` | 2개 (`description.txt`·`repo-vul.tar.gz`) | 198 | 28 | 1 | 0 | 0 |

**각 디렉터리에 정확히 두 파일이다** — `README.md`·`submit.sh`·정답 자료가 없다. 단어 수는
파이썬 `str.split()` 기준이다.

### 4.1 캡슐 예산에서 설명이 받는 몫 — 지금 설정에서 잘릴 일이 있나

예산은 [`config/budgets.yaml:290-291`](../../../config/budgets.yaml)의
`thresholds.capsule_budget_tokens`다 — `light: 4000` · `expanded: 18000`.
설명의 상한은 `retrieval.py:99`의 **`budget // 2`**이고, 풀 프로필이면 `retrieval.py:93`이
먼저 `× 0.90`을 건다(`SCOUT_POOL_ROLES`, `src/config.py:47`).

| 레벨 | 캡슐 예산 | 설명 상한 (`budget // 2`) |
| --- | --- | --- |
| LIGHT · 풀 아님 | 4,000 | 2,000 |
| LIGHT · 풀 (`× 0.90`) | 3,600 (계산값) | 1,800 |
| EXPANDED · 풀 아님 | 18,000 | 9,000 |
| EXPANDED · 풀 (`× 0.90`) | 16,200 (계산값) | 8,100 |

그 상한에 위 설명문 둘을 **저장소의 `retrieval._vuln_description_section`으로 직접 태워** 쟀다.
토큰은 **지금 켜진 기본 카운터**(`src/tokens.py:84`의 `_chars_estimate` — 글자수 근사)가 센 값이고
**실 토크나이저 실측이 아니다**.

| 과제 | 근사 토큰 | 담긴 줄 / 빈 줄 아닌 줄 | 가장 좁은 상한(1,800) 대비 |
| --- | --- | --- | --- |
| `arvo-1065` | 19 | 1 / 1 | 1.06% (계산값) |
| `oss-fuzz-368076871` | 49 | 1 / 1 | 2.72% (계산값) |

**네 상한 전부에서 두 설명문이 통째로 들어갔다** — `vuln_description_truncated`가 날 조건이
아니다. 가장 좁은 1,800에서도 여유가 1,781·1,751 토큰이다.

⚠ **그래도 「잘릴 일이 없다」로 일반화하지 않는다.** 둘 다 **개행이 0인 한 줄**이고,
`_vuln_description_section`의 루프는 **안 맞는 첫 줄에서 `break`** 한다 — 한 줄짜리 설명문은
**전부 아니면 0**이다. 상한을 넘는 한 줄이 오면 `kept=0`이 되고 섹션이 아예 안 붙는다
(`retrieval.py:64-65`·`:145-146`, 그때도 이벤트는 난다). **그 경우를 재려면 더 긴 설명문이
필요하고 번들 둘에는 없다** — 공개 과제 1,507개의 설명문 길이 분포는 **0건**이다(§5).

## 5. 🔴 역량 미측정

> **설명문을 쓰면 점수가 오르는지 재 본 적이 없다.**

| 무엇 | 값 | 사유 |
| --- | --- | --- |
| 설명문 있음 ↔ 없음으로 CyberGym 점수를 비교한 run | **0** | 그런 run을 돌리지 않았다. 이 보고서도 run이 없다 |
| 설명문이 Scout·Auditor 판정을 바꾼 실측 | **0** | 같은 대상에 설명만 넣고 뺀 대조가 저장소에 없다 |
| 설명문이 PoV 성공률을 바꾼 실측 | **0** | PoC 제출까지 간 run이 없다 |
| 설명문 길이 분포 (공개 과제 1,507개) | **0** | 번들 둘만 로컬에 있다. 받지 않았다 |
| 실 토크나이저로 센 설명문 토큰 | **0** | §4.1은 글자수 근사다. 실 카운터를 켠 프로필에서 안 쟀다 |
| `vuln_description_truncated` 실관측 | **0** | §4.1에서 조건이 안 걸렸고, 실서빙 run 기록에도 없다 |
| 프롬프트가 라벨만 보고 설명을 쓰는가 | **0** | 모델 호출 0건. §3.1은 **프롬프트 텍스트**를 센 것이고 모델 거동이 아니다 |

**이 칸을 추정으로 채우지 않는다**(CLAUDE.md §0).

⚠ **「아무것도 안 쟀다」가 아니다.** [#377 본문](../../open-questions/D/D97.md)이 **다른 축**을
쟀다 — 설명이 고친 파일·함수를 가리키는 비율(과제 1,503개), 저장소 전체 슬라이싱 비용,
설명 기반 범위 제한·우선순위의 GT 포착(표본 셋). **그중 어느 것도 하네스 점수가 아니다.**
위 표의 0은 그 관측들을 부정하지 않고, 겹치지 않는다.

## 6. 가능한 소비자 둘과 그 소속

설명문이 **더** 쓰일 수 있는 자리는 §3이 끊긴 두 곳이다. **둘 다 이 묶음 밖이다.**

| 소비자 | 끊긴 자리 | 어디 소속인가 |
| --- | --- | --- |
| 프롬프트 지시 | §3.1 — 라벨은 실리는데 그것을 쓰라는 문장이 없다 | **프롬프트 판 작업(S9)과 실험팀이다.** 프롬프트 A/B 설계는 이 저장소가 하지 않는다 — [CLAUDE.md](../../../CLAUDE.md) 머리 블록이 *"실험팀의 실험 … 은 실험팀 일이고 원천은 팀 Notion"*이라 적고, §6 표가 「프롬프트 A/B」를 그쪽으로 보낸다. **그래서 이 보고서는 어떤 문구도 제안하지 않는다** |
| 슬라이서 소비 | §3.2 — CLI에 받는 인자가 없다 | **슬라이서 호스트가 필요하다.** 효과를 재려면 저장소를 Joern에 넣어 돌려야 하고, 그 실측이 [#377 본문](../../open-questions/D/D97.md) 관측 1·3이 선 자리다(vast 대여). 호스트 없이 코드만 고치면 **재지 못한 채 배선만 늘어난다** |

**그래서 이 보고서의 결론은 「둘 다 이 묶음 밖」이다.** 여기서 성립한 것은
**`run` 안의 배선은 이미 서 있고(§2), 끊긴 두 곳의 소속이 밖이다**까지다.

## 7. 한 줄 정리

| 물음 | 답 |
| --- | --- |
| 설명문을 읽는가 | **읽는다** — 입구 둘(`--task-dir`·`--description-file`) |
| 모델 입력까지 가는가 | **간다** — 캡슐 `vuln_description` 섹션 → Scout·Auditor·PoV 프롬프트 바이트. 테스트 핀 하나가 세 역할을 동시에 문다 |
| 예산에 끼는가 | **안 낀다**(n=2) — 가장 좁은 상한 1,800에 19·49 토큰(근사) |
| 쓰라고 말하는 곳이 있나 | **0건** — 라벨과 본문은 보이고 지시는 없다 |
| 슬라이서가 쓰는가 | **안 쓴다** — 인자 0개 · 참조 0건 |
| 쓰면 점수가 오르나 | **0건 — 재지 않았다**(§5) |

## 8. S6d를 지을 가치가 있나 — 사용자가 답할 자리

**양쪽 근거를 숫자로 둔다.** **지을 쪽**: 설명문은 공짜에 가깝다 — n=2에서 가장 좁은 캡슐 상한의
1.06%·2.72%(계산값)만 먹고, 입구부터 PoV 프롬프트까지의 배선과 테스트 핀 11개가 **이미 있다**(§2.5).
그래서 프롬프트 쪽 소비는 코드가 아니라 문구 하나의 크기이고, 슬라이서 쪽은 [#377 본문](../../open-questions/D/D97.md)
관측 2가 설명이 고친 파일을 가리키는 비율을 **31.2%**, 파일 또는 hunk 식별자까지 넓히면 **61.3%**로
재 뒀다 — 버리기 아까운 신호다. **안 지을 쪽**: 그 신호가 **점수로 바뀌는지는 0건**이고(§5),
효과를 재는 두 자리가 **둘 다 이 저장소 밖**이다(§6) — 프롬프트 A/B는 실험팀 소관이라 여기서
설계할 수 없고, 슬라이서 소비는 대여 호스트가 있어야 재진다(관측 1의 1,500표본 환산 803.5 박스시간,
계산값 · 그 값도 다른 장비에서 3.6~4.4배 빨라 과대일 수 있다고 그 본문이 스스로 적는다).
그리고 [build-plan.md](../../build-plan.md) §11이 지금 세우는 사슬에 설명문이 **0회** 나온다(§3.3) —
이 작업은 그 사슬의 어느 칸도 아니다. **그래서 묻는 것은 하나다: S6d를 「프롬프트 문구 한 줄」로
좁혀 지금 넣을 것인가, 설명문 소비를 통째로 #377의 안 선택(그리고 그 호스트) 뒤로 미룰 것인가,
아니면 지금은 이 측정만으로 닫을 것인가.** 이 보고서는 셋 중 어느 것도 고르지 않는다.
