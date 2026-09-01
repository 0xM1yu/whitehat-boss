셋 다 확인했습니다. 2번은 지적이 맞아 테스트를 넣었습니다(03e451c). 1번은 전수로 봤고 영향받는 소비자가 없습니다.

## 1. 컨슈머 전수 확인 — 빈 객체로 "진행"하는 호출자가 없습니다

`read_declared_sampling`을 부르는 곳은 `src/adapters/generation_config.py:105` 하나입니다. 그리고 말씀하신 `if result is None: skip`이 바로 그 다음 줄 106의 `if sampling is not None:`인데, **이 PR이 고치는 함수 안**에 있습니다. 그 분기의 동작이 바뀌는 것이 이 PR의 요점입니다 — 읽었으나 선언이 0개인 역할이 이제 맵에 남습니다.

`declared_sampling_by_role`을 부르는 곳은 `src/adapters/factory.py:362` 하나입니다. 거기서 흐름이 이렇게 끝납니다:

```
factory.py:362 → deps.model_sampling → runner.py:507 → _new_record(1062·1097) → RunRecord.model_sampling
```

그리고 `record.model_sampling`을 **읽는** 코드는 `src/` 어디에도 없습니다. `grep -rn "model_sampling" src/`의 결과가 전부 위 경로의 쓰기와 필드 선언·주석입니다. 보고서 생성도 비교 코드도 이 필드를 아직 안 읽습니다.

그래서 계약 전환의 파급은 "기록에 무엇이 실리는가"에서 멈추고 빈 객체를 받아 잘못 진행하는 하류가 없습니다. 파일 2개만 건드린 것이 좁아서가 아니라 그게 전부여서입니다.

## 2. 왕복 테스트 — 넣었습니다

좋은 지적입니다. 리더에서 갈라도 직렬화에서 뭉개지면 이 PR의 값이 저장 경계에서 사라진다는 게 맞습니다. 확인해 보니 두 축이 서로 다른 자리에 실립니다:

- 읽었으나 선언 0개인 역할 → **키가 있고 값이 전부 `null`인 객체**
- 못 읽은 역할 → **키 자체가 없음**

```json
"model_sampling": {"scout": {"temperature": null, "top_p": null, "top_k": null, "repetition_penalty": null}}
```

run 수준의 `None`(옛 manifest)과 `{}`(읽을 것이 없었음)도 각각 유지됩니다. `test_the_read_but_empty_role_survives_the_manifest_round_trip`이 재검증까지 통과한 뒤에도 둘이 갈리는 것을 못 박습니다.

헛도는 단언이 아닌 것도 확인했습니다 — `return ModelSampling()` 두 곳을 `return None`으로 되돌리면 이 테스트가 `KeyError: 'scout'`으로 실패합니다.

## 3. ①과의 순서 — 맞습니다

③이 주는 것은 "선언을 정직하게 기록한다"까지입니다. 그 선언이 모델이 실제로 쓴 값이라는 보장은 ①이 주는 것이고, ①이 남아 있는 한 이 PR만으로 비교가 신뢰되지 않는다는 말씀이 맞습니다.

다만 순서는 ③이 먼저인 편이 낫다고 봅니다. ①은 나중에 고쳐도 그때부터 맞는 값이 들어오지만 ③은 **지금 도는 run의 기록이 사후에 복원 불가**라 미루면 그 run들의 샘플링 축이 영구히 검증 불가로 남습니다. HF 캐시가 저장소 밖이라 "그때 그 상태였는지"를 나중에 알 방법이 없습니다.

순서와 의존은 이슈 #99에 적어 두겠습니다.

<!-- HUMANIZE-SUMMARY v1.6.1
run_id: 2026-09-01-002
mode: monolith / light / 강도=보수
metrics:
  char_in: 1721        # shim의 char_count 값
  char_out: 1719       # 쉼표 2자 삭제
  change_rate: 0.1%    # 2/1721 = 0.116%, 자가 산출값(확정은 verify_change_rate.py)
  self_check: 5/6
  grade: B
categories:  # before → after
  C-11 연결어미 뒤 쉼표: 4 → 2   # -고 2, -지만 1, -ㄴ데 1 → -고 1, -ㄴ데 1
  A-18 관형절 3중 중첩: 1 → 1    # 미적용(분리 시 의미 드리프트 위험, 보수 모드)
  J-2 따옴표 강조: 4 → 4         # 임계 5회 미만, 전부 절-명사화로 기능함
  J-1 볼드 강조: 5 → 5           # GitHub 마크다운 강조로 기능함, 목록 대조축이 걸림
  D-1~D-4 · A-7 · A-8 · A-9 · A-16 · H-1 · H-3 · I-1 · C-5: 0 → 0
self_check:
  - 고유명사·수치·인용·내용 앵커 100% 보존: OK (단어 삭제·치환 0건, 코드/경로/SHA/식별자 무손상)
  - 변경률 30% 이하: OK (0.1%)
  - 장르 이탈 없음: OK (PR 리뷰 답글 유지)
  - register 보존: OK (합쇼체 유지, '-했-'→'-하였-' 전환 없음, '봤고·안 읽습니다·그게' 구어 보존)
  - S1 잔존 0건: NG — C-11 1건 의도적 보존 (아래 residual_findings 참조)
  - 인공 표현 추가 없음: OK (신규 어휘 삽입 0건, 원문 대시 3개 전부 보존)
highlights:
  - id: C-11
    before: "…에서 멈추고, 빈 객체를 받아 잘못 진행하는 하류가 없습니다."
    after: "…에서 멈추고 빈 객체를 받아 잘못 진행하는 하류가 없습니다."
  - id: C-11
    before: "…맞는 값이 들어오지만, ③은 지금 도는 run의 기록이…"
    after: "…맞는 값이 들어오지만 ③은 지금 도는 run의 기록이…"
residual_findings:
  - id: C-11
    severity: S1
    span: "…①이 주는 것이고, ①이 남아 있는 한…"
    reason: "쉼표 제거 시 '것이고 ①이'가 붙어 절 경계가 무너진다. 제거가 개선이라는 확신이 없어 보수 모드 지침(확신 없는 구간 보존)에 따라 유지."
  - id: C-11
    severity: S2
    span: "…`if sampling is not None:`인데, **이 PR이 고치는 함수 안**에…"
    reason: "'-ㄴ데'는 C-11이 열거한 연결어미(-고/-며/-지만/-면서/-아서/-어서) 집합 밖이고, 긴 코드 인용 뒤 절 경계를 표시하는 쉼표다."
  - id: A-18
    severity: S2
    span: "그 선언이 모델이 실제로 쓴 값이라는 보장은 ①이 주는 것이고, ①이 남아 있는 한…"
    reason: "관형절 중첩 1건(shim relative_clause_nesting=1). 문장 분리는 ①/③의 논리 관계를 재배열해야 해서 의미 드리프트 위험. 보수 모드에서 미적용."
grade_reason: "B — 입력이 이미 사람이 쓴 글이라 근거 있는 edit이 C-11 2건뿐이었다. S1 잔존 1건(C-11)은 결함이 아니라 가독성 판단에 따른 의도적 보존이다. 변경률 0.1%는 등급 A 밴드(10~25%)보다 낮은데, 이는 윤문이 부족해서가 아니라 A 밴드가 전제하는 'AI 티가 있는 입력'이 아니었기 때문이다 — shim도 route_hint: light / '이미 잘 쓴 글'로 판정했다."
note: "강도=보수 · Do-NOT list(코드 블록·파일 경로·줄번호·수치·SHA·식별자·테스트명·헤딩/목록 구조) 전 구간 무수정 확인."
-->
