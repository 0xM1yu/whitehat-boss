# 평가셋

고정 표본과 digest 매니페스트를 둔다. `problem_names.txt`(한 줄에 문제명 하나)를 놓으면
`python -m eval.leakage`가 문제명 마스킹까지 검사한다. **실제 데이터는 커밋하지 않는다.** CyberGym 과제 번들은 받는다: `python tools/cybergym_task.py arvo:1065 --out eval/datasets/arvo-1065` ([tools/README.md](../../tools/README.md)).
