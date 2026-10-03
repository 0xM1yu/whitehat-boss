# PoV 프롬프트 도구 조사 — v2 툴 루프 프롬프트 근거 (2026-09-30)

**목적.** Phase 3 PoV 툴 루프(Option C)의 프롬프트를 v1(단발) → v2(다중 홉)로 바꾸는 근거.
"PoV 모델이 쓰는 도구가 무엇이고, 그것을 어떻게 가르칠까"를 (1) 우리 루프 코드에서 직접,
(2) 외부 문헌(CyberGym·퍼징 LLM·에이전트 루프)에서 조사했다. **이 문서는 남이 주장한 것과
우리 코드 사실을 나눠 적는다**(CLAUDE.md §0·§6 — research는 실측 아님).

## 1. PoV 모델이 우리 루프에서 실제로 쥐는 도구 (우리 코드)

`runner._run_pov_hops` + `nodes/pov` + `schemas/pov` 기준. 매 홉 모델은 `PovAction` 하나를 낸다:

- **`gen`** — `spec`(`PovSpec`)을 싣는다. 순수 함수 `assemble_poc`(orchestrator)가 결정론적으로
  바이트를 만들고, `pov.run_tool`이 `poc` 도구로 대상에서 실행해 Evidence를 낸다. **모델이 쓴
  코드를 실행하지 않는다.** `PovSpec` 세그먼트는 `literal`(hex)·`repeat`(byte×count)·`int`
  (value·size≤8·endian)뿐이다(≤8 MiB, ≤4096 세그먼트). **계산 크로스필드(길이-of·체크섬-of)가
  없다.**
- **`inspect`** — 새 PoC 없이 `tool ∈ {asan, gdb, build, poc}`로 현 후보를 진단. ⏵ **낡음** — `d85ea62`(2026-10-02) 기준 `PovAction.tool`은 `asan`·`build`·`poc` 셋이다(아래 ⏵ 노트).
- **`submit`** — 루프 종료. 마지막 gen의 PoC가 final.

관측 되먹임(`_pov_observation`): `tool=… action=… ok=… exit=… kind=…`(+error). 정지 사유
(`PovStopReason`): submitted·repeat_spec·max_hop·parse_failed·context_overflow·gateway_error.

**dev_fake 한계(코드 사실):** fake 도구는 workdir 대상 소스 존재만 보고 ok를 낸다
(`fake_tools._run_repro`) — PoC 바이트를 안 읽는다. `gdb`는 `TOOL_KINDS`에 없어 dev_fake에서
inspect(gdb)는 실패한다(실 컨테이너엔 gdb 있음). 바이트 materialize·실크래시 판정은 호스트 후속.

⏵ **2026-10-02 (`d85ea62` 기준 main) — 위 `gdb` 문장은 이유가 바뀌었고, `tool ∈ {asan, gdb, build, poc}`는 틀렸다.**
① PR #327이 `5cf5c20`(2026-10-01 03:29 KST)로 머지되며 `TOOL_KINDS`에 `"gdb": EvidenceKind.CRASH`가 들어갔다
(`src/adapters/fake_tools.py:30`) — 이 문서를 쓴 **다음 날**이다. 그래서 「`TOOL_KINDS`에 없어서」라는 이유는 낡았다.
② 그러나 **PoV 루프가 낼 수 있는 `inspect.tool`은 지금도 셋이다** — `PovAction.tool: Literal["asan", "build", "poc"]`
(`src/schemas/pov.py:170`)라 `tool="gdb"`는 **스키마에서 거절**되고, v2 프롬프트(`prompts/pov/v2/prefix.md:13-14`)도
`asan`·`build`·`poc`만 안내한다. `gdb`는 `96a80fe`(2026-09-30 22:04 KST)에서 그 Literal에서 빠졌다.
그러므로 **dev_fake에서도 `inspect(gdb)`는 서지 않는다**(막는 곳이 `TOOL_KINDS`가 아니라 `PovAction` 스키마다) — 결론은 그대로 참이고 이유만 바뀌었다.
③ PR #380(`feat/pov-information-wiring`)이 그 Literal에 `gdb`를 더했다(이슈 #360의 결정) — **아래 ⏵ 2026-10-03이 그 뒤의 사실이다.** ②는 `d85ea62` 기준 서술이다.

⏵ **2026-10-03 (`a696f85` 기준 main) — #380이 머지돼 ②는 낡았다.** `PovAction.tool`의 Literal은 이제 `"asan", "build", "gdb", "poc"`다(`src/schemas/pov.py:185`) — 스키마는 `inspect(gdb)`를 받는다. **그러나 모델은 gdb를 쓰라고 안내받지 못한다**: 배선된 v2 프롬프트는 여전히 `asan`·`build`·`poc`만 안내하고(`prompts/pov/v2/prefix.md` — gdb 언급 0), v3(`prompts/pov/v3`)는 미배선이다. 또 `run_tool`은 모든 도구에 `argv=[tool]`만 넘겨 **어떤 도구도 PoC 바이트를 입력으로 받지 않는다**(#354) — gdb만의 한계가 아니다. D92는 프롬프트에 gdb 사용 규칙을 넣는 일에 ASAN 수리·실행 검증(#326)을 선행시킨다. 이슈 #360은 그래서 열려 있다.

## 2. 외부 문헌 (남이 주장한 것 — 인용)

- **CyberGym 과업·채점.** 1,507 인스턴스/188 프로젝트. "patch 전 빌드에서 새니타이저 크래시 ∧
  patch 후 빌드에서 크래시 없음"이면 성공. 제출물은 raw 바이트 PoC(수 바이트~1 MB+).
  [arXiv 2506.02548](https://arxiv.org/abs/2506.02548) · [repo](https://github.com/sunblaze-ucb/cybergym)
- **정량 실패 모드는 논문 Table 9다**(OpenHands+GPT-4.1, Level 1) `[출처주장]` — 조기 종료(포기하거나 성공을 잘못 선언)
  비율 **0.30** · 긴 평문 PoC(`'A'`를 1,000자 그대로 찍는 식) 비율 **0.19** · 평균 grep/ls/find 시도 **30.1±0.5회**(실패 **7.8±0.3회**).
  [arXiv 2506.02548v3 Appendix D 「Failure Mode Analysis」](https://arxiv.org/html/2506.02548) (2026-10-02 원문 대조).
  ⚠ **「지배적 실패는 취약 경로 미도달」은 이 논문의 결과가 아니다** — 그 표현은 부록 사례 궤적에서 *에이전트 자신이* 한 말이고 `[미확인]`이다.
  논문은 오히려 에이전트가 Python·Bash 스크립트로 복잡한 PoC를 구성할 수 있다는 점과 긴 PoC를 압축 표현 대신 평문으로 쓰는 실패를 함께 적는다.
- **정답 PoC가 길수록 성공률이 낮다.** **정답(ground-truth) PoC가 100바이트보다 긴 인스턴스**(전체의 65.7%)에서 OpenHands(GPT-4.1·Claude-Sonnet-4)의
  성공률이 약 10%다 `[출처주장, arXiv 2506.02548v3 Fig. 7]`. ⚠ 기준은 에이전트가 낸 PoC의 길이가 아니라 **과제의 정답 PoC 길이**다 —
  과제 난이도의 프록시이지 PoV 스펙(`PovSpec`)의 표현력 한계나 에이전트 PoC 길이의 효과가 아니다.
- 확장판 CyberGym-E2E는 에이전트 프롬프트(Figure 4)에서 "작게 시작(10–50 bytes)·매직/헤더 먼저"를 명시한다
  [arXiv 2606.04460](https://arxiv.org/html/2606.04460) `[출처주장]`(2026-10-02 원문 대조 — 프롬프트 문구일 뿐 효과의 측정은 아니다).
- **Python 생성 스크립트 > raw/hex 직접 출력.** LLM은 비텍스트(바이너리) 직접 생성이 약하다 —
  체크섬·길이를 코드로 계산해 바이트를 뽑는 스크립트가 정공법. [G2FUZZ, USENIX Sec'25,
  arXiv 2501.19282](https://arxiv.org/abs/2501.19282) · [OSS-Fuzz-Gen](https://github.com/google/oss-fuzz-gen)
  · [verse.systems](https://verse.systems/blog/post/2024-03-09-using-llms-to-generate-fuzz-generators/)
- **ReAct/Reflexion 루프 규율.** 한 턴 한 액션, 관측을 읽고 실패 이유를 한 줄로 말한 뒤 다음
  액션, 같은 실패 액션 반복 금지. [ReAct arXiv 2210.03629](https://arxiv.org/pdf/2210.03629) ·
  [Reflexion arXiv 2303.11366](https://arxiv.org/abs/2303.11366) ·
  [Anthropic, Building Effective Agents](https://www.anthropic.com/engineering/building-effective-agents)
- **인터랙티브 진단 도구(디버거/ASan)가 취약점 탐색을 유의하게 돕는다.**
  [EnIGMA arXiv 2409.16165](https://arxiv.org/pdf/2409.16165)

## 3. v1→v2로 무엇을 바꿨나 (조사 → 결정)

| 조사 결과 | v2 프롬프트 반영 |
| --- | --- |
| 「미도달이 최난관」 — **문헌 근거 없음 `[미확인]`**(위 §2: Table 9에 없다) | "가장 어려운 건 크래시 로직이 아니라 취약 경로 도달" 절 신설 — ⚠ `prompts/pov/v2/prefix.md`의 그 문장과 「실패의 대부분은 …도달하지 못해서」는 **우리 가설**이지 문헌 결과가 아니다 |
| 작게 시작·매직 먼저 | 도달 절차 1·2로 명시(최소 유효 입력→헤더/매직 `literal`) |
| 관측 반영·실패 이유 한 줄 | "gen 다음 턴엔 직전 관측을 먼저 판단하고 스펙 고침" |
| 같은 액션 반복 금지 | "직전과 똑같은 spec 금지 — 가설을 바꿔라"(루프도 repeat_spec으로 끊음) |
| 한 턴 한 액션(JSON) | "최종 답은 PovAction JSON 하나" |
| 인터랙티브 진단 | inspect(asan/gdb/build/poc) 설명 — ⏵ **낡음**: `d85ea62` 기준 v2 프롬프트는 `asan`·`build`·`poc` 셋만 설명한다(`prompts/pov/v2/prefix.md:13-14`, §1의 ⏵ 노트) |

**gen-script는 안 넣었다.** 문헌이 1순위로 꼽지만(Option B) 임의 코드 실행이라는 새 신뢰면이다 —
council이 정한 승격 seam 그대로 둔다. **승격 트리거:** 체크섬/해시 게이트 포맷에서 도달률 0.
v2는 그 한계를 프롬프트 `rationale`로 드러내게만 한다("스펙으로 못 쓰는 계산값은 한계를 적어라").

## 4. 열린 실측(호스트 필요, Phase 7)

- v2 프롬프트의 실모델 성능(feyospace) — checksum·benchmark 0(미실측).
- 과제의 정답 PoC 길이 구간별 도달률 — 논문 Fig. 7은 정답 PoC가 100B 넘는 과제(65.7%)에서 ~10%다(에이전트 PoC 길이 기준이 아니다).
  우리 PovSpec이 그 구간 과제에서 도달하는지.
- gen-script 승격이 실제로 필요한지(체크섬 포맷 도달 0 여부).
