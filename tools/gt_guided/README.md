# gt_guided — GT 기반 슬라이스로 Auditor FT 입력 만들기 (긴급용)

> 🔴 **하네스 슬라이싱 설계가 아니다.** 정답(GT)을 보고 후보를 고르므로 실제 run의 입력 분포와 다르다.
> FT 데이터를 급히 만들 때만 쓴다. 산출 행은 `gt_guided: true`로 표시된다 -> 설계대로 자른 데이터와 섞지 않는다.

## 무엇을 하나

- 슬라이서(`tools/slicer/`)를 그대로 돌리되, 후보 발견 직후 GT 근처 후보만 남긴다(`gt_slice.py`)
  - 남기는 후보 -> entry나 앵커가 GT 함수(인덱스의 그 줄을 품는 함수) 앞뒤 5줄 안
  - 하나도 없으면 -> GT 파일에서 GT 줄에 가장 가까운 후보 8개
  - 결과 `gt_filter` -> 방식 · 발견 후보 수 · 남긴 수 · GT 범위
- 슬라이스 자체는 같은 Joern 질의, 같은 `slices.json` 모양
- Auditor 입력 -> `build_auditor.py`가 하네스의 `runner._auditor_target`·`_auditor_source`를 `config/budgets.yaml` 값으로 부른다
  - `AUDITOR_DEFINITIONS=1`이면 `auditor_definitions`만 켬(매크로·타입·파일 변수 정의)
  - GT는 `source` 조립에 안 쓰고 라벨(`gt_relevant` · `gt_in_source`)에만 쓴다

## 파일

| 파일 | 어디서 | 하는 일 |
| --- | --- | --- |
| `gt_slice.py` | 장비 | 슬라이서 + GT 후보 필터 |
| `build_auditor.py` | 장비 | 표본 하나의 슬라이스 -> Auditor 입력 JSONL |
| `gt_worker.py` | 장비 | 작업자 W개, 각자 Joern 서버 하나(상자당 하나만 뜸 -- `flock`). `inbox/`에서 작업을 하나씩 가져가(하드링크 -- 이미 도는 표본은 못 가져감) 받기 -> 인덱스 -> `gt_slice` -> `build_auditor` -> `out/<id>/`. 끝에 표지 하나: `DONE`(성공) · `FAILED`(표본 자체의 실패) · `RETRY`(일시 실패 -- clone · Joern 기동 · 예상 밖 예외) |
| `coord.py` | 사용자 PC | 장비마다 `inbox/`를 작업자 수 x 2 이상 유지(큰 저장소 먼저) · 끝난 표본을 받아(sha256 확인) 표본 단위로 19MB 이하가 되게 묶어 `Downloads/ft_gt_auditor_batchNNN.tar`(있는 파일은 덮지 않고 다음 번호) + 디스코드 웹훅 첨부(넘치는 표본은 다음 묶음, 혼자 19MB 넘는 표본은 조각내 전송) · 멈춘 작업 재배정 · `RETRY`는 한 번 더(총 2회), `FAILED`와 소진된 `RETRY`는 실패로 세고 `build.json`은 받는다 · 상자 사본은 받은 기록(`state.json`)을 저장한 **뒤에** 지운다 · 다 받으면(jobs.json의 표본 **집합**이 전부 pulled) 상자에 안 받은 것이 없는지 보고 vast 인스턴스를 삭제(`vastai`가 실패하면 3회 뒤 손으로 지우라고 로그에 남기고 종료 코드 1) |

## 장비 배치

```
/root/gt/harness   <- 이 저장소 체크아웃(또는 git archive)
/root/gt/joern     <- Joern 4.0.605
/root/gt/venv      <- python3.11 + pydantic·pyyaml·tokenizers·tree-sitter(-c·-cpp) (tools/harness-tools/Dockerfile 고정 버전)
py harness/tools/gt_guided/gt_worker.py <W>
```

- 작업 JSON -> `sample_id` · `repo_url` · `commit` · `gt_points`(`role`·`file`·`line`·`line_end`) · `description` · `project`
- 슬라이서 인자 -> `--max-flows-per-candidate 10 --backward-depth 30 --backward-nodes 5000`, 질의 600초 · 후보 600초, 슬라이서 단계 상한 2400초(`HARD_SECONDS`) -- 표본 전체의 상한이 아니다(`git fetch` · 인덱서는 각 1800초, 빌드는 3600초로 따로 묶인다)
- 코디네이터 -> `py -3.11 coord.py jobs.json boxes.json` · Python 3.11.4 이상(`tarfile` 추출 필터) · `state.json`은 그 `jobs.json`에만 묶인다 -- 새 캠페인이면 `tools/gt_guided/state.json`을 치운다(다른 jobs.json이면 시작을 거부한다). `coord.py`와 `gt_worker.py`는 같은 커밋에서 배포한다(표지 `DONE`·`FAILED`·`RETRY`가 둘의 약속이다)

## 한계

- 실 Joern 종단 1회 -> 2026-10-03 vast 1대(EPYC 7K62, 작업자 4) · Joern 4.0.605 · s500 vuln 4개(json · proj.4 · readstat · ndpi)
  - 4/4 오류 없음 · 표본당 약 1분 · 후보 470~4,416 -> 1~10개 남김 · 표본 단위로는 4/4에서 Auditor 입력에 GT 줄 보임 -- FT의 단위는 행이고 행 단위로는 **10행 중 5행**(PR #416 본문 검증 표 -- readstat 3행 중 1 · proj.4 1/1 · json 1/1 · ndpi 5행 중 2)
  - 그때의 라벨 `gt_function`은 함수 범위 창과 GT 앞뒤 30줄 창을 구별하지 못했다 -- 4건이 어느 쪽이었는지는 원자료가 저장소 밖이라 알 수 없다(미실측). 지금 판은 `gt_window`로 가른다
  - `coord.py` 배정 -> 완료 감지 -> sha256 확인 내려받기 -> 인스턴스 삭제까지 확인
  - run ID 없음 · 원자료 저장소 밖
- 표본 4개뿐 -> 큰 저장소·source 라벨 있는 표본·인덱서가 함수를 못 뽑는 C++는 아직 안 돌려 봄
- source 라벨이 없는 표본은 sink·root_cause 함수 쪽 후보만 남는다
- C++ 헤더처럼 인덱서가 함수를 못 뽑으면 GT 범위가 GT 줄 앞뒤 30줄로 떨어진다
- 큰 저장소(CPG 생성이 질의 600초를 넘는 것)는 실패로 기록하고 넘어간다
- 테스트 -> `tests/test_gt_guided_coord.py` · `tests/test_gt_guided_box.py` · 가짜 상자로 코디네이터·작업자의 **결정 로직**만 잰다. 실 vast · ssh · scp · `vastai` 호출은 0건이다(`subprocess`를 가짜로 바꾸고 진짜가 불리면 테스트가 실패한다) -- 진짜 `vastai`가 실패를 종료 코드로 내는지는 **미실측**이고 코디네이터는 그 출력의 끝 80자를 로그에 남긴다
- 안 고친 것 -> 비정상 종료한 작업자가 남기는 Joern 고아 프로세스(실 박스 검증 필요) · `port_open`이 아무 listener나 받아들임 · 코디네이터의 재배정 타이머가 push 시각부터 센다(claim 시각을 모른다 -- 큐 대기가 3시간에 들어간다) · 재배정된 작업이 같은 바퀴에 같은 상자로 다시 push됨(상자의 claim이 배타적이라 해롭지 않고, 끝난 표본의 중복 작업은 버려진다)
