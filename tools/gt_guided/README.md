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
| `gt_worker.py` | 장비 | 작업자 W개, 각자 Joern 서버 하나. `inbox/`에서 작업을 하나씩 가져가 받기 -> 인덱스 -> `gt_slice` -> `build_auditor` -> `out/<id>/` |
| `coord.py` | 사용자 PC | 장비마다 `inbox/`를 작업자 수 x 2 이상 유지(큰 저장소 먼저) · 끝난 표본을 받아(sha256 확인) 표본 단위로 19MB 이하가 되게 묶어 `Downloads/ft_gt_auditor_batchNNN.tar` + 디스코드 웹훅 첨부(넘치는 표본은 다음 묶음, 혼자 19MB 넘는 표본은 조각내 전송) · 멈춘 작업 재배정 · 다 받으면 vast 인스턴스 삭제 |

## 장비 배치

```
/root/gt/harness   <- 이 저장소 체크아웃(또는 git archive)
/root/gt/joern     <- Joern 4.0.605
/root/gt/venv      <- python3.11 + pydantic·pyyaml·tokenizers·tree-sitter(-c·-cpp) (tools/harness-tools/Dockerfile 고정 버전)
py harness/tools/gt_guided/gt_worker.py <W>
```

- 작업 JSON -> `sample_id` · `repo_url` · `commit` · `gt_points`(`role`·`file`·`line`·`line_end`) · `description` · `project`
- 슬라이서 인자 -> `--max-flows-per-candidate 10 --backward-depth 30 --backward-nodes 5000`, 질의 600초 · 후보 600초, 표본 상한 2400초(`HARD_SECONDS`)

## 한계

- 실 Joern 종단 1회 -> 2026-10-03 vast 1대(EPYC 7K62, 작업자 4) · Joern 4.0.605 · s500 vuln 4개(json · proj.4 · readstat · ndpi)
  - 4/4 오류 없음 · 표본당 약 1분 · 후보 470~4,416 -> 1~10개 남김(`gt_function`) · 4/4에서 Auditor 입력에 GT 줄 보임
  - `coord.py` 배정 -> 완료 감지 -> sha256 확인 내려받기 -> 인스턴스 삭제까지 확인
  - run ID 없음 · 원자료 저장소 밖
- 표본 4개뿐 -> 큰 저장소·source 라벨 있는 표본·인덱서가 함수를 못 뽑는 C++는 아직 안 돌려 봄
- source 라벨이 없는 표본은 sink·root_cause 함수 쪽 후보만 남는다
- C++ 헤더처럼 인덱서가 함수를 못 뽑으면 GT 범위가 GT 줄 앞뒤 30줄로 떨어진다
- 큰 저장소(CPG 생성이 질의 600초를 넘는 것)는 실패로 기록하고 넘어간다
