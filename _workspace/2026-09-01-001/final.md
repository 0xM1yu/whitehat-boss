셋 다 확인했습니다. 결론부터: 1번은 이미 그 형태로 서 있고 위치가 스키마가 아니라 config 경계입니다. 2·3번은 지적이 맞고, 다만 제안하신 assert는 현재 경로에서 죽은 코드가 됩니다.

## 1. 검증 — "새 쓰기만 검증하고 옛 읽기는 허용"이 이미 그 구조입니다

`RunRecord.profile`에 들어가는 값은 `config.profile_name` 하나뿐입니다. 그리고 `HarnessConfig`의 생성 지점은 저장소 전체에서 `src/config.py:417` 한 곳이고 그 자리가 `load_config` 안입니다.

그 함수가 `src/config.py:401-402`에서 이렇게 거릅니다:

```python
if profile not in profiles:
    raise ConfigError(f"프로필 {profile!r}이 없다. 있는 것: {sorted(profiles)}")
```

그래서 `config.profile_name`은 **`config/profiles.yaml`의 키였음이 증명된 문자열**입니다. 오타나 개명된 이름은 run이 시작되기 전에 죽고 `RunRecord`까지 도달할 경로가 없습니다. 이미 `tests/test_config.py:188`의 `test_unknown_profile_fails_loudly`가 그걸 못 박고 있습니다.

옛 읽기가 허용되는 것도 자동으로 따라옵니다 — 검증이 로드 경계에 있으니 이미 쓰인 manifest를 파싱할 때는 걸리지 않습니다.

스키마에 `Literal`이나 enum을 두지 않은 이유는 따로 있습니다. `config/profiles.yaml`이 데이터라서 프로필을 하나 추가할 때마다 스키마 코드를 같이 고쳐야 하고 그 둘은 어긋날 수 있습니다. 지금은 YAML이 단일 원천입니다.

## 2. 인정합니다 — 다만 러너 경로는 타입으로 막힙니다

"현재 코드 흐름의 보장이지 타입 보장이 아니다"는 맞습니다. 정확히 어디까지가 타입인지만 적어 두겠습니다.

`HarnessConfig.profile_name`은 `src/config.py:263`에서 `NonEmptyStr`이고 기본값이 없는 필수 필드입니다. 그래서 `profile=config.profile_name`이 `None`을 낼 수 없는 것은 관례가 아니라 타입입니다. 남는 것은 지적하신 그대로 **`_new_record`를 지나지 않는 새 `RunRecord` 생성 경로**가 생기는 경우고 그건 타입으로 막히지 않습니다.

## 3. assert는 지금 자리에서는 뜨지 않습니다

2번의 이유로 러너 경로에서는 `profile`이 `None`이 될 수 없어서 `_new_record`에 넣는 assert는 실행되자마자 항상 참인 죽은 단언이 됩니다.

`incomplete_fields()`도 같습니다. 호출 지점이 `src/orchestrator/runner.py:1103` 하나뿐이고 거기서 보는 것은 **러너가 방금 만든 record**입니다. 옛 manifest를 읽어 판정하는 경로는 지금 없습니다. 그래서 `profile is None`을 넣으면 영영 안 뜨는 줄이 하나 늘어납니다 — `tool_runner`를 뺀 것과 같은 자리입니다.

말씀하신 사각지대가 실제로 신호를 낼 수 있는 자리는 **옛 manifest를 로드해 판정하는 코드가 생기는 시점**입니다. 그때는 `profile: None`이 "이 run은 비교의 기준축을 안 남겼다"라는 뜻 있는 경고가 됩니다. 그 경로가 생길 때 같이 넣는 게 맞다고 봅니다. 지금 넣자고 하시면 넣겠습니다만, 그러면 뜨지 않는 경고를 하나 만드는 것이라 판단을 남겨 둡니다.

<!-- HUMANIZE-SUMMARY v1.6.1
run_id: 2026-09-01-001
route: light / 강도: 보수 / genre: report (GitHub PR 리뷰 답글)
metrics:
  char_in: 1810            # 01_input_with_metrics.txt 의 [원문 시작]~[원문 끝] 구간 실측
  char_out: 1804            # final.md 본문 실측
  change_rate: 0.33%      # (char_in - char_out) / char_in — 계산값
  edits: 6 (전부 쉼표 1자 삭제 — 어휘·어순·문장 구조 변경 0건)
  self_check: 6/6
  grade: A
categories:  # before -> after
  C-11 연결어미 뒤 쉼표: 7 -> 1
  A-18 관형절 3중+ 중첩: 2 -> 2 (미조치 — residual_findings 참조)
  H-1 문두 접속사: 4 -> 4 (임계 5회 미만이라 비대상)
  D-1·D-2·D-4·A-7·A-8·A-9·A-16·C-5·J-2: 0 -> 0 (탐지 없음)
self_check:
  - 고유명사·수치·인용·내용 앵커 100% 보존: OK — 코드 블록·인라인 코드·파일 경로·줄번호·식별자 전부 바이트 동일
  - 변경률 30% 이하: OK
  - 장르 이탈 없음: OK — PR 리뷰 답글 그대로, 헤딩·목록 구조 무변경
  - register 보존: OK — 합쇼체 유지, '-했-' -> '-하였-' 전환 0건
  - S1 잔존 0건: OK — C-11 강한 신호(6회+ 임계)가 1건으로 해소. 의도적 보존 1건은 residual에 명시
  - 인공 표현 추가 없음: OK — 삽입 0건. 부가설명 대시(—)·'넣겠습니다만' 구어 종결 전부 보존
highlights:
  - id: C-11
    before: "`src/config.py:417` 한 곳이고, 그 자리가 `load_config` 안입니다."
    after: "`src/config.py:417` 한 곳이고 그 자리가 `load_config` 안입니다."
  - id: C-11
    before: "run이 시작되기 전에 죽고, `RunRecord`까지 도달할 경로가 없습니다."
    after: "run이 시작되기 전에 죽고 `RunRecord`까지 도달할 경로가 없습니다."
  - id: C-11
    before: "`config/profiles.yaml`이 데이터라서, 프로필을 하나 추가할 때마다"
    after: "`config/profiles.yaml`이 데이터라서 프로필을 하나 추가할 때마다"
  - id: C-11
    before: "`None`이 될 수 없어서, `_new_record`에 넣는 assert는"
    after: "`None`이 될 수 없어서 `_new_record`에 넣는 assert는"
residual_findings:
  - id: C-11
    severity: S1
    count: 1
    reason: "'2·3번은 지적이 맞고, 다만 …' — 이 쉼표는 대조 전환(다만)을 표시해 가독성에 기여한다. 보수 강도에서 의도적 보존. 패턴 임계(6회+)는 이미 밑돈다."
  - id: A-18
    severity: S2
    count: 2
    reason: "'말씀하신 사각지대가 실제로 신호를 낼 수 있는 자리는 …' 과 '남는 것은 지적하신 그대로 …' 두 문장의 관형절 중첩. 문장을 쪼개려면 볼드로 묶인 내용 앵커(`_new_record` 우회 생성 경로 / 옛 manifest 로드 판정 시점)를 재배치해야 해 보류."
grade_reason: "A — S1 패턴 신호 해소(C-11 7 -> 1, 임계 미만), S2 잔존 2, 자체검증 6/6. 다만 변경률은 rubric의 A 대역(10~25%) 하한을 밑돈다. shim이 route_hint=light · route_reason='이미 잘 쓴 글'을 냈고 사용자 지시가 보수 강도였으며, 실제로 quick-rules 처방이 붙는 패턴이 C-11 하나뿐이었던 결과다."
notes:
  - "사용자 Do-NOT list(코드 블록·인라인 백틱 코드·파일 경로·줄번호·수치·커밋 SHA·식별자·헤딩·목록 구조) 전부 무변경."
  - "lexical_diversity z=+2.99는 어휘 다양성이 높다는 뜻이라 인간 방향 신호다. quick-rules에 대응 처방이 없어 조치하지 않았다."
  - "relative_clause_nesting=2 는 A-18과 교차 확인했고, 앵커 보호를 이유로 미조치 처리했다(residual 기재)."
-->
