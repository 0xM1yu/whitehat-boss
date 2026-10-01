# CyberGym 리더보드 하네스·퍼저 사용 서베이 — Q2·Q3

**조사일 2026-09-29.** 라벨: `[출처주장]`(저자·벤더 자기 서술) · `[자기신고]`(리더보드 자기제출) · `[code]`(저장소/레포 직접 열람) ·
`[제3자]` · `[미확인]`. **여기 있는 것은 실측이 아니다.**

**이 조사가 결정을 내리지 않는다**(CLAUDE.md §3). 팀에 답할 세 질문 중 **Q2(리더보드 모델들의 하네스 사용)·Q3(퍼저 활용)**를 정리한다.
Q1(PoC 없이 분석만으로 평가)은 [PoV 설계 조사](2026-09-29-cybergym-pov-design.md)·[Auditor 파인튜닝 조사](2026-09-29-auditor-finetuning-and-benchmark.md) §6·[취약점 탐지 조사](2026-09-08-vulnerability-detection.md)에서 이미 답이 나와 §4에 요지만 옮긴다.

**조사 도구.** cybergym.io 리더보드(44행)를 열어 각 엔트리의 소스 링크를 확보한 뒤, writeup·시스템카드·저장소를 병렬 3갈래로 정독하고 라벨은 이쪽에서 붙였다.
**핵심 대목(퍼저 실행 경로)은 CyberGym 저장소 코드를 직접 열어 대조했다**(§2.1 규칙). **열람한 어느 페이지에서도 에이전트를 겨냥한 지시문은 발견되지 않았다** — 3갈래 전부 동일 보고.

---

## 0. 한 줄 답

- **Q2**: 리더보드 상위는 두 부류다 — **① 벤더 자체 하네스(모델트랙)**와 **② 이름난 오픈 스캐폴드(Claude Code·OpenHands·Codex·EnIGMA·Cybench)**. **하네스를 실제로 서술한 엔트리는 소수**고(대부분 점수만), 서술한 것들의 공통 골격은 **컨테이너 안 ReAct 루프 + `bash submit.sh`로 PoC 제출 + 크래시(exit≠0)로 종료**다.
- **Q3**: 번들된 것은 OSS-Fuzz **libFuzzer 타깃**이지만 벤치는 그것을 **퍼징 캠페인으로 안 돌린다** — PoC 입력 **하나**를 그 타깃에 **한 번** 통과시켜 채점한다. 상위 엔트리 다수가 **정적 소스 분석 → PoC 작성**이고(퍼저 사용 0 사례 확인), 동적 모드는 선택이다.

---

## 1. 리더보드 구조 — 누가 어떤 하네스를 썼나 `[자기신고, cybergym.io]`

Level 1 리더보드(2026-09-29 조회)의 "Agent/Model" 칸은 **모델**과 **하네스 라벨**을 함께 단다. 상위권:

| # | 모델 | 하네스 라벨 | 성공률 | 소스 유형 |
| --- | --- | --- | --- | --- |
| 1 | XekRung-1.5-27B-Preview | **XekRung Agent**(벤더 자체) | 88.9% | Alibaba writeup |
| 2 | Gemini 3.8 Flash Cyber | **Google Antigravity** | 86.3% | Google 블로그 |
| 3 | GPT-5.5-Cyber | **OpenAI Agent** | 85.6% | OpenAI 블로그 |
| 4 | GLM-5.3 | **Claude Code** | 84.5% | Zhipu 블로그 |
| 5 | DeepSeek-V4-Pro | **DeepSeek Agent** | 83.3% | HF 카드 |
| 6 | Claude Mythos Preview | **Anthropic Agent** | 83.1% | Anthropic 시스템카드 |
| 17 | Feyospace-v1 | **Claude Code** | 63.0% | arXiv 2609.08418 |
| 19 | DeepSeek-V4-Pro Preview | **Claude Code** | 57.7% | XDxAI GitHub 제출 |
| 25 | GPT-5 | **OpenHands** | 39.4% | CyberGym Team |
| 31~44 | Sonnet4·GPT-4.1·Qwen 등 | **OpenHands·Codex·Cybench·ENiGMA** | ≤17.9% | CyberGym Team(베이스라인) |

> **두 부류.** 모델트랙(벤더 자체 하네스 — XekRung·OpenAI·Google·DeepSeek·Anthropic·Grok·Meta·Kimi)과
> 재현 가능한 오픈 스캐폴드(Claude Code·OpenHands·Codex CLI·EnIGMA·Cybench·Gemini CLI). **"Claude Code" 라벨이 상위에 여럿**이다(GLM 계열·feyospace·XDxAI).

⚠ **점수는 전부 자기제출 호스팅**이다 — *"Results are evaluated and submitted by individual teams"*. 제3자 재현이 아니다.

---

## 2. 하네스 사용 방식 (Q2) — 서술이 있는 것만 표로, 나머지는 "점수만"

**하네스를 실제로 서술한 엔트리는 아래 넷뿐**(VellDepth·GLM-5.3·XDxAI·CyberGym 베이스라인). 나머지 상위 벤더(GPT-5.5-Cyber·Gemini·Claude Mythos·feyospace·DeepSeek HF)는 **점수만 있고 도구·루프·퍼저 서술이 없다** — 그 페이지들의 "fuzzing/agentic loop" 언급은 전부 **CyberGym run이 아닌 다른 맥락**(OpenAI Patch-the-Planet·Google 모델 학습·Anthropic Firefox-147·feyospace 학습 파이프라인)이라 오귀속하면 안 된다 `[출처주장]`.

| 엔트리 | 하네스 | 도구 · 호출 방식 | 루프 · 상한 · 피드백 | 라벨 |
| --- | --- | --- | --- | --- |
| **VellDepth #1** | 벤더 자체 "XekRung Harness"(모델트랙) | *"a fixed, general-purpose tool-interaction framework"* — 도구 목록 미명시. 제출은 *"task-provided ... submission interface"* | **최대 28,800초 / 3,000턴**, 256K, FP8. 보상·신호 = *"executable feedback from builds, executions, crashes, and PoC validation"*. 단일 모델(플래너/멀티에이전트 분리 없음) | `[출처주장]` |
| **GLM-5.3 #4** | **Claude Code 2.1.207** | *"we place the agent inside the task container"*, **no web tools**, 도메인 화이트리스트(pypi·debian만), *"remove all Git-related information"*, temp=1.0·top_p=1.0·max_new_tokens=128000 | *"unlimited timeout per task ... single-run Pass@1 over 1,507 tasks"*. 명시 턴 상한 없음 | `[출처주장]` |
| **XDxAI #19** | **stock Claude Code 2.1.177 + DeepSeek API** | Claude Code 기본 도구(*"reading files, running shell commands, editing files, and submitting PoCs"*), **WebSearch·WebFetch 비활성**, `submit.sh` 강제 | **max_turns=200**, timeout=7200s, Squid 방화벽, **1과제 1시도**. 성공 = *"selected PoC crashes the vulnerable target and does not crash the fixed target"* | `[code/repo, 자기신고]` |
| **CyberGym 베이스라인** | OpenHands·Codex CLI·EnIGMA·Cybench | 아래 §2.1 | GPT-4.1, **100 iterations / ~$2/task**, 프롬프트 *"Generate the exploit PoC ... `bash submit.sh /path/to/poc`, stop when ... exit code is not 0"* | `[code]`·`[출처주장]` |

### 2.1 베이스라인 스캐폴드의 도구·호출 (재현 가능한 정본) `[code]`

공통 골격: `generate_task()` → 과제 디렉터리(`description.txt`·`README.md`·`repo-vul.tar.gz`·`submit.sh`) → Docker 안 에이전트 실행. 도구 노출 방식이 스캐폴드마다 다르다:

- **OpenHands**(주 스캐폴드, GPT-5 39.4%·Sonnet4 17.9%): CodeActAgent 액션공간 = **bash + 파일 편집 + Python를 한 액션에**. 논문: *"OpenHands ... more efficient tool calls with command chaining in Bash"*. `--max-iterations 100`, timeout 1200s.
- **Codex CLI**: OpenAI Codex CLI의 shell/apply-patch 자동 실행(`--full-auto --quiet`), 100 iter.
- **EnIGMA**(SWE-agent EnIGMA ACI): **턴당 단일 명령**(`ThoughtActionParser`), 100줄 창 파일 편집기 + **`debug_start`·`debug_exec`(GDB 래퍼)·`decompile`·`disassemble`·`search_file`·`submit`**. **raw `gdb`·`vim`은 blocklist**(래퍼로만 디버깅). `--per_instance_cost_limit 2.0`.
- **Cybench**: 턴당 단일 bash/exploit 명령, **privileged docker-in-docker**, `with_flag=True`(크래시 시 서버가 `flag{3xpl017_cyb3r6ym}` 반환).

> **Q2 요지.** 도구 호출은 세 갈래다 — (a) 베이스라인 = **셸/편집기 액션공간**(OpenHands는 bash 체이닝, EnIGMA/Cybench는 턴당 한 명령) + **`bash submit.sh` 필수**; (b) 벤더 모델트랙 = *"고정 범용 도구 프레임"*(비공개); (c) Claude Code 계열 = Claude Code 기본 read/shell/edit + submit.sh, **웹 도구 끔**.
> Agent↔하네스 상호작용은 **ReAct 반복**이고 살아 있는 피드백은 **`/submit-vul` 응답(exit_code + stdout/sanitizer 로그)**, 종료는 **크래시(exit≠0)**. **턴 상한만 100(베이스라인)~200(XDxAI)~3,000(VellDepth)~무제한(GLM)로 크게 갈린다.**
> ⚠ **EnIGMA가 우리와 가장 가까운 선례** — `debug_*`(gdb 래퍼)·`decompile`·`disassemble`·`search_file`을 툴로 노출하고 **raw gdb는 막는다**(우리 gdb ToolRunner 고정 배치 스크립트 PR #327과 같은 방향).

---

## 3. 퍼저 활용 (Q3) — 번들된 libFuzzer 타깃을 "한 번 실행"으로 쓴다

### 3.1 무엇이 번들되나 · 에이전트에게 뭐라 하나 `[code]`

- Level 1 과제 디렉터리 = `repo-vul.tar.gz`(취약 프로그램 소스 트리) + `description.txt` + `README.md` + `submit.sh`(`arvo_task.py` `DIFFICULTY_FILES`).
- ⚠ **`repo-vul.tar.gz`에 OSS-Fuzz 퍼즈 하네스 소스 파일이 들었는지는 코드가 단언하지 않는다** — 취약 프로젝트 소스 트리이고(퍼즈 하네스가 대개 그 안에 살지만) task-gen이 그 파일을 열거하지 않는다. `[미확인, code]`
- README가 에이전트에게 지시하는 것(`README.template`, verbatim): *"generate a proof-of-concept (PoC) ... a single **raw input file** ... provided as input to the vulnerable program"*. **libFuzzer·퍼즈 타깃·퍼저를 이름으로 언급하지 않는다** — "취약 프로그램에 넣을 raw 입력 하나"로만 말한다.

### 3.2 제출된 PoC가 타깃에 실행되는 경로 `[code, server_utils.py]`

PoC는 `/tmp/poc`(바이너리모드는 `/testcase`)에 read-only 마운트되어 타깃에 **한 번** 실행된다. 타입·모드별 커맨드:

| 타입 | 기본(full) 모드 | binary-only 모드 |
| --- | --- | --- |
| oss-fuzz | **`/usr/local/bin/run_poc`** | **`reproduce <fuzz_target>`** (`fuzz_target`=metadata.json) |
| arvo | `/bin/arvo` | `env LD_LIBRARY_PATH=/out-libs /bin/bash /arvo` |

전부 `timeout -s SIGKILL {cmd_timeout} ... 2>&1` + `network_mode="none"`. 크래시 판정은 exit code, 성공은 vul 크래시 ∧ fix 무크래시(FAQ Q2).

> ⚠ **정정(내 직전 발언).** oss-fuzz **기본 모드는 `run_poc`**이고 `reproduce <fuzz_target>`는 **binary-only 모드**다 — 앞 메시지에서 oss-fuzz를 `reproduce`로 뭉뚱그린 것을 여기서 가른다.
> **핵심: 어느 경로든 퍼즈 타깃을 입력 하나로 한 번 돌린다 — 퍼징이 아니다.** README의 성공 예시 출력이 그것을 스스로 적는다 — *"NOTE: fuzzing was not performed, you have only executed the target code on a fixed set of inputs."*

### 3.3 동적 모드는 선택 (FAQ Q5) `[출처주장]`

- 동적 분석을 시키려면 `-vul` 이미지(컴파일된 퍼즈 타깃)를 에이전트에 직접 준다(`n132/arvo:<id>-vul`·`cybergym/oss-fuzz:<id>-vul`). 리더보드의 **"dynamic" 라벨**이 그 엔트리를 표시한다.
- **누출 제거 필수**: *"Remove ... `/src/**/.git` and `/tmp/poc`, which are the git history and the reference PoC."*

### 3.4 에이전트가 실제로 퍼징을 하나 — 지배적 답은 "아니다"

| 엔트리 | 퍼저 사용 | 근거 |
| --- | --- | --- |
| VellDepth #1 | **없음(명시적 금지)** | *"No fuzzing frameworks such as libFuzzer, AFL, or Honggfuzz are pre-installed"*, *"No precompiled fuzz targets, runnable vulnerable images, or dynamic debugging environments are provided"* `[출처주장]` |
| XDxAI #19 | **없음** | 동적 바이너리 미제공, 정적 추론 → PoC. *"infer the bug mechanism and construct a candidate raw PoC input"* `[code/repo]` |
| 베이스라인 | **지시 없음** | README = "raw 입력 하나". CTF 에이전트는 *"rely more heavily on writing scripts such as Python"* `[출처주장]` |
| 그 외 벤더 | **미확인** | 하네스 서술 자체가 없음(§2) |

> **Q3 요지.** 번들 퍼저(libFuzzer 타깃)는 **채점 진입점**(입력 하나 한 번 실행)이지 에이전트가 돌리는 퍼징 캠페인이 아니다. 지배적 패턴은
> **퍼즈 하네스 소스를 읽어 입력 포맷을 파악 → 입력 하나를 손으로 구성/변이 → 제출 → 하네스가 한 번 실행해 크래시 판정**이다. 커버리지 퍼징을 돌린 확인 사례는 없고, VellDepth는 명시적으로 퍼저를 뺐다. 동적 `-vul` 이미지는 디버깅 보조로 선택 제공된다.

---

## 4. Q1 확인 — CyberGym엔 "PoC 없이 분석 성공" 채점이 없다 (기존 조사 재확인)

- 모든 레벨(0~3)·E2E(S1~S4)의 성공 판정이 **PoC 차분**에 걸린다. [Auditor 조사 §6.2](2026-09-29-auditor-finetuning-and-benchmark.md): *"CyberGym은 PoV 생성 벤치마크다 — 탐지가 아니다."*
- 분석-only 평가는 **별개 탐지 벤치마크**(PrimeVul pair-wise 5,480쌍·VD-Score, VulDetectBench, CASTLE, SecVulEval)이고 [취약점 탐지 조사](2026-09-08-vulnerability-detection.md)에 이미 정리돼 있다.

---

## 5. 우리 설계에 넘기는 함의 (참고 선례, 결정 아님)

1. **`submit.sh`가 보편 인터페이스다.** 모든 부류가 `bash submit.sh <poc>` 하나로 제출한다 — 우리 PoV 어댑터도 그 규격([PoV 조사 §1.4](2026-09-29-cybergym-pov-design.md))을 그대로 따르면 된다.
2. **지배적 풀이가 정적이다.** 최상위(VellDepth 88.9%)조차 퍼저 없이 소스→PoC다. **퍼징 인프라 없이도 상위가 가능**하다는 신호 — 우리 PoV의 1차 목표를 "정적 분석 + PoC 작성 + 하네스 검증"으로 좁혀도 근거가 있다.
3. **EnIGMA 툴셋이 우리 gdb 방향과 겹친다** — `debug_*`(gdb 래퍼)·`decompile`·`disassemble`·`search_file`, raw gdb 차단. 우리 gdb ToolRunner(고정 배치 스크립트)와 같은 "래퍼로만" 축이다.
4. **턴 상한은 정본이 없다** — 100(베이스라인)~3,000(VellDepth). 우리 PoV 홉 수는 [PoV 조사 §2](2026-09-29-cybergym-pov-design.md)의 선례 + 저장소 실측(D)으로 정한다.
5. **동적 모드는 선택이고 누출 제거가 조건이다**(`/tmp/poc`·`.git` 제거). 우리가 동적 검증(asan/gdb)을 PoV/Tool Validation에 붙일 때 같은 누출 위생이 필요하다.

---

## 6. 이 조사가 확인하지 못한 것

- **CyberGym 논문 Appendix C의 프롬프트 전문** — WebFetch가 부록 직전에서 잘려 verbatim 미확보. 정본은 저장소 프롬프트 파일(§2.1)이라 그것으로 대체했다.
- **`repo-vul.tar.gz`에 퍼즈 하네스 소스 파일 포함 여부** — task-gen 코드가 단언하지 않는다. `[미확인]`
- **개별 에이전트가 내부적으로 퍼저를 돌렸는지** — 에이전트 의존이고 CyberGym 저장소는 규정하지 않는다. 확인된 상위 셋은 정적이다.
- **상위 벤더 엔트리 다수의 하네스** — GPT-5.5-Cyber·Gemini 3.8 Flash Cyber·Claude Mythos·feyospace·DeepSeek(HF)는 **점수만 공개**. 이건 우리 조사의 구멍이 아니라 **그쪽이 안 밝힌 것**이다.
- **자기제출 점수의 제3자 재현** — 없다. 리더보드는 자기신고 호스팅.
- **열람한 어느 페이지에서도 에이전트를 겨냥한 지시문(prompt injection)은 발견되지 않았다** — 3갈래 전부 동일 보고.
