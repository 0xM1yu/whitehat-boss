# models/

하네스에서 바로 참조하지 않는, 전달(handoff) 목적의 모델 스냅샷 저장소다.
`config/models.yaml`의 모델 레지스트리는 `hf://` 또는 `file:///` 같은 외부 경로만
참조하므로, 여기 있는 파일들은 아직 어떤 역할(auditor/critic/scout 등)에도
연결(wiring)되어 있지 않다. 레지스트리 연결은 이 PR이 머지된 뒤 별도로 진행한다.

## sft_1-1/

`Qwen/Qwen3.8-27B`를 베이스로 한 LoRA SFT 어댑터 스냅샷 3종(epoch1~3).
각 폴더는 추론에 필요한 파일만 담고 있고, 학습 재개용 체크포인트
(`optimizer.pt`, `rng_state_*.pth`, `scheduler.pt`, `training_args.bin`)는
용량이 크고(스냅샷당 600MB대) 서빙에 쓰이지 않아 제외했다.

- `adapter_config.json`, `adapter_model.safetensors` — PEFT LoRA 어댑터 가중치
- `tokenizer_config.json`, `tokenizer.json`(있는 경우), `chat_template.jinja`
- `trainer_state.json` — 학습 진행 상황 참고용 메타데이터
- `README.md` — 원본 모델 카드 (대부분 미작성 상태로 생성됨)

`adapter_model.safetensors`, `tokenizer.json`은 git-lfs로 추적한다.
