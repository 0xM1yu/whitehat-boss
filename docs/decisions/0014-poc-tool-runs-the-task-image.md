# 0014. `poc` 도구는 우리가 빌드한 바이너리가 아니라 과제의 `-vul` 이미지를 돌린다

- 상태: **제안** — 승인은 @danielcho02
- 일자: 2026-10-09
- 관련: 이슈 [#513](https://github.com/0xM1yu/whitehat-boss/issues/513) ·
  [#507](https://github.com/0xM1yu/whitehat-boss/issues/507) ·
  [#559](https://github.com/0xM1yu/whitehat-boss/issues/559) ·
  [ADR 0005](0005-tool-runner-override.md)(같은 모양의 대가) ·
  ADR 0013([PR #560](https://github.com/0xM1yu/whitehat-boss/pull/560) — 아직 머지 전이라 이 브랜치에
  파일이 없다. 채점기 값과 지정의 경계) ·
  [agent-contracts.md](../agent-contracts.md) §8단계 ·
  [build-plan.md](../build-plan.md) §「실림/읽힘」 ·
  [D99](../open-questions/D/D99.md)

## 배경

CyberGym Level 1의 PoC는 **libFuzzer 입력 바이트**다. 하네스 바이너리에 **파일 경로로 전달**한다.
그런데 컨테이너 `poc` 도구의 명령은 **실행 파일 하나**였다.

```python
# src/adapters/container_tools.py (이 ADR 전)
if tool == "poc":
    return [Command([f"{SCRATCH}/poc"], cwd=SCRATCH)]
```

그런 바이너리를 만드는 주체가 없다. 그래서 실서빙 run `run_20261007_0001`(과제 `arvo:1065` ·
완주 8시간 05분)에서 매 홉 같은 값이 났다.

```
exec: "/work/poc": stat /work/poc: no such file or directory   (exit=127)
```

`state.db`의 `poc_run` Evidence **7건 전부 `ok=0`**이고 `build` Evidence 2건도 `ok=0`이다.

**PoC 바이트를 도구 요청에 싣는 쪽은 이미 섰다** — PR [#520](https://github.com/0xM1yu/whitehat-boss/pull/520)
(`c2d8b1a` · `ToolArgs.input_bytes` + 자리표 `{harness_input}`)이 바이트를 그 도구의 scratch에 쓴다.
**읽는 쪽이 없는 것**이 남은 문제이고, 이 구분은 [build-plan.md](../build-plan.md)가 2026-10-06에 세웠다
(*"「실림」은 됐고 「읽힘」은 아니다"*).

### 무엇이 걸려 있나 — 그리고 걸려 있지 않은 것

🔴 **이것은 점수를 막는 문이 아니다.** 그 run의 점수 0의 직접 원인은 모델이 `submit`을 안 내
`oracle.final = none`이 된 것(#549)이고, 그 앞은 `origin/main`이 아직 pov `v3`를 서빙하는 것이다.
그 수정은 PR [#561](https://github.com/0xM1yu/whitehat-boss/pull/561)이다.

**PoC에 의존하는 신호도 이미 하나 산다** — `runner._submit_vul_to_oracle`이 중간 `gen`마다
`oracle_vul=<원시 종료 코드>`를 다음 홉에 싣고, 그 run에서 `exit 0` 6건 · `77` 1건을 돌려줬다.

그래서 걸려 있는 것은 셋이다.

1. **홉마다 정보량 0인 관측을 읽힌다.** `thresholds.pov_tool_hops`가 16이고 홉마다 모델 호출이
   하나다. [budgets.yaml](../../config/budgets.yaml)의 그 자리 주석이 스스로 적는다 —
   *"도구가 PoC를 읽지 않는 동안 … 홉이 늘면 무신호 되먹임을 더 읽는다."*
2. **채점기 예산이 다음 벽이다.** 루프는 `gen` 홉마다 `/submit-vul`을 부르고 서버 한도는
   **20회/60초 슬라이딩 창**이며 **캐시 적중도 예산을 쓰고 재시도가 없다**
   (`adapters/cybergym_oracle.py`의 측정). 홉 16 × Finding 5 ⇒ 최대 ~75회. 그 run이 7회였던 것은
   루프가 일찍 죽어서다(`repeat_spec` 3 · `parse_failed` 2). **#561이 루프를 돌리면 거기서 박는다** —
   그리고 `rate_limited`는 이미 지정을 죽이는 사유다(`orchestrator/oracle_log.py`).
3. **컨테이너 경로의 E4가 영구히 안 선다.** `POC_RUN` Evidence가 그 조건이고 그것을 만드는
   유일한 도구가 `poc`다.

⚠ **그 run의 `repeat_spec` 3건을 이 결함의 결과로 적지 않는다.** 모델은 도구 관측과 `oracle_vul`을
**둘 다** 봤고 어느 쪽이 재발행을 유발했는지 가른 측정은 **0회**다. 이 ADR은 그 유추에 기대지 않는다.

## 결정

`poc` 도구는 **과제 자신의 `-vul` 이미지**를 돌린다. 모델의 PoC를 `/tmp/poc`로 바인드하고,
컨테이너 안 명령은 **계열이 정하는 것**을 쓴다.

```
docker run --rm --network none --pull never \
  -v <PoC 호스트 경로>:/tmp/poc:ro  n132/arvo:<과제id>-vul  /bin/arvo
```

**우리는 대상을 빌드하지 않는다.** 그것이 이 결정의 핵심이다.

딸린 불변식 여섯. ⚠ **「전부 테스트가 문다」고 처음 적었는데 그것이 거짓이었다** — 적대적 리뷰
2026-10-09이 cli·factory 배선을 **지우고 2,028건을 통과**시켰고, 입력 없는 `poc` 호출을 무는
테스트가 0건이었다. 그 둘은 이 PR에서 세웠다(`test_the_cli_turns_the_oracles_task_id_into_the_tool_image` ·
`test_the_factory_hands_the_task_image_to_every_container_runtime` ·
`test_a_poc_call_without_input_is_failed_evidence`). 6번은 위 노트대로 **여전히 기록에 안 드러난다.**

1. **종료 코드 `124`·`125`·`126`·`127`과 음수는 타깃의 판정이 아니다.** 「관측 실패」로 낸다.
   근거는 `tools/oracle_canary.py`가 이미 적는다 — *"이 코드로 `-vul` 다리가 채워지면 망가진 docker
   설정이 「정답 PoC가 타깃을 터뜨렸다」로 읽힌다."* **`1`은 가를 수 없다**(ASan 기본 종료 코드일 수
   있다) — 가를 수 없다는 **그 사실을 기록한다**. ⏵ 2026-10-09 — **예외 하나**: Windows Docker
   Desktop은 데몬이 꺼지면 `125`가 아니라 `1`을 낸다(실측 — stderr *"failed to connect to the docker
   API …"*). 그 `1`은 stderr의 클라이언트 연결 실패 문구로 가려 「관측 실패」로 낸다
   (`container_tools._POC_DAEMON_UNREACHABLE` · #567 리뷰).
   ⏵ 2026-10-09 — **위 코드들이 전부가 아니다**(적대적 리뷰): `137`(docker SIGKILL — **우리가 `--memory`를
   건다**) · `255`(singularity FATAL) · `>255`(Windows NTSTATUS)도 「관측 실패」로 낸다
   (`_POC_ENV_FATAL` · `_poc_is_harness_failure`). ⚠ **`137`은 대상 자신의 OOM과 갈리지 않는다** —
   `src/adapters/container_tools.py`의 `_POC_ENV_FATAL` 주석이 그것을 적는다. 그래서 사유 문구는
   「띄우지 못했다」로 단정하지 않고 「대상의 판정으로 읽을 수 없다」까지만 말한다
   (`POC_UNREADABLE_EXIT_REASON`).
2. **채점은 오라클만 한다. 이 도구는 관측만 낸다.** 둘이 갈리면 오라클이 이긴다.
    **이 ADR은 「지정은 채점기 값을 읽지 않는다」를 건드리지 않는다** —
   `test_designation_does_not_read_oracle_fields`가 그대로 선다. ⏵ 2026-10-14 — 그 결정은
   ADR 0013 결정 2 개정(#637)으로 **후보 자기 `vul` 응답 코드 하나만큼** 열렸다(이 ADR이 연 것이 아니다).
3. **`-fix` 이미지는 도구에 노출하지 않는다.** 모드를 받을 수 있는 인자를 **노출하지 않는 것**으로
   막는다(주석이 아니라 타입).
4. **`vm.mmap_rnd_bits`가 28보다 크면 실행을 거부한다.** ⚠ 술어는 `> 28`이다 — 처음 이 줄을
   「≠ 28」로 적었는데 `canary_report.canary_refusal`의 실제 비교는 상한이다(적대적 리뷰
   2026-10-09이 잡았다. 28보다 **작은** 값은 거부하지 않는다). 32면 무해 입력도 ≈0.28로 성공해 판정이 잡음이다.
   🔴 **새 게이트를 만들지 않는다 — 이미 있는 자리에 묶는다.** `cli._canary_gate` →
   `adapters/canary_report.canary_refusal`이 `rnd_bits_above_28`로 이미 거부하고, 미측정
   (`rnd_bits_unmeasured`)도 거부한다. 그래서 **`poc_target`은 오라클이 배선되는 그 자리에서만
   배선된다** — 카나리 선행을 통과하지 않은 run에서는 `poc` 도구가 과제 이미지를 못 받고
   `Evidence(ok=False)`가 된다. 게이트를 두 벌 두면 두 벌이 갈린다.
5. **모르는 과제 계열은 거부한다 — 이름도 명령도 지어내지 않는다.** 계열은 `partition(":")`
   **정확 일치**로 가른다. `startswith("oss-fuzz")`면 `oss-fuzz-latest`가 `oss-fuzz`로 떨어져
   **없는 이미지 이름을 지어낸다**(저자 서버도 콜론까지 넣어 가른다).
6. **별도 스위치를 만들지 않는다.** ⚠ 처음 이 줄을 「기본 켜짐」이라 적었는데 **그 말은
   정확하지 않다**(적대적 리뷰 2026-10-09). 새 손잡이는 없지만 `poc_target`은 **채점기가 서는
   run에서만** 온다 — `--oracle-submit`이 없거나 채점기가 닿지 않거나 모르는 계열이면 `None`이고
   그 run의 `poc`는 전건 실패 Evidence다. 즉 **조건부로 켜진다.** 기본 꺼진 **스위치**를 두지
   않는다는 뜻만 참이다(그런 스위치가 점수 사슬을 막은 전례가 있다 — 「켤 때의 값은 미실측」 순환).
   🔴 **그 run에서 `poc`가 안 섰다는 사실이 기록에 직접 드러나지 않는다** — `RunRecord`에 그
   필드가 없고 경고도 없다. 간접 신호는 `tool_images`의 항목 수뿐이다. **미해결로 남긴다.**

### 🔴 그 대가로 `RunRecord.tool_images`를 넓힌다 — ADR 0005와 같은 자리

`ContainerToolRunner.images()`는 `{self.image: digest}` **하나**를 냈다. `poc`가 과제 이미지로
나가면 **그 run의 재현성 기록이 거짓이 된다** — 실제로 돈 이미지 둘 중 하나만 적힌다.

> **`RunRecord.tool_images`는 이 run에 배선된(돌 수 있는) 이미지 전부를 digest와 함께 든다** —
> `poc`가 한 번도 안 불린 run에도 과제 이미지가 실린다(채점기가 서면 늘 배선된다). 과제 이미지의 digest를
> 못 구하면 그 run은 비교표 밖이다.**

[ADR 0005](0005-tool-runner-override.md)가 `--tool-runner` 오버라이드를 열며
[evaluation.md](../evaluation.md) §2.3 규칙 2에 예외를 하나 열고 *"예외를 열되 사후에 구분
가능하게"* `RunRecord.tool_runner`를 필수 필드로 추가한 것과 같은 거래다. 여기서는 **샌드박스
이미지가 도구마다 갈리는 예외**를 열고, 기록 필드로 값을 치른다.

## 고르지 않은 것 — 근거째로 남긴다

다시 꺼내려면 이 근거들을 반증해야 한다.

| 안 | 모양 | 기각 근거 |
| --- | --- | --- |
| **A** | `[<우리가 빌드한 대상>, "/work/poc.bin"]` | **우리 clang이 이 번들을 컴파일하지 못한다**(#507 — `unknown type name 'uint8_t'` 등 19건, 번들에 `config.h`가 없다). 저자 과제는 1,507건이고 빌드 규칙이 과제마다 다르다. 우리가 빌드 규칙을 **지어내는** 것은 §0과 닿는다 |
| **C** | 입력을 받아 대상을 부르는 래퍼를 scratch에 쓴다 | 래퍼가 부를 **대상 바이너리가 여전히 필요**하므로 A로 환원된다. 그리고 래퍼는 또 하나의 「우리가 지은 재현 경로」다 |
| **E** | `poc`를 PoV 도구 목록에서 뺀다 | 「0줄」이 **거짓이었다**: 서빙 중 `prompts/pov/v3/prefix.md`가 `poc`를 이름으로 광고하고(`- \`poc\` — 재현 실행 도구.`), `src/schemas/pov.py`의 `PovAction.tool`이 `Literal["asan","build","gdb","poc"]`라 **스키마 차원이라 v2에도 든다**. 그리고 **지정이 내부 등급에 의존한다**(`_designate_final`의 사슬 첫 키) |
| **F** | `oracle_vul`을 Evidence로 승격한다 | ⏵ 2026-10-14 — 새 사유: 채점기 값을 **하네스 내부 등급에 섞는다.** 내부 등급은 게이트·Critic 풀·리포트·지정이 다 읽는 값이라 영향이 한 자리에 안 머물고, 「채점 성공」이 내부 등급을 올리거나 그 반대가 된다(`src/interfaces/oracle.py`의 ⚠ · #232). ⏵ 2026-10-14 — **이 행의 옛 사유는 물러났다.** 옛 사유는 *"`_designate_final`이 채점기 값을 읽지 않는 것이 이 저장소의 결정이고, 그 값으로 후보를 가르면 후보를 채점기에 물어 고르는 best-of가 된다"*(ADR 0013의 #549 ⓒ 기각과 같은 축 — [PR #560](https://github.com/0xM1yu/whitehat-boss/pull/560))였는데, ADR 0013 결정 2 개정(#637)으로 지정이 후보 자기 `vul` 응답 코드 하나를 읽게 됐다. 그래서 F는 「지정이 채점기 값을 읽는다」가 아니라 **그 값을 등급 전체에 묶는다**는 점에서만 기각이다. `test_designation_does_not_read_oracle_fields`는 지금 그 한 필드 말고는 안 읽는 것을 문다 |
| **D** | 안 고치고 한계로 적는다 | 아래 「철회 트리거」로 남긴다 |

### 철회 트리거 — D로 돌아가는 조건

**D가 방치가 아니라 결정이 되려면 조건이 필요하다.** 아래 둘 중 하나가 성립하면 이 결정을 되돌린다.

- 과제 이미지 실행이 **홉당 시간을 유의하게 늘려** 같은 예산에서 홉 수가 줄어드는 것이 측정된다.
- 로컬 판정과 오라클 판정이 **같은 바이트에서 갈리는 비율**이 측정되고, 그 갈림이 모델을 더 나쁜
  방향으로 움직이는 것이 관측된다.

둘 다 **지금 미측정**이다. 되돌릴 때는 `poc`를 도구 목록에서 빼는 쪽(E)이 아니라 이 결정 전으로
돌리는 쪽이고, 그때도 프롬프트는 **새 판**이 필요하다.

## 결과

**따라오는 것.**

- `poc` 명령이 입력을 받으므로 서빙 프롬프트의 「지금 하네스의 한계」 문단이 거짓이 된다.
  프롬프트 판은 불변 기록이라 **pov `v5`**가 따라온다(v4 prefix가 스스로 *"도구의 한계가 풀리면 이
  문단이 바뀐다"*고 적어 뒀다). `nodes/pov._WIDE_INPUT_VERSIONS`에 그 판을 넣지 않으면 되먹임
  (`oracle_vul` 줄 · 출력 꼬리 · `last_gen_*`)이 **조용히 사라진다**.
- 핀 둘이 뒤집힌다 — `test_the_poc_command_takes_no_argument_today`와
  `test_no_executed_container_command_receives_the_input_today`. 후자는 `poc`만 빠지고
  **`asan`·`gdb`·`build` 셋은 그대로 선다**(한 도구만 바꾼다).
- 샌드박스 이미지가 도구마다 갈린다 — 저장소에 선례가 **0건**이던 모양이다.
- `#507`은 **안 닫힌다.** 그쪽은 `clang-tidy`·`build`(정적·컴파일 근거)이고 이 결정은 실행 근거만
  건드린다. 두 도구는 여전히 이 번들에서 `ok=0`이다.

**안 바뀌는 것.**

- 채점 경로(`oracle.final` 지정) · any-of 금지 · 「끝에 정확히 한 장」.
- 다른 세 도구(`asan`·`gdb`·`build`)의 argv — **바이트 동등**이어야 한다.
- `D99`의 상태. 선행 하나(「도구가 PoC 바이트를 읽는 것」)가 서지만 나머지가 남는다.

## 미결 · 미측정

§0대로 **0으로 두고 사유를 적는다.**

- **서빙 장비에서 과제 이미지가 도는지 0회.** `tools/oracle_canary.py`의 「실 Docker 2건 통과」는
  그 도구의 기록이고 **같은 장비·같은 시점이라는 근거가 없다**. `--pull never`라 이미지 사전 당김은
  운영자 몫이고, 없으면 전건이 실패 관측이다.
- **홉당 로컬 실행 시간.** `arvo:1065`에서 컨테이너 1회 0.4~0.7초는 **표본 1**이다. 홉 16 ×
  Finding 5의 총합은 **안 쟀다**.
- **다른 과제 계열·다른 과제에서의 종료 코드 분포.** 재 본 것은 둘뿐이다 — `arvo:1065`(MSan) **77** ·
  `oss-fuzz:368076871`(ASan) **1**. 1,507건 중 2건이라 모비율이 아니다.
- **이 변경이 모델의 `submit` 시점을 바꾸는지.** 전후 대조 0회.
  🔴 **그리고 이 PR은 그 변수를 가르지 못한다 — 적대적 리뷰 2026-10-09이 잡았다.** 서빙 판이
  v4에서 **v5**로 올라가고 v5 = v4 + 이 변경이므로, 다음 run은 #561의 판독 규칙과 이 변경을
  **항상 같이** 진다. 가르려면 **v4로 한 run을 먼저** 돌려야 하는데 v4로 돈 run은 0건이다
  (`config/models.yaml`의 그 좌석 주석이 적는다). 즉 비교 기준선은 v3이고 v5-대-v3은 두 변수를
  같이 움직인다. **어느 run이 무엇을 지는지는 PM이 정한다** — 이 ADR은 그 사실을 적을 뿐이다.
- **도구 환경이 채점기와 다르다.** 채점기는 `bash -c "timeout -s SIGKILL 10 …"`로 돌리고 이 도구는
  명령을 직접 실행하며 `--read-only`·`--cpus`·`--memory`·`-v <대상>:/src:ro`(이미지 자신의 `/src`를
  덮는다)·`-w /work`가 붙는다. PoC가 대상을 멈추면 채점기는 10초에 끊는데 이 도구는
  `sandbox.timeout_s`(지금 300)까지 간다 — **홉당 최대 300초**다. 그 차이가 판정을 가르는지는
  **안 쟀다**(적대적 리뷰 2026-10-09이 지적했다).
- **복제한 상수의 대조는 양쪽이 같이 틀리면 못 잡는다.** `_POC_FAMILY`·`POC_IN_IMAGE`·환경 코드
  범위를 `tools/oracle_canary.py`에서 베꼈고, 둘이 같은지는
  `tests/test_poc_tool_feeds_the_input.py::test_the_copied_constants_match_the_tool_they_were_copied_from`이
  센다. 둘 다 저자 소스와 어긋나는 경우는 저자 소스 태그를 다시 봐야 한다(그 테스트 docstring).
- **런타임 실패가 run 상태에 드러나는지**(#559). 이 결정은 그 자리를 **새로 하나 더 만든다** —
  과제 이미지의 `exit 125`가 도구 결과로 적히면 같은 사고다. 불변식 1이 그것을 막지만 ~~#559 자체는
  이 ADR이 닫지 않는다.~~ ⏵ 2026-10-12 — #559는 닫았다(서빙 trace 124~127 0건 · 시작 시점은 `preflight`(#534)가 `docker run`으로 본다 · run 도중 데몬 실패의 run 경고는 안 만들었다).
  ⏵ 2026-10-13 — 정정(#634 사후 리뷰). ① 위 취소선 문장은 PR #634가 ⏵ 없이 지웠던 것을 되살린 꼴이다. ② 「서빙 trace 124~127 0건」은 모집단을 밝혀야 읽힌다: 센 것은 trace 여섯 — 2026-10-10·11 run 다섯(`C:\w\s14-results\run_20261010_0001`~`0004`·`run_20261011_0001`)과 2026-10-12 run(`C:\w\s17-artifacts\run_20261011_0001`) — 이고, 본 종료 코드는 0과 1뿐이다(2026-10-13 재계수). 그중 **PoV 도구(`poc`·`gdb`)가 돈 trace는 2026-10-12 run 하나**다 — `poc` 6회 exit 0 · `gdb` 1회 exit 1 · `build` exit 1; 나머지 다섯은 1·2단계 도구(`index`·`semgrep`·`clang-tidy`·`build`)만 있어 이 도구의 124~127을 볼 자리가 아니었다. ③ 이 모집단 밖의 2026-10-08 run은 로컬 `poc` 도구가 **매 홉 exit 127**이었다([#513](https://github.com/0xM1yu/whitehat-boss/issues/513) · [10-08 보고서](../experiments/harness/2026-10-08-first-real-serving-run-arvo1065-vastai-remote.md) §1) — 이 결정이 고친 바로 그 사고다. 그러므로 「0건」은 **이 결정 뒤 PoV 도구가 한 번 돈 run 하나에서의 0건**이다.
