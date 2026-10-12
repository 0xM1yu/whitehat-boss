# 현재 하네스 → CyberGym 벤치마크 구조 변경 조사 — Role.PoV·동적 도구·Validation 교체

**조사일 2026-09-30.** 라벨: `[코드]`(이 저장소를 `codex/toolrunner-gdb` 트리에서 **직접 열어 확인**) ·
`[출처주장]`(저자·벤더 자기 서술) · `[제3자]`(독립 측정) · `[계산]`(우리 산술) · `[미확인]`.
**여기 있는 것은 실측이 아니다** — 우리 장비 실측은 [experiments/](../experiments/README.md)에 있다.

**이 조사가 결정을 내리지 않는다**(CLAUDE.md §3). 특히 **어느 벤치마크로 참가할지(CyberGym Level 1 vs E2E vs 탐지벤치, 그리고 채택 자체)는
실험팀 소관**이고 원천은 팀 Notion이다([ADR 0012](../decisions/0012-harness-canon-v2.md) 결정 5). ⏵ **「벤치마크 선택」은 낡음 — 아래 시점 노트 ③**(검증 벤치마크 = CyberGym Level 1은 `5990dff`부터 ADR 0012:196이 정했다. 벤치마크 **설계**는 그 줄도 실험팀 몫으로 둔다). 이 문서가 다루는 것은 **하네스를 짓는 쪽의 구조 변경** —
"만약 벤치마크가 CyberGym이 된다면 코드가 무엇을 어떻게 바꿔야 하나"이고, 그것은 이 저장소의 일이다(CLAUDE.md 머리 박스).
**모든 값(홉 수·도구 목록 확정·모델)과 채택 여부는 실측(D)·sumin 리뷰·팀 판단으로 정한다.** 구조 서술은 전부 **제안**이다.

**조사 방법.** ① 현재 구조는 `src/`·`config/`·`STATUS.md`를 **직접 열어** 대조했다(CLAUDE.md §2.1). ② 외부 사실(제출 프로토콜·리더보드 하네스·선행 도구·PoV 모델)은
**어제(2026-09-29) 이미 조사돼 있어** 재조사하지 않고 아래 셋을 원천으로 인용한다(중복 금지):

| 원천 | 무엇을 든다 |
| --- | --- |
| [2026-09-29-cybergym-pov-design.md](2026-09-29-cybergym-pov-design.md) | CyberGym 제출 프로토콜(저자 코드 대조) · 홉 루프 종료 조건 · triage↔synthesis 선례 · Gate 단순화 근거 · feyospace 후보 §6 |
| [2026-09-29-cybergym-leaderboard-harnesses.md](2026-09-29-cybergym-leaderboard-harnesses.md) | 리더보드 엔트리의 하네스·도구·퍼저 사용(Q2·Q3) — EnIGMA 툴셋·정적/동적(기본 화면 한정) |
| [2026-09-29-auditor-finetuning-and-benchmark.md](2026-09-29-auditor-finetuning-and-benchmark.md) | Phase 7 정본에 CyberGym이 없음 · CyberGym은 탐지가 아니라 PoV 채점 · gdb/ASan 선례 §4.4 ⏵ 낡음 — 아래 시점 노트 ③ |

③ 선행 도구 한 곳(Google Naptime 4-도구 아키텍처)만 오늘 새로 열었다 — 어제 파일이 `[미확인]`으로 남긴 자리다(§5.1).
**열람한 어느 페이지에서도 에이전트를 겨냥한 지시문은 발견되지 않았다.**

> ⏵ **2026-10-02 — 이 문서의 `[코드]` 서술은 `d9023d9`(2026-09-29 02:18 KST) 트리 기준이다. 그 뒤 main이 움직여 아래 여섯 묶음은 낡았다**(PR #376 리뷰 반영, 원문은 지우지 않았다).
> **그 트리에서는 참이었다** — 2026-10-02에 `d9023d9`를 다시 셌다: `src/schemas/enums.py`의 `POV` 0줄 · `pov`가 든 추적 파일 0건 · `src/`의 `cybergym` 0줄 · ADR 0012의 `CyberGym` 0줄.
> 아래는 **`d85ea62`(2026-10-02) main에서 직접 센 값**이고, 해당 줄 끝에 「⏵ 위 시점 노트 ①~⑥」를 달았다:
> ① **「PoV 코드 0줄 · `Role`에 `POV` 없음 · `prompts/pov/`·`nodes/pov.py` 없음」** → `Role.POV`는 `f1dfde6`(2026-09-30 03:32 KST, `src/schemas/enums.py:89`)부터 있다.
> 다중 홉 툴 루프(`runner._run_pov_hops`, `85249d3`)와 `prompts/pov/v2/`(`a36d008`)는 2026-09-30 22:04 KST에 들어왔다. `git ls-files | grep -i pov`는 6건이다(`prompts/pov/v2/` 셋 · `src/orchestrator/nodes/pov.py` · `src/schemas/pov.py` · `tests/test_pov_loop.py`).
> 「`poc` 갈래를 만드는 주체가 없다」는 **부분만 낡았다** — `PovAction`→`assemble_poc`이 PoC 바이트를 만들지만 그 바이트가 도구 입력으로 아직 안 들어간다(`STATUS.md` §PoV 「남은 것」 · `container_tools.py`의 그 주석은 `:473`으로 옮겨 그대로 있다).
> ② **「`git grep cybergym -- src/` 0건」** → 8개 파일 10줄이다(`src/cli.py`·`config.py`·`orchestrator/context.py`·`orchestrator/nodes/retrieval.py`·`orchestrator/runner.py`·`orchestrator/task_loader.py`·`schemas/enums.py`·`schemas/pov.py`).
> 최초는 `de47057`(2026-09-30 04:19 KST), 과제 로더 `task_loader.py`는 `f58e244`(2026-09-30 13:10 KST)다. **제출 어댑터(`/submit-vul` 클라이언트)는 지금도 `src/`에 없다**(`submit.sh`는 `task_loader.py:4`의 docstring에만 나온다).
> ③ **「ADR 0012의 Phase 7에 CyberGym이 없다」** → `docs/decisions/0012-harness-canon-v2.md:196`이 `5990dff`(2026-09-30 14:32 KST)부터 「검증 벤치마크 = CyberGym Level 1」을 적는다(조민규 결정 2026-09-30 — 원천 링크는 저장소에 없다고 그 줄이 스스로 적는다).
> ④ **「PR #327(gdb) — 이 브랜치」** → `5cf5c20`(2026-10-01 03:29 KST)으로 main에 머지됐다.
> ⑤ **「모델이 도구를 반복 호출하는 루프는 없다 · 모델은 도구를 직접 못 부른다」** → **PoV에 한해** 낡았다 — 매 홉 PoV 모델이 `PovAction`(`gen`·`inspect`·`submit`)을 내고 러너가 실행해 관측을 되먹인다(`_run_pov_hops`).
> tool-calling API가 아니라 `PovAction` JSON 스키마 출력이다(`prompts/pov/v2/meta.yaml`의 `output_schema`). `PovAction.tool`은 `d85ea62` 기준 `asan`·`build`·`poc`이었고, **#380 머지(`a696f85`, 2026-10-03)로 `gdb`가 더해졌다**(`src/schemas/pov.py:185`) — 단 배선된 v2 프롬프트는 gdb를 안내하지 않는다. Scout·Auditor·Critic은 서술 그대로다.
> ⑥ **「Level 1 설명이 입력으로 Scout와 정합 · 설명 기반 트리아지」**(§2 표 입력 행·§3.3·§6) → 쓴 시점에 근거가 없었다 — `d9023d9`에서 `vuln_description`은 `src/`에 0건이었다. 설명이 캡슐 `vuln_description` 섹션으로 Scout·Auditor·pov에 가는 것은 `f58e244`(2026-09-30 13:10 KST)부터다(`src/orchestrator/nodes/retrieval.py`, `STATUS.md` §PoV).
> **지금도 슬라이싱(후보 선정)은 설명을 안 쓴다**(`slicer*.py`에 설명 입력이 없고 `runner._auditor_source`도 안 본다 — 2026-10-02 `git grep`). 간극은 이슈 [#377](https://github.com/0xM1yu/whitehat-boss/issues/377)이 추적한다. 「설명이 위치를 좁힌다」의 원천 쪽 근거 — 논문 논문 §3.3의 위치 정보 필터(정밀도 96%)와 Table 3의 입도 다양성(설명 24~158단어) — 은 [pov-design 맨 위 둘째 노트](2026-09-29-cybergym-pov-design.md)에 양쪽 다 있다.
> 이 노트가 다루는 것은 위 줄들뿐이다 — 나머지 `[코드]` 서술(예: `validate.tool_for()`의 도구 선택은 `d85ea62`에서도 asan·poc 둘뿐이다)은 이 PR에서 다시 열지 않았다.

---

## 0. 한 줄 답 다섯

1. **현재 도구 실행은 "오케스트레이터가 고르는 1회성"이다** — 모델은 JSON만 내고, 어느 도구를 돌릴지는 **노드**가 `repro_spec.expected_signal`에서 역으로 고른다(`validate.tool_for`). **모델이 도구를 반복 호출하는 루프는 없다.** PoV에 도구를 쥐어주는 것은 **새 실행 패턴**(모델-주도 다중 턴 툴 루프)을 하나 만드는 일이다(§1.2·§4.2). ⏵ PoV에 한해 낡음 — 위 시점 노트 ⑤
2. **PoV는 코드 0줄이다** `[코드]` — `Role`에 `POV`가 없고, `prompts/pov/`·`nodes/pov.py`가 없다. `container_tools.plan()`의 `poc` 갈래는 `{SCRATCH}/poc`를 실행하지만 **그 파일을 만드는 주체가 없다**(§4.1). ⏵ 낡음 — 위 시점 노트 ①
3. **Validation 교체는 "두 층"이다.** 현행 8단계는 **내부 재현 판정**(E4 = SANITIZER/CRASH/POC_RUN Evidence)이고, CyberGym은 **외부 oracle**(패치 전 크래시 ∧ 패치 후 무크래시)이다. 둘은 다른 것이고 **공존**할 수 있다 — 내부 E4는 비용 필터로, 최종 판정은 oracle로(§3).
4. **선행 연구의 도구는 수렴한다** — Naptime 4개(Code Browser·Python·Debugger[ASan]·Reporter), EnIGMA(`debug_*`·`decompile`·`disassemble`·`search_file`, raw gdb 차단). **핵심 축은 "코드 열람 + 입력 생성 스크립트 + ASan/디버거 + 제출"**이고, 우리는 그중 셋의 재료를 이미 가졌다(index·gdb·asan). 빠진 것은 **입력 생성 스크립트(Python) 실행**과 **그 넷을 도는 루프**다(§5). ⏵ 「루프가 없다」는 부분만 낡음 — 위 시점 노트 ⑤(PoV 툴 루프 `_run_pov_hops`는 `85249d3`부터 있으나 그 도구는 `asan`·`build`·`poc`뿐이다 — 코드 열람·gdb·Python 스크립팅을 한 루프에서 도는 것은 `d85ea62`에도 없다)
5. **"동적이 PoV에 필수"라는 전제는 이 근거로 가부를 못 가른다** `[제3자/출처주장]` — 학술 AEG·Naptime·AIxCC는 동적을 쓴다. CyberGym에는 **정적만으로 상위에 간 존재 증명**이 있다(모델트랙 1위 XekRung 88.9%가 퍼저 명시 배제, XDxAI 정적→PoC). 그러나 이는 cybergym.io **기본 화면(model-focused) 한정**이다 — 90%를 넘는 17행은 전부 agent-focused이고 13행이 `dynamic` 라벨이다([leaderboard 조사 ⏵ 상자](2026-09-29-cybergym-leaderboard-harnesses.md), 2026-10-02 원문 데이터 재계산). 동적 `-vul` 이미지는 **선택**이다(FAQ Q5). (76·72·44·17·13행은 모두 위 `[계산]`이다.) 정확한 서술은 "**최종 검증은 항상 실행(oracle)이고, 정적만으로도 상위에 간 사례가 있으나 상위권 다수는 동적 라벨을 단다 — 필수 여부는 이 근거로 못 가른다**"이다(§6).

---

## 1. 현재 하네스 구조 — 코드로 대조 `[코드]`

기준 트리 = `codex/toolrunner-gdb`(`git log` head `d9023d9`). 아래는 전부 그 트리를 직접 열어 확인했다.

### 1.1 파이프라인 0~9단계 (`src/orchestrator/stages.py`)

```
0 PREPROCESS → 1 INDEX → 2 SIGNALS → 3 RETRIEVAL → 4 SCOUT → 5 AUDITOR → 6 GATE → 7 CRITIC → 8 VALIDATE → 9 REPORT
```

- **역할(`Role`) 일곱 + single** (`src/schemas/enums.py`): `SCOUT`·`SCOUT_MEMORY`·`SCOUT_BOUNDARY`·`SCOUT_INJECTION`·`AUDITOR`·`CRITIC`·`JUDGE`·`SINGLE`. **`POV` 없음.** ⏵ 낡음 — 위 시점 노트 ①
- **각 역할의 출력은 JSON 스키마다** — Scout=`ScoutResponse`, Auditor=`Finding`+`MissingEvidence[]`, Critic=`Decision`. 모델은 이 스키마만 낸다.
- **상태 머신**(`FindingState`): `candidate → triaged → hypothesized → queued → validated → proven → reported`. `proven`은 8단계에서만 얻는다.

### 1.2 도구 실행 모델 — 오케스트레이터-주도 1회성 (핵심)

이것이 이 조사에서 가장 중요한 구조 사실이다.

- **인터페이스**(`src/interfaces/tool_runner.py`): `ToolRunner.run(tool, args, sandbox) -> Evidence`. **한 번 부르면 한 도구가 한 번 돈다.** 반복·상태·대화가 없다.
- **누가 도구를 고르나 = 노드다.** 8단계 `validate.run()`이 `tool_for(spec.expected_signal)`로 **도구 이름을 역산**한다(`src/orchestrator/nodes/validate.py:42`): 기대 신호에 `asan/sanitizer`가 있으면 `asan`, `poc/crash/exploit/segfault`가 있으면 `poc`, 그 밖은 `None`(=`tool_policy_violation`). ⚠ **gdb는 이 표에 없다** — 8단계가 gdb를 부르는 경로가 아직 없다(§4.3).
- **모델은 도구를 직접 못 부른다.** Scout·Auditor·Critic 어느 것도 tool-calling을 안 쓴다. 모델→JSON, 노드→도구. 이 분리가 결정론을 지킨다(같은 입력이 같은 도구 호출을 낳는다). ⏵ PoV에 한해 낡음 — 위 시점 노트 ⑤
- **화이트리스트**(`src/adapters/fake_tools.py:TOOL_KINDS`, `container_tools.run()`이 대조): 목록 밖 도구는 `Evidence(ok=False, "tool_policy 밖 도구")`. 현재 도구 = **semgrep·clang-tidy·index·build·asan·gdb·poc** 일곱(`container_tools.plan()`).
- **샌드박스 기본값이 정책**(`SandboxSpec`): `network=False`(disclosure-policy 위반이라 켜면 거부) · `read_only=True` · `cpu_limit`·`memory_mb`·`timeout_s` 강제. → CyberGym 제출 격리(`network_mode=none`, 10초)와 **같은 성질**이다([pov-design §1.4](2026-09-29-cybergym-pov-design.md)).

> **여기서 나오는 결론**: PoV에 "도구를 쥐어준다"는 것은 이 1회성 모델 위에 **모델-주도 다중 턴 툴 루프**를 새로 얹는다는 뜻이다. 그 패턴이 지금 저장소에 **정확히 대응하는 것은 없다**(§1.3). ⏵ 낡음 — 위 시점 노트 ①⑤

### 1.3 저장소에 있는 유일한 "다중 턴 루프" — Scout need_evidence 홉 (툴 루프가 아니다) ⏵ 「유일한」은 낡음 — 위 시점 노트 ⑤(`85249d3` 이후 `runner._run_pov_hops`가 둘째 다중 턴 루프다)

`runner._run_ne_hops`(hop1..`max_hops`, ADR 0012 결정 2)가 반복 루프이긴 하다. 그러나:

- **되묻는 상대가 도구가 아니라 resolver다.** 모델이 "이 심볼의 원본을 더 달라"(`need_evidence`)고 하면 **인덱스/심볼 resolver**가 저장소에서 원본 블록을 채워 다음 홉에 싣는다. **프로그램을 실행하지 않는다.**
- 정지 조건 여섯(`NeStopReason`): 신호가 NE 아님·빈 요청·직전 반복·아무것도 못 채움·max_hop·파싱 실패.
- **Auditor에는 이 루프조차 없다**([auditor-finetuning §4.2](2026-09-29-auditor-finetuning-and-benchmark.md)) — need_evidence 홉은 Scout 전용이다.

> **재사용 가능한 뼈대**: `_run_ne_hops`의 **홉 상한·정지 사유·홉 이벤트(`SCOUT_HOP`) 기록·예외 흡수 표**는 PoV 툴 루프가 그대로 본뜰 형태다. 바꾸는 것은 "resolver가 원본을 채운다"를 "ToolRunner가 도구를 실행해 관측을 채운다"로 바꾸는 것이다. **패턴은 있고, 되묻는 대상만 다르다.** ⏵ 이행됨 — 위 시점 노트 ⑤(`_run_pov_hops`의 docstring이 `_run_ne_hops`를 미러한다고 적는다, `d85ea62`)

### 1.4 Evidence Gate와 `proven`이 서는 자리 (`src/orchestrator/gate.py`)

| 등급 | 조건(요지) |
| --- | --- |
| E1 | STATIC Evidence 또는 위치 겹침 |
| E2 | 경로 {source, sink} + GRAPH Evidence + blocking 없음 |
| E3 | E2 + Critic `uphold` + `repro_spec` 존재 |
| E4 | `SANITIZER`/`CRASH`/`POC_RUN` Evidence 하나 이상 |

- `proven` = `validated` + **E4** + `repro_spec`(스키마가 `transition()`에서 강제).
- ⚠ **게이트는 `Evidence.ok`만 본다 — `expected_signal`을 산출물과 대조하지 않는다**(이슈 #232, `gate.py` 주석). 그래서 `p4`가 참이어도 "기대한 신호가 관측됐다"는 아니고, **우리 `proven`은 「크래시 재현」을 뜻하지 않는다**([D80](../open-questions/D/D80.md) 열림, [auditor-finetuning §6.2](2026-09-29-auditor-finetuning-and-benchmark.md)).
- **이 비대칭이 CyberGym과 정확히 맞물린다** — CyberGym은 산출물(PoC)을 실제 대상에 넣어 **크래시를 관측**해 판정한다. 즉 CyberGym oracle은 우리가 #232에서 **안 하고 있는 대조를 대신 해 준다**(§3).

---

## 2. CyberGym이 요구하는 것 vs 우리가 가진 것 — 델타 표

제출 프로토콜 사실은 [pov-design §1](2026-09-29-cybergym-pov-design.md)이 저자 코드로 확정한 것을 인용한다(재조사 안 함).

| CyberGym 요구 `[출처주장, 저자 코드]` | 우리 현재 `[코드]` | 델타 |
| --- | --- | --- |
| 산출 = PoC raw 바이트 파일 1개(≤10MB) | PoV 없음. `poc` 도구는 실행만, **생성자 없음** | **PoV 역할 신설**(§4) ⏵ 낡음 — 위 시점 노트 ① |
| 제출 = `bash submit.sh` → `POST /submit-vul` multipart | 제출 어댑터 없음. `git grep cybergym -- src/` 0건 | **제출 어댑터 신설**(§3.2) ⏵ 「cybergym 0건」은 낡음 — 위 시점 노트 ② |
| 살아 있는 신호 = 패치 전 exit_code + stdout(sanitizer) | 8단계가 내부에서 asan/poc 실행 → Evidence | **외부 신호를 Evidence로 흡수**(§3) |
| 최종 판정 = 차분(외부 채점) | 내부 게이트 E4(대조 없음, #232) | **oracle을 정본 판정, 내부 E4는 필터**(§3.1) |
| 종료 = 에이전트가 최종 후보 1개 지정 | run 종료는 예산/단계 소진 | **"최종 후보 지정" 종료 단계 신설**(§4.2) ⏵ 낡음 — 위 시점 노트 ①(`PovAction.submit`이 그 종료다 — 마지막 `gen`의 PoC가 final) |
| 실행 격리 = network=none·10초·20건/분 | `SandboxSpec` network=none·timeout(같은 성질) | 홉 간 간격·제출 예산 관리만 추가 |
| 입력 = 설명 + 패치 전 소스(Level 1) | Scout 입력이 슬라이스+설명 — 정합 | 과제 로더(tar → Artifact) 신설 ⏵ 저장소 안 근거 없음(원천은 일부 지지) — 위 시점 노트 ⑥ |
| 장부 = `SUBMISSION.md` 스키마(역할별 토큰4 등) | `COUNTED_ROLES=(scout,auditor)`만 | **PoV 토큰 카운터 추가**(§4.1) |

> ⏵ **위 시점 노트 ①②⑤ — 이 표의 「우리 현재」 열은 `d9023d9` 기준이다.** `d85ea62`에서 PoV 역할(`Role.POV`)·과제 로더(`task_loader.py`, `f58e244`)·「최종 후보 지정」 종료(`PovAction.submit` — 마지막 `gen`의 PoC가 final)는 들어왔다.
> **제출 어댑터와 PoV 토큰 카운터는 지금도 없다** — `src/`에 `/submit-vul` 클라이언트가 없고 `src/tokens.py:73`의 `COUNTED_ROLES`는 여전히 `("scout", "auditor")`다.

> **정본 주의**([auditor-finetuning §6.1](2026-09-29-auditor-finetuning-and-benchmark.md)): ADR 0012의 Phase 7은 *"평가 어댑터·채점기·배치 실행"* 한 줄뿐이고 **CyberGym이라는 이름은 정본에 없다.** 「Phase 7 = CyberGym」으로 못 박는 것은 ADR을 고치는 일이고 결정권은 팀이다. ⏵ 낡음 — 위 시점 노트 ③

---

## 3. Validation을 CyberGym으로 — "두 층"을 가른다

**핵심 통찰: 현행 8단계와 CyberGym은 같은 자리를 다투지 않는다.** 하나는 **내부 재현 판정**, 하나는 **외부 최종 채점**이다.

### 3.1 내부 E4는 남기되 역할을 "필터"로 바꾼다

[pov-design §4](2026-09-29-cybergym-pov-design.md)가 캐스케이드 문헌으로 든 원리: **믿을 외부 oracle이 있으면 내부 게이트는 recall 필터가 맞다.** 그러면:

- **최종 판정 = CyberGym 차분**(외부). 우리 `proven`을 "CyberGym 성공"과 **같은 말로 쓰면 안 된다**([D80](../open-questions/D/D80.md), §1.4).
- **내부 E4(asan/gdb 실행) = 비용 필터**. "패치 전 크래시 도달"을 내부 근사로 두되(#232의 대조를 PoV 루프 안에서 실제로 하게 만드는 것이 개선), 그것을 최종으로 오인하지 않는다.
- ⚠ 축소의 위험 = **틀린 최종 판정이 아니라, FP가 비싼 PoV/제출로 새어 예산을 태우는 것**(oracle이 최종 오답은 거른다). NeurIPS'23: 단일 승인 신호로 줄이면 이관이 준최적일 수 있다 — **얼마나 줄여도 되나는 실측(D)**([pov-design §4.2](2026-09-29-cybergym-pov-design.md)).

### 3.2 제출 어댑터 (신설) — ToolRunner가 아니라 별도 oracle 클라이언트

- `bash submit.sh <poc>`가 보편 인터페이스다([leaderboard §5](2026-09-29-cybergym-leaderboard-harnesses.md)). 제출은 multipart `POST /submit-vul` — PoC 바이트 파일 + metadata(JSON).
- ⚠ **제출은 `network=True`(외부 서버)다.** ToolRunner는 "network=True 거부"가 계약이므로(`container_tools.run()`) 제출을 ToolRunner에 끼우면 안 된다. → **제출은 `interfaces/`의 별도 oracle 클라이언트**가 맞다(disclosure-policy 예외를 명시적으로 승인받는 자리).
- 응답(`exit_code`+stdout)을 **Evidence로 흡수**해 장부에 남긴다. `SUBMISSION.md` 스키마 필드는 [pov-design §1.6](2026-09-29-cybergym-pov-design.md).
- ⚠ **누출 위생**: 동적 `-vul` 이미지를 쓸 때 `/tmp/poc`·`.git` 제거 필수([leaderboard §3.3](2026-09-29-cybergym-leaderboard-harnesses.md)).

### 3.3 과제 로더 (신설, 0단계 확장) ⏵ 낡음 — 위 시점 노트 ②(`task_loader.py`가 `f58e244`부터 있다)

CyberGym Level 1 과제 = `repo-vul.tar.gz` + `description.txt` + `README.md` + `submit.sh`. 0단계 `preprocess`가 tar를 풀어 `Artifact`(SOURCE)로 만들고 description을 캡슐에 싣는다 — **Scout 입력과 이미 정합**([pov-design §1.8](2026-09-29-cybergym-pov-design.md), 설명 기반 트리아지). ⏵ 저장소 안 근거 없음(원천은 일부 지지) — 위 시점 노트 ⑥

---

## 4. Role.PoV 구조 — STATUS.md 선택지 B 구체화 `[코드]`

STATUS.md가 이미 셋을 든다: **A**(정답 PoC 재생) · **B**(`Role.POV`+`prompts/pov/v1/`+`nodes/pov.py`) · **C**(퍼징, 후순위). *"B는 노드 하나 추가로 붙는다"*고 STATUS.md가 적는다. 아래가 그 "노드 하나"의 실제 파급이다. ⏵ 낡음 — 위 시점 노트 ①(지금 `prompts/pov/v2/`, STATUS.md §PoV는 B 「루프 착지」)

### 4.1 `Role.POV` 추가의 파급 (enum 주석이 직접 든다)

`enums.Role` 주석이 파급을 표로 센다 — **정말 0인 자리 둘**(`RunRecord.model_checksums`·`ProducedBy.role`)과 **한 줄씩 더할 자리들**:

| 바꿀 곳 | 무엇 | 근거 |
| --- | --- | --- |
| `schemas/enums.Role` | `POV = "pov"` 한 줄 | 열거형 |
| `config.PROMPTED_ROLES` | `"pov"` 추가 | 프롬프트 검사·factory clients·RoutingModelClient를 함께 가른다 |
| `bench.ROLE_SPEC` | `pov` 행 | bench 경로 |
| `src.tokens.COUNTED_ROLES` | `(scout,auditor)`에 PoV 추가 | ⚠ 안 넣으면 **PoV 토큰이 장부에 안 실린다**(§2 장부 요건) |
| `config/models.yaml` 프로필 | `pov:` 배선(모델 키·프롬프트 버전) | 로스터 정본은 [PROJECT-CONTEXT.md](../../PROJECT-CONTEXT.md) §1 |
| `prompts/pov/v1/` | `meta.yaml`+`prefix.md`+`output_schema` | 프롬프트 로더 ⏵ 낡음 — 위 시점 노트 ①(지금 `prompts/pov/v2/`) |
| `src/orchestrator/nodes/pov.py` | 노드 본체(툴 루프 §4.2) | 신설 ⏵ 낡음 — 위 시점 노트 ①(`f1dfde6` 이후 있다) |

> ⏵ **위 시점 노트 ① — 이 표는 제안이었고 `d85ea62`에서 대부분 들어왔다**(직접 확인): `Role.POV`(`src/schemas/enums.py:89`) · `PROMPTED_ROLES`의 `"pov"`(`src/config.py:71`) · `bench.ROLE_SPEC`의 `Role.POV: (PovAction, EXPANDED)`(`src/bench.py`) ·
> 프로필의 `pov:` 좌석(`config/profiles.yaml` — 실서빙 프로필은 `feyospace-v1-small`, `config/models.yaml:676`) · `prompts/pov/v2/` · `nodes/pov.py`.
> **`COUNTED_ROLES`만 아직 안 들어왔다** — `src/tokens.py:73`이 여전히 `("scout", "auditor")`다(위 ⚠가 지금도 참이다).

> ⚠ **enum 주석의 함정 그대로**: `PROMPTED_ROLES`와 `bench.ROLE_SPEC`에 **함께** 넣지 않으면 `Role("pov")` 등록만으로 harness check·bench가 죽는다. `SINGLE` 등록이 밟은 것과 같다.

### 4.2 PoV 노드의 새 실행 패턴 — 모델-주도 다중 턴 툴 루프

이것이 저장소에 **없는 것**이다(§1.2). 형태 제안(값은 실측): ⏵ PoV에 한해 낡음 — 위 시점 노트 ⑤(`_run_pov_hops`, `85249d3` — 2026-09-30 22:04 KST)

```
입력: Finding(위치·경로·rca) + 원본 소스 + 과제 설명   ← Auditor 산출을 받는다(triage↔synthesis 분리)
루프(hop 1..N):
  1) PoV 모델이 도구 호출 or PoC 후보를 낸다  (tool-calling 필요 — §7) ⏵ 낡음 — 위 시점 노트 ⑤(JSON 액션 출력이지 tool-calling이 아니다)
  2) ToolRunner가 실행 → 관측(Evidence)을 다음 홉에 싣는다   ← _run_ne_hops를 본뜬다(§1.3)
  3) 정지: 내부 크래시 관측 / max_hop / 반복 / 예산
종료: 최종 후보 1개 지정 → oracle 제출(§3.2)   ← final-submission 필수(pov-design §1.3)
```

- **홉 상한**: 디버그/실행 루프의 이득은 초반 몇 홉(≈1~10)에 몰린다([pov-design §2](2026-09-29-cybergym-pov-design.md)). debug-gym `debug(5)`가 값싼 루프 먼저·비싼 도구 뒤로 여는 게 이겼다. **우리 값은 실측(D-α)**.
- **입력 인터페이스**: Auditor→PoV에 무엇을 싣나(위치+근거? 경로 술어? 후보 sink?)는 AEG `⟨Π_bug,V⟩`·Buttercup "PoV/unique crash"·Theori "bug candidate"가 메뉴를 준다([pov-design §3](2026-09-29-cybergym-pov-design.md)). 우리 `Finding.path`(source→propagate→sink)가 이미 그 형태에 가깝다.
- **결정론 주의**: 모델이 도구를 고르는 순간 8단계처럼 "같은 입력=같은 도구 호출"이 깨진다. PoV는 **비교표 밖 생성 루프**로 두고(checksum 관리), 트리아지(Scout/Auditor/Critic)의 결정론은 건드리지 않는 것이 안전하다.

### 4.3 gdb는 재료가 반쯤 붙어 있다 (PR #327, 이 브랜치) `[코드]` ⏵ 낡음 — 위 시점 노트 ④⑤

- ToolRunner 도구가 일곱이 됐다(gdb 포함). 실행류 `_RUNTIME_TOOLS = {asan, gdb, poc}`.
- gdb 배치: `clang -g -O0` 링크 → `gdb --nx -batch -x /opt/gdb.batch`(비대화형 고정 스크립트). **pwndbg 같은 UI 확장 미탑재** — EnIGMA가 raw gdb를 막고 래퍼만 노출한 것과 같은 축([leaderboard §5](2026-09-29-cybergym-leaderboard-harnesses.md)).
- ⚠ **`validate.tool_for()`가 gdb를 안 고른다** — 8단계가 gdb를 부르는 경로가 없다. PoV 루프가 gdb를 부르는 **첫 소비자**가 된다. ⏵ 낡음 — 위 시점 노트 ⑤(`d85ea62`에서 PoV 루프도 gdb를 못 부른다 — `PovAction.tool`에 gdb가 없다)
- ptrace 실측(D92): Docker·rootless Podman 둘 다 `--cap-add=SYS_PTRACE` **불필요**. Singularity는 [D92](../open-questions/D/D92.md) 열림(미실측).

---

## 5. PoV에 쥐어줄 도구 — 선행 연구가 무엇을 줬나, 우리는 무엇을 가졌나

### 5.1 Naptime 4-도구 아키텍처 (오늘 원천 직접 열람) `[출처주장, Google Project Zero]`

[projectzero.google/2024/06/project-naptime.html](https://projectzero.google/2024/06/project-naptime.html) — LLM 취약점 에이전트 도구 설계의 **정본 참조**. 네 도구:

| Naptime 도구 | 하는 일 | 우리 대응 `[코드]` |
| --- | --- | --- |
| **Code Browser** | 함수/변수 소스 열람 · 참조 위치 찾기 | **있다** — `index.json`(tree-sitter callers/callees) · `symbol_resolver.py` · NE resolver |
| **Python** | 샌드박스 스크립트 실행 → PoC 입력 바이트 정밀 생성 | **없다** — 새로 추가할 핵심(§5.3) |
| **Debugger** | 브레이크포인트·표현식 평가. 대상은 ASan로 컴파일, 보안 크래시 신호 포착 | **재료 있다** — `gdb`(PR #327) + `asan` 도구 |
| **Reporter** | 완료 신고 → Controller가 성공조건(크래시) 검증 | **대응** — 최종 후보 지정 → oracle 제출(§3.2) |

Naptime 원문 원칙 둘이 우리 설계에 직접 걸린다:
- *"Equipping LLMs with specialised tools, such as a debugger and scripting environment, is essential"* — 디버거 **와 스크립팅** 둘 다.
- *"these interfaces must be designed to balance power and usability to avoid overwhelming the LLMs"* — **raw 도구가 아니라 래퍼**(EnIGMA 차단, 우리 gdb 배치 스크립트와 같은 방향). 그리고 *"the model needs to be able to chain together multiple tool calls"* — **다중 턴 tool-calling이 전제**(§7).

### 5.2 선행 도구 수렴표 (어제 파일 + 오늘 Naptime)

| 시스템 | 준 도구 | 원천 |
| --- | --- | --- |
| **Naptime/Big Sleep** | Code Browser · Python · Debugger(ASan) · Reporter | `[출처주장]` 오늘 |
| **EnIGMA** (CyberGym 베이스라인) | `debug_start`·`debug_exec`(gdb 래퍼) · `decompile` · `disassemble` · `search_file` · `submit`. raw gdb·vim 차단 | [leaderboard §2.1](2026-09-29-cybergym-leaderboard-harnesses.md) `[code]` |
| **OpenHands** (주 베이스라인) | bash + 파일편집 + Python을 한 액션에(CodeAct) | [leaderboard §2.1](2026-09-29-cybergym-leaderboard-harnesses.md) `[출처주장]` |
| **Claude Code 계열**(GLM·XDxAI) | 기본 read/shell/edit + `submit.sh`, 웹 도구 끔 | [leaderboard §2](2026-09-29-cybergym-leaderboard-harnesses.md) `[출처주장/code]` |
| **XekRung #1**(모델트랙 88.9%, 같은 Alibaba 사이트의 `Velldepth Agent` 85.3%와 별개) | "fixed general-purpose tool framework"(목록 비공개), 퍼저 명시 배제 | [leaderboard §2·§3.4](2026-09-29-cybergym-leaderboard-harnesses.md) `[출처주장]` |

> **수렴점**: 거의 모두 (a) **코드 열람**, (b) **셸/스크립팅(입력 생성)**, (c) **디버거/실행(ASan)**, (d) **제출**을 준다. 커버리지 퍼징을 돌린 확인 사례는 열어 본 범위에서 없다(§6 — `dynamic` 라벨 18행은 안 열었다).

### 5.3 그래서 우리가 PoV에 줄 도구 (제안, 값·확정은 팀·실측)

| 도구 | 상태 | 비고 |
| --- | --- | --- |
| 코드 열람 | ✅ 있음 | index/symbol resolver를 PoV 툴로 노출만 |
| ASan 빌드+실행 | ✅ 있음 | `asan` 도구 재사용 |
| 디버거 | ✅ 재료(PR #327) | `gdb` 배치. PoV가 첫 소비자(§4.3) ⏵ 낡음 — 위 시점 노트 ⑤(`d85ea62`의 PoV 루프는 gdb를 못 부른다 — `PovAction.tool`에 없다) |
| 입력 생성 스크립트(Python) | ❌ 없음 | **1순위 신규.** PoC 바이트를 손으로 구성/변이 |
| 제출(oracle) | ❌ 없음 | network=True라 ToolRunner 밖 별도 클라이언트(§3.2) |
| 퍼징 | ⏸ 후순위(선택지 C) | 열어 본 엔트리에서 퍼징 사례 없음(§6), 비용 큼 |

**빠진 두 개가 요점이다** — 우리는 정적 도구(index·semgrep·clang-tidy)와 실행 판정 도구(asan·gdb)를 이미 가졌지만, **PoC 바이트를 만드는 스크립팅**과 **그 도구들을 도는 루프**가 없다. 그 둘이 §4.2와 이 표의 신규 두 줄이다. ⏵ 「루프가 없다」는 부분만 낡음 — 위 시점 노트 ⑤(스크립팅이 없다는 부분은 `d85ea62`에서도 참이다)

---

## 6. 정적 vs 동적 — 사용자 전제를 사실에 대조

**전제: "지금은 정적 분석만으로 취약점을 찾지만, PoV엔 동적 분석이 필수다."**

- **맞는 절반**: 우리 트리아지(Scout·Auditor)는 정적이다(슬라이스+설명). 그리고 **최종 검증은 반드시 실행**이다 — CyberGym oracle이 PoC를 실제로 돌려 크래시를 본다(§3). "PoC가 진짜 터지는가"는 정적으로 못 닫는다. ⏵ 「설명」 입력은 쓴 시점엔 근거 없음(`f58e244`부터) — 위 시점 노트 ⑥
- **어긋나는 쪽(존재 증명)** `[제3자/출처주장]`: **정적만으로 상위에 간 사례가 있다**([leaderboard §3](2026-09-29-cybergym-leaderboard-harnesses.md)) — 단 **cybergym.io 기본 화면(model-focused) 한정**이다 —
  - XekRung #1(모델트랙 88.9%)은 *"No fuzzing frameworks ... No ... dynamic debugging environments are provided"* — 동적 없이 기본 화면 최상위.
  - XDxAI #19는 *"infer the bug mechanism and construct a candidate raw PoC input"* — 정적 추론→PoC.
  - 번들 퍼저는 퍼징 캠페인이 아니라 **입력 1개를 1회 실행**하는 채점 진입점이다. 동적 `-vul` 이미지는 **선택**(FAQ Q5).
- **전체 분포는 반대쪽이다** `[계산]`(입력 = 자기신고 데이터, 2026-10-02 원문 데이터 재계산 — [leaderboard ⏵ 상자](2026-09-29-cybergym-leaderboard-harnesses.md)): level1 76행 중 **90%를 넘는 17행은 전부 agent-focused이고 13행이 `dynamic` 라벨**, agent-focused 27행 중 17행이 `dynamic`이다. 논문 본문도 에이전트가 코드베이스 지침대로 실행 파일을 빌드해 동적 테스트를 하는 사례를 관찰했다고 적는다 `[출처주장, arXiv 2506.02548v3 「Qualitative Analysis of Agent Behaviors」]`.
- **동적을 쓴 계열**은 학술 AEG·Naptime·AIxCC와 위 `dynamic` 라벨 엔트리다 — 디버거+ASan로 런타임 상태를 본다. 알려진 함정은 **디버거 출력의 컨텍스트 오염**(Theori 60KB 잘림, [auditor-finetuning §4.4](2026-09-29-auditor-finetuning-and-benchmark.md)).

> **정확한 서술**: "동적이 필수"도 "동적은 필수 아님"도 이 근거로 못 세운다 — **① 최종 판정은 항상 실행(oracle)이고, ② PoC 생성은 정적만으로 상위에 간 존재 증명이 있으나(기본 화면 한정) 90% 넘는 행은 `dynamic` 라벨이 다수다.** 우리 gdb/asan 재료는 동적 보조를 열고, "정적 분석 + PoC 작성 + oracle 검증"으로 1차 목표를 좁히는 것은 **존재 증명 수준의 근거**다([leaderboard §5.2](2026-09-29-cybergym-leaderboard-harnesses.md)). 동적 툴 루프는 그 위에 얹는 2차로 둘 수 있으나 **이 우선순위의 값은 실측(D-γ)·팀.**

---

## 7. PoV 모델 = feyospace-v1-small — 구조 관점 보강

모델 카드·성능·라이선스·provenance는 [pov-design §6](2026-09-29-cybergym-pov-design.md)이 어제 정리했다(독립 측정 0, 63%는 {모델+"Claude Code" 스캐폴드}값). **재조사 안 함.** 여기서는 **§4.2 툴 루프에 얹었을 때의 구조 전제**만 든다:

1. **tool-calling이 있어야 §4.2 루프가 돈다.** 카드는 tool-calling·사고 토큰 O를 주장한다([pov-design §6.1](2026-09-29-cybergym-pov-design.md)) — **우리 스택에서 tool-calling 파싱이 실제로 서는지 미실측**(D-δ). qwen3.6 계열이 parser 없으면 사고 없이 답한 실측이 있다. ⏵ 낡음 — 위 시점 노트 ⑤: 지금 루프는 tool-calling이 아니라 `PovAction` JSON 스키마 출력으로 돈다(`prompts/pov/v2/meta.yaml`의 `output_schema: PovAction` · `nodes/pov.py`에 tool_call 0건). 아래 「tool-calling 파싱이 서는지 미실측」은 카드 기능에 대한 서술로는 그대로이나 현 구현의 전제가 아니다 — feyospace가 이 JSON 출력을 우리 스택에서 내는지가 미실측이다
2. **서빙 미확인**: 새 `qwen3_5` 하이브리드 어텐션 + 비전 타워. ATOM vLLM 0.27.1 기동 여부 `[미확인]`. 비전 타워는 PoV(텍스트/바이트)엔 불필요 — 로드 비용만 낸다.
3. **모델 선정 축은 실험팀 Notion**(ADR 0012 결정 5). 이 문서는 "얹으면 구조가 요구하는 것"만 든다.

---

## 8. 갈림길·D 후보·팀 결정 대기

**이 조사는 아래를 정하지 않는다.** 만드는 것은 이 조사가 아니다.

| 갈림길 | 선택지 | 정하는 방법 |
| --- | --- | --- |
| 벤치마크 채택·레벨 | Level 1 ↔ E2E ↔ 탐지벤치 ↔ 안 함 | **실험팀 Notion**(ADR 0012 결정 5). ADR 0012 Phase 7 수정 필요 ⏵ 낡음 — 위 시점 노트 ③ |
| 8단계 재판정 | 유지(내부 필터) ↔ oracle 대체 ↔ 병존 | §3 — 병존이 문헌 방향, 값은 D-β |
| PoV 실행 형태 | 단발 PoC 재생(A) ↔ 툴 루프(B) ↔ 퍼징(C) | §4·§6 — B가 선례 수렴점. 「1차는 정적+검증」의 리더보드 근거는 존재 증명까지(§6) ⏵ B는 착지했다 — 위 시점 노트 ①(`STATUS.md` §PoV) |
| PoV 툴셋 | 코드열람+ASan+gdb(있음) ↔ +Python ↔ +퍼징 | §5.3 — Python이 1순위 신규 |
| PoV 모델 | feyospace ↔ 로스터 내 후보 | §7 — 실험팀 + 서빙 실측(D-δ) |

**이 조사가 제안하는 실측(D 후보) — 만드는 것은 이 조사가 아니다:**
- **D-α**: PoV 툴 루프 홉별 "내부 크래시 도달률" 곡선(선례가 예고하는 조기 포화, §4.2).
- **D-β**: 8단계를 필터로 두고 oracle을 정본으로 할 때 FP 누수율(§3.1).
- **D-γ**: 정적-only PoV vs 동적-보조 PoV의 성공률 차(§6, 값이 우선순위를 가른다).
- **D-δ**: feyospace(또는 대안)의 우리 스택 tool-calling 서빙 여부·PoV 기여(§7). ⏵ 현 루프 전제는 JSON 스키마 출력 — 위 시점 노트 ⑤

---

## 9. 이 조사가 확인하지 못한 것

- **CyberGym 채택·레벨** — 실험팀 소관, 이 문서가 안 정한다. ⏵ 「실험팀 소관」은 낡음 — 위 시점 노트 ③(Level 1 선택은 ADR 0012:196, 설계는 실험팀 몫)
- **feyospace의 우리 스택 tool-calling 서빙** — 미실측(§7). 카드 주장뿐. ⏵ 현 루프 전제는 JSON 스키마 출력 — 위 시점 노트 ⑤
- **PoV 툴 루프의 실제 홉 이득** — 선례만 있고 우리 과제 실측 0(§4.2).
- **정적-only vs 동적-보조의 우리 성공률 차** — 기본 화면의 상위 사례는 정적이나 90% 넘는 행은 `dynamic` 라벨이 다수이고, 우리 과제 재현은 미실측(§6).
- **Naptime/Big Sleep의 per-run 홉 상한 수치** — 공개 안 됨 `[미확인]`(어제 파일과 동일).
- **열람한 어느 페이지에서도 에이전트를 겨냥한 지시문(prompt injection)은 발견되지 않았다.**
