# /metrics 프로브

vLLM `/metrics` 원문을 **두 장비에서 같은 절차로** 받아 내는 도구다. 2026-08-14 Slurm 클러스터에서
손으로 조립해 돌린 0단계 절차를 코드화한 것이고, 그 절차가 대화에만 있었던 것이 이 도구가
생긴 이유다.

> ⏵ 2026-10-08 — ATOM 쪽은 2026-08-18에 돌았다([보고서](../../docs/experiments/harness/2026-08-18-serving-atom.md)
> §3.3 — `kv_cache_memory_bytes` `None` · 부재 이름 1건). 그 원문은 `tests/fixtures/metrics/`에 안 들어왔고
> ATOM은 2026-09-29 사용 종료(#340)다. 아래 「ATOM에서 숫자면」·「ATOM 원문에서」는 그 전에 쓴 계획이고
> `### ATOM` 절은 그때의 절차다.

## 무엇을 판정하는가

이 프로브는 성능을 재지 않는다. 성능은 `python -m src.cli bench`가 잰다. 여기서 답하는
것은 **"이 서버가 우리가 읽는 이름으로 우리가 읽는 값을 내주는가"** 하나다.

| 출력 | 답하는 질문 | 어긋나면 |
| --- | --- | --- |
| `missing_metric_names` | `METRIC_NAMES` 7개가 이 서버에 다 있는가 | bench가 그 값을 못 읽는다. 버전별 이름 맵이 필요하다 |
| `metrics_delta.status` / `hits_delta` | 카운터가 before→after로 늘었는가 | `no_traffic`이면 엔드포인트 불일치, `metrics_reset`이면 구간 무효 |
| `cache_config.enable_prefix_caching` | prefix cache가 켜져 있는가 | `False`면 "적중 0"은 서버 설정 탓이다. 다시 띄워야 한다 |
| `cache_config.gpu_memory_utilization` | 점유를 재현할 수 있는가 | 이 값 없이 적힌 `mem_gb_ctx8k`는 다른 사람이 재현 못 한다(model-serving.md §5) |
| `cache_config.kv_cache_memory_bytes` | KV cache 크기를 서버가 알려주는가 | Slurm 클러스터는 `"None"`이었다. **ATOM에서 숫자면 `mem_gb_ctx8k`를 nvidia-smi 없이 얻는다** |
| `prompt_tokens_details` | 응답이 캐시 적중을 장부로 주는가 | `null`이면 R6 재현(open-questions R6). 어댑터 결함이 아니라 서버가 안 주는 것이다 |

## 왜 두 장비에서 같은 모델·같은 요청 수인가

원문 차이가 **순수하게 vLLM 버전 차이로** 분리되어야 하기 때문이다. 모델이나 요청 수가
다르면 "0.19.1은 이 이름이 없다"와 "0.6B 모델은 이 이름이 없다"를 가를 수 없다. 0단계에서
Qwen3-0.6B로 통일한 이유가 그것이고, 기본값 `REQUESTS=6` / `PREFIX_TOKENS=240`도 실측과
같은 값이다 — 원문의 `prefix_cache_queries_total`이 1458 = 243 × 6이다.

## 측정 순서를 지켜야 하는 이유

**Prometheus 카운터는 첫 관측 전에는 `/metrics`에 나타나지 않는다.** 서버를 띄우자마자
긁으면 `prefix_cache_queries_total`도 `prefix_cache_hits_total`도 문서에 없고, 그 상태를
보고 "이 서버에는 그 이름이 없다"고 결론 내리면 틀린다. 그래서

```
/v1/models 확인 → before 스냅샷 → 요청 N회 → after 스냅샷
```

순서를 지키고, **이름 판정은 반드시 after 스냅샷으로 한다.**

## 구성

| 파일 | 하는 일 |
| --- | --- |
| `probe_metrics.py` | 측정 본체. stdlib + `src.bench_metrics`만 쓴다 |
| `run_probe.sh` | vLLM 기동 → 프로브 → 종료. **두 장비에서 완전히 같아야 하는 부분** |
| `slurm.sh` | `srun` + conda + `/data/` 캐시. 잡 실행 방식만 감싼다 |
| `atom.sh` | 직접 실행 + 포트·GPU 점유 확인. 접속·조율만 감싼다 |

서버와 클라이언트를 **같은 노드**에서 돌린다. 그래서 endpoint는 두 장비 모두
`127.0.0.1`이고, 장비별로 host가 갈리지 않는다.

## 이름을 여기에 다시 적지 않는다

확인하는 메트릭 이름은 `src.bench_metrics.METRIC_NAMES`에서, 라벨은
`cache_config_labels()`에서 온다. 프로브가 이름을 문자열로 다시 적으면 bench가 읽는 것과
프로브가 확인하는 것이 갈라지는데, 정확히 그 자리에서 `gpu_cache_usage_perc` 사고가 났다
(존재하지 않는 이름인데 테스트 26개가 초록이었다). `tests/test_probe.py`가 프로브 소스에
`vllm:` 문자열 리터럴이 없다는 것을 강제한다.

**그래서 저장소를 클론해야 쓸 수 있다.** 대신 `pip install`은 필요 없다 —
`src.bench_metrics`도 `src.console`도 stdlib만 쓰고 `src/__init__.py`는 docstring뿐이다.
`jq`·`curl`도 쓰지 않는다(conda의 libcurl이 경고를 쏟는다).

## 돌리는 법

### Slurm 클러스터

```bash
tmux new -s probe                        # srun은 SSH가 끊기면 job까지 죽는다
sinfo -p <partition>                  # 비어 있는 노드 확인
PARTITION=<partition> tools/probe/slurm.sh --node <node> --out /data/<account>/probe
```

`--node`에 **기본값이 없다.** 클러스터의 계산 노드는 고정 대상이 아니라 그때그때 비어 있는 것을
쓰고, 노드마다 카드가 다르다(u1은 6GB, u3·u4는 12GB, u8은 Prolog failure로 사용 금지).
기본값을 두면 남이 쓰는 노드에 얹히거나 6GB 카드에서 재고도 그 사실이 산출물에 안 남는다.
범위(`<node>[a-b]`)로 주면 `Node count specification invalid`다.

외부망에서는 접속 포트가 다르다(`ssh -p <ssh-port>`).

### ATOM

```bash
tmux new -s probe
tools/probe/atom.sh --port 8001 --out ~/probe
```

**계정이 공용일 수 있다.** 포트가 이미 쓰이면 죽고, GPU를 쓰는 프로세스가 있으면 확인을
받는다. 프로브도 `/v1/models`의 id를 `--served-model-name`과 대조해 한 번 더 막는다 —
남이 띄운 서버에 요청을 쏘고 그 수치를 우리 것으로 기록하는 일이 없어야 한다.

## 서버가 안 뜰 때 — 인터페이스가 여러 개인 장비 (2026-08-17 ATOM 실측)

`run_probe.sh`가 아래 셋을 미리 설정한다. **이미 설정돼 있으면 덮지 않는다** — 다중
노드 환경에서는 loopback 강제가 틀린 값이기 때문이다.

```
VLLM_HOST_IP=127.0.0.1
NCCL_SOCKET_IFNAME=lo
GLOO_SOCKET_IFNAME=lo
```

**증상을 알아두는 것이 요점이다.** 이게 없으면 vLLM이 이렇게 죽는다:

```
distributed_init_method=tcp://<private-ip>:54029 backend=nccl
[c10d] The client socket has failed to connect to [atom-host]:54029
       (errno: 113 - No route to host)
TCP client failed ... retrying (try=0, timeout=600000ms, ...)
```

`world_size=1`이어도 torch.distributed의 TCPStore를 띄우는데, 자기 주소로 WiFi
인터페이스를 골라 **자기 자신에게** 접속하려다 실패한다. `timeout=600000ms`라 **10분짜리
재시도 루프**에 갇히고 매번 C++ 백트레이스를 찍는다. 프로브는 `--wait`(기본 300초)에서
먼저 포기하므로 사람이 보는 것은 "서버가 안 떴다"뿐이다.

**이건 R1 실패가 아니다.** 그 로그는 `Resolved architecture: Qwen3ForCausalLM`,
`device_config=cuda`까지 정상으로 갔다 — **GPU 작업이 시작되기 전, 네트워크 초기화에서**
죽은 것이다. 커널 호환성 문제로 오진하면 크리티컬 패스(R1)에 대한 거짓 보고가 된다.

기동이 느린 것 자체가 원인일 수도 있다. 대기 상한은 `WAIT_S`로 늘린다.

```bash
WAIT_S=900 tools/probe/atom.sh --port 8001 --out ~/probe
```

## 산출물이 곧 fixture다

받아서 그대로 옮기면 된다. **손으로 이름을 바꾸는 단계는 없다.**

| 파일 | fixture인가 |
| --- | --- |
| `vllm_<버전>_<장비>_<아키>.txt` | **예** — after 스냅샷 |
| `vllm_<버전>_<장비>_<아키>_chat_completion.json` | **예** — 첫 성공 응답 원문 |
| `vllm_<버전>_<장비>_<아키>.before.prom` | 아니다 |
| `vllm_<버전>_<장비>_<아키>.probe.json` | 아니다 — 판정 요약 |
| `vllm-server.log` | 아니다 — 기동에 실패하면 여기를 본다 |

```bash
cp <out>/vllm_0110_atom_sm121.txt <out>/vllm_0110_atom_sm121_chat_completion.json \
   tests/fixtures/metrics/
```

**before 스냅샷은 옮기지 않는다.** 그 문서에는 카운터가 아직 없어서(위 "측정 순서"),
`tests/fixtures/metrics/`에 들어가면
`test_bench_reads_only_names_that_exist_in_every_fixture`가 `*.txt`를 전부 훑다가 "이름이
없다"고 실패한다. 확장자를 `.prom`으로 둔 것이 그 실수를 구조적으로 막는다. 프로브의
마지막 출력이 옮길 파일 두 개를 그대로 찍어 준다.

버전·아키텍처는 `/metrics`도 `/v1/models`도 주지 않으므로(실측 확인) 추론하지 않고 받는다.
`run_probe.sh`가 서버를 띄운 그 환경에서 `vllm.__version__`을 직접 물어 넘긴다 — 사람이
손으로 적으면 틀린 이름의 fixture가 CI를 통과한다.

원문은 `.gitattributes`가 `tests/fixtures/metrics/** -text`로 빼 두었다. 서버가 내보낸
바이트를 그대로 두기 위해서이고, 프로브도 같은 이유로 개행을 번역하지 않는다.

### 옮기고 나면 CI가 판정한다

`test_bench_reads_only_names_that_exist_in_every_fixture`는 fixture **파일마다**
`METRIC_NAMES` 전부가 있는지 본다. ATOM 원문에서 이름이 하나라도 다르면 **CI가 빨개지는
것이 의도다** — 그 실패가 정확히 우리가 사려는 신호이고, 그때 버전별 이름 맵을 도입한다.
미리 관대하게 만들면 `gpu_cache_usage_perc` 사고가 그대로 재발한다.
