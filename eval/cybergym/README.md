# CyberGym 과제 받기

이 저장소는 CyberGym 과제 데이터를 담지도, 재배포하지도 않는다. 과제는 Hugging Face
`sunblaze-ucb/cybergym`에서 직접 받는다.

```bash
python tools/cybergym_task.py arvo:1065 --out eval/datasets/arvo-1065
```

받는 파일은 Level 1에 필요한 `repo-vul.tar.gz`·`description.txt` 둘뿐이고 정답 자료는 요청하지 않는다.
revision 고정·해시 대조·`submit.sh` 생성은 [tools/README.md](../../tools/README.md)의
「`cybergym_task.py` — 과제 번들 받기」 절을 본다.
