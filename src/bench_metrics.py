"""`/metrics`를 읽는 계층. 이름·파싱·카운터 스냅샷·구간 delta가 전부 여기 있다.

측정 시나리오(`src/bench.py`)와 나눈 이유는 두 가지다. 첫째 수명이 다르다 — 여기는
**vLLM이 무엇을 어떤 이름으로 내보내는가**이고, 그건 서버 버전을 올릴 때 바뀐다. 둘째
`src/bench.py`가 800줄 상한(engineering-guide.md §8.1)을 넘었다.

**이름이 틀린 채로 통과한 적이 있다**(2026-08-14): `vllm:gpu_cache_usage_perc`는 존재하지
않고 `vllm:kv_cache_usage_perc`가 맞는데, 스텁이 bench가 기대하는 이름을 그대로 찍어 주어
테스트 26개가 전부 초록이었다. 그래서 이름을 `METRIC_NAMES`에 모으고,
`tests/test_bench.py`가 그 상수를 **실측 원문 fixture**와 대조한다.
"""

import http.client
import re
import shutil
import subprocess
import urllib.error
import urllib.request
from collections.abc import Iterable
from dataclasses import dataclass

_TIMEOUT_S = 60


# bench가 `/metrics`에서 찾는 이름 전부. **조회는 반드시 이 상수를 지난다.**
#
# 이름을 문자열로 흩뿌렸다가 하나가 틀린 채 통과했다(2026-08-14): `vllm:gpu_cache_usage_perc`는
# 존재하지 않는 이름이고 실제로는 `vllm:kv_cache_usage_perc`다. 스텁이 bench가 기대하는
# 이름을 그대로 찍어 주었기 때문에 테스트 26개가 전부 초록이었다. 그래서 (1) 이름을 한곳에
# 모으고 (2) `tests/test_bench.py`가 이 상수를 실측 원문 fixture와 대조한다.
METRIC_PREFIX_QUERIES = "vllm:prefix_cache_queries_total"
METRIC_PREFIX_HITS = "vllm:prefix_cache_hits_total"
METRIC_EXTERNAL_HITS = "vllm:external_prefix_cache_hits_total"
METRIC_TOKENS_CACHED = "vllm:prompt_tokens_cached_total"
METRIC_TOKENS_RECOMPUTED = "vllm:prompt_tokens_recomputed_total"
METRIC_KV_USAGE = "vllm:kv_cache_usage_perc"
METRIC_CACHE_CONFIG = "vllm:cache_config_info"
# bench가 아니라 `openai_compat`이 읽는다 — 타임아웃이 「느림」인지 「멈춤」인지 가른다(#261).
# 이름이 여기 있는 것은 위 fixture 대조를 같이 받기 위해서다.
METRIC_GENERATION_TOKENS = "vllm:generation_tokens_total"

METRIC_NAMES: tuple[str, ...] = (
    METRIC_PREFIX_QUERIES,
    METRIC_PREFIX_HITS,
    METRIC_EXTERNAL_HITS,
    METRIC_TOKENS_CACHED,
    METRIC_TOKENS_RECOMPUTED,
    METRIC_KV_USAGE,
    METRIC_CACHE_CONFIG,
    METRIC_GENERATION_TOKENS,
)

# 생성 카운터 한 번 읽기의 상한. 요청마다 한 번 읽으므로 `_TIMEOUT_S`(60)를 쓰지 않는다 —
# 행에 걸린 서버에서 `/metrics`까지 늦으면 판정 하나에 1분씩 붙는다. 로컬 루프백이라 짧게 둔다.
_COUNTER_TIMEOUT_S = 5

# `vllm:prompt_tokens_by_source_total`은 여기에 없다. 이번 범위에서 쓰지 않기 때문이지
# 쓸모가 없어서가 아니다 — **local/external을 라벨 하나(`source=`)로 분해하는 가장 깨끗한
# 출처다**(실측: local_compute 338 / local_cache_hit 1120 / external_kv_transfer 0).
# 지금은 세 메트릭(prefix_cache_hits · external_prefix_cache_hits · prompt_tokens_cached)을
# 조합해 같은 것을 재구성하고 있다. `_sum_metric`이 라벨을 구분하지 않고 전부 더하므로
# (이 이름에 쓰면 1458.0 = prompt_tokens_total이 나온다) 라벨 인식 헬퍼가 생기기 전에는
# 쓸 수 없다. 그 헬퍼가 생기면 이쪽이 정답이다.


# --- HTTP / metrics ---------------------------------------------------------


def metrics_url(endpoint: str) -> str:
    """`http://host:8001/v1` → `http://host:8001/metrics`.

    엔드포인트는 config에서 온다. 호스트·포트를 여기에 박지 않는다(engineering-guide §8.5).
    """
    base = endpoint.rstrip("/")
    root = base[: -len("/v1")] if base.endswith("/v1") else base
    return f"{root}/metrics"


def fetch(url: str) -> tuple[str | None, str | None]:
    """(본문, 실패 사유). 예외로 올리지 않는 것은 사유를 결과에 실어야 하기 때문이다."""
    try:
        with urllib.request.urlopen(url, timeout=_TIMEOUT_S) as response:  # noqa: S310
            return response.read().decode("utf-8"), None
    except (
        urllib.error.URLError,
        TimeoutError,
        OSError,
        ValueError,
        http.client.HTTPException,
    ) as exc:
        # `HTTPException`은 `OSError`가 아니다 — 아래 `generation_tokens`와 같은 이유로 더했다.
        return None, f"{url} 요청 실패: {type(exc).__name__}: {exc}"


_SAMPLE = re.compile(r"^(?P<name>[a-zA-Z_:][\w:]*)(?:\{(?P<labels>[^}]*)\})?\s+(?P<value>\S+)$")
_LABEL = re.compile(r'(\w+)="([^"]*)"')


def parse_prometheus(text: str) -> list[tuple[str, dict[str, str], float]]:
    """Prometheus 텍스트 노출 형식을 (이름, 라벨, 값)으로 편다."""
    samples: list[tuple[str, dict[str, str], float]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = _SAMPLE.match(line)
        if match is None:
            continue
        try:
            value = float(match.group("value"))
        except ValueError:
            continue
        labels = dict(_LABEL.findall(match.group("labels") or ""))
        samples.append((match.group("name"), labels, value))
    return samples


def _sum_metric(samples: Iterable[tuple[str, dict[str, str], float]], name: str) -> float | None:
    """같은 이름의 모든 라벨 조합을 더한다. 없으면 None — 0과 구분한다."""
    values = [value for metric, _, value in samples if metric == name]
    return sum(values) if values else None


def generation_tokens(endpoint: str) -> float | None:
    """그 서버가 지금까지 낸 생성 토큰 누계. 못 읽으면 `None` — 0(안 냈다)과 가른다."""
    try:
        with urllib.request.urlopen(  # noqa: S310
            metrics_url(endpoint), timeout=_COUNTER_TIMEOUT_S
        ) as response:
            text = response.read().decode("utf-8")
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, http.client.HTTPException):
        # ⏵ 2026-10-02 — `http.client.HTTPException`을 더했다(적대적 리뷰 실측). **`OSError`가
        # 아니라서** 안 걸렸고, 그러면 `IncompleteRead`·`BadStatusLine`이 여기서 생으로 올라간다.
        # 이 함수의 호출처는 `openai_compat._post`의 `before`(try **밖**)와 `TimeoutError` 갈래 안
        # `after`다 — 둘 다 그 함수의 예외 사슬이 못 덮는 자리라, 새면 러너까지 간다.
        # 이 함수의 계약은 "못 재면 `None`"이고 측정 실패는 사건이 아니다.
        return None
    return _sum_metric(parse_prometheus(text), METRIC_GENERATION_TOKENS)


@dataclass(frozen=True)
class CacheCounters:
    """`/metrics`의 캐시 카운터 한 시점. 절대값은 구간 delta를 만들 때만 쓴다.

    **네 카운터가 세는 것이 서로 다르다** (2026-08-14 Slurm 실측, HELP 원문 기준):

    | 필드 | 메트릭 | 세는 것 |
    | --- | --- | --- |
    | `hits` | `prefix_cache_hits_total` | **local만.** 단위는 토큰 |
    | `external_hits` | `external_prefix_cache_hits_total` | KV connector 경유 **external만** |
    | `tokens_cached` | `prompt_tokens_cached_total` | **local + external** |
    | `tokens_recomputed` | `prompt_tokens_recomputed_total` | 캐시됐다가 재계산된 토큰 |

    실측에서 `hits`와 `tokens_cached`가 **똑같이 1120.0**이었지만 그건 external이 0이었기
    때문이다. 값이 같다고 같은 것을 세는 것이 아니다.
    """

    queries: float | None
    hits: float | None
    error: str | None = None
    # 아래 셋은 `usable` 판정에 들어가지 않는다 — 이 이름들이 없는 vLLM 버전에서
    # verdict 전체가 `metrics_unavailable`로 무너지면 R6 판정 자체를 못 하게 된다.
    # 없으면 그 필드만 None으로 남고 delta에 사유가 실린다.
    external_hits: float | None = None
    tokens_cached: float | None = None
    tokens_recomputed: float | None = None
    # `cache_config_info`의 `enable_prefix_caching` 라벨. 같은 스크레이프에서 나오므로
    # 요청이 늘지 않는다. 이것이 있으면 "꺼져 있다"와 "켜져 있는데 안 맞는다"를 가를 수
    # 있다 — 앞은 서버를 다시 띄워야 하는 문제이고 뒤는 진짜 조사 대상이다.
    prefix_caching_enabled: bool | None = None

    @property
    def usable(self) -> bool:
        return self.error is None and self.queries is not None and self.hits is not None


def _config_label(samples: Iterable[tuple[str, dict[str, str], float]], key: str) -> str | None:
    """`vllm:cache_config_info`의 라벨 하나. 없으면 None."""
    for name, labels, _ in samples:
        if name == METRIC_CACHE_CONFIG and key in labels:
            return labels[key]
    return None


def read_counters(endpoint: str) -> CacheCounters:
    text, error = fetch(metrics_url(endpoint))
    if text is None:
        return CacheCounters(None, None, error)
    return counters_from(text)


def counters_from(text: str) -> CacheCounters:
    """**이미 받아 둔** `/metrics` 문서에서 카운터를 뽑는다.

    `read_counters()`가 엔드포인트를 받아 하는 일에서 스크레이프만 뺀 것이다. 원문을
    따로 보관해야 하는 쪽(`tools/probe/`)이 같은 시점에 두 번 긁지 않도록 나눴다 —
    두 번 긁으면 그 사이의 요청이 어느 쪽에 잡혔는지 알 수 없고, 저장한 원문이 리포트의
    delta가 나온 문서와 달라진다. 아래 본문이 "한 스크레이프에서 전부 뽑는다"고 말하는
    것과 같은 이유다.
    """
    samples = parse_prometheus(text)
    queries = _sum_metric(samples, METRIC_PREFIX_QUERIES)
    hits = _sum_metric(samples, METRIC_PREFIX_HITS)
    if queries is None or hits is None:
        # **찾던 이름을 그대로 싣는다.** "카운터가 없다"로 적으면 다음 사람이 어느 이름을
        # 찾아야 하는지부터 다시 알아내야 한다. 이름은 vLLM 버전 사이에서 바뀐다.
        missing = [
            name
            for name, value in ((METRIC_PREFIX_QUERIES, queries), (METRIC_PREFIX_HITS, hits))
            if value is None
        ]
        return CacheCounters(queries, hits, f"{', '.join(missing)}가 /metrics에 없다")
    # 같은 스크레이프 한 번에서 뽑는다. 새 경로를 만들지 않는다 — 두 번 긁으면 그 사이의
    # 요청이 어느 쪽에 잡혔는지 알 수 없어 구간이 흐려진다.
    return CacheCounters(
        queries,
        hits,
        external_hits=_sum_metric(samples, METRIC_EXTERNAL_HITS),
        tokens_cached=_sum_metric(samples, METRIC_TOKENS_CACHED),
        tokens_recomputed=_sum_metric(samples, METRIC_TOKENS_RECOMPUTED),
        prefix_caching_enabled=_enabled_flag(samples),
    )


def _enabled_flag(samples: Iterable[tuple[str, dict[str, str], float]]) -> bool | None:
    """`enable_prefix_caching` 라벨을 bool로. 못 읽으면 None(= 모른다)."""
    raw = _config_label(samples, "enable_prefix_caching")
    if raw is None:
        return None
    return raw.strip().lower() == "true"


def counter_delta(
    before: CacheCounters, after: CacheCounters, *, calls_ok: int | None = None
) -> dict[str, object]:
    """구간 증분. **절대값을 쓰지 않는다** — 서버가 언제 떴는지와 앞 구간이 무엇을 데웠는지가
    절대값에 그대로 섞인다(사유는 `src/bench.py` 모듈 docstring "측정 순서가 결과를 바꾼다").

    `calls_ok`는 그 구간에 **성공한 호출 수**다. 엔진이 본 요청이 0인데 성공 호출이
    있으면 다른 서버의 `/metrics`를 긁고 있다는 뜻이므로(역할마다 포트가 다르다),
    그 조합에서는 추측이 아니라 단정할 수 있다 — `_no_traffic_reason` 참조.
    """
    if not before.usable or not after.usable:
        return {
            "status": "metrics_unavailable",
            "reason": before.error or after.error or "카운터를 읽지 못했다",
        }
    queries = (after.queries or 0.0) - (before.queries or 0.0)
    hits = (after.hits or 0.0) - (before.hits or 0.0)
    if queries < 0 or hits < 0:
        # 서버 재기동 등으로 카운터가 리셋됐다. 이 구간은 무효다 — 음수를 0으로 접으면
        # "적중 없음"이라는 적극적인 거짓말이 된다.
        return {
            "status": "metrics_reset",
            "reason": f"카운터가 감소했다 (queries {queries:+.0f}, hits {hits:+.0f}). 구간 무효",
            "queries_delta": queries,
            "hits_delta": hits,
        }
    if queries == 0:
        # 카운터는 정상 판독됐다. 다만 **엔진이 이 구간에 요청을 한 건도 보지 못했다.**
        # 이걸 `ok`로 흘리면 `verdict_for`가 `neither`로 떨어져 "캐시가 매번 축출되고
        # 있다"고 단언한다 — 음수를 0으로 접는 것과 같은 종류의 거짓말이다.
        return {
            "status": "no_traffic",
            "reason": _no_traffic_reason(calls_ok),
            "queries_delta": queries,
            "hits_delta": hits,
            "hit_ratio": None,
            "prefix_caching_enabled": after.prefix_caching_enabled,
            **_ledger_substitutes(before, after),
        }
    return {
        "status": "ok",
        "queries_delta": queries,
        # local 적중만이다. external은 아래 필드로 따로 나간다.
        "hits_delta": hits,
        "hit_ratio": round(hits / queries, 4),
        "prefix_caching_enabled": after.prefix_caching_enabled,
        **_ledger_substitutes(before, after),
    }


def _no_traffic_reason(calls_ok: int | None) -> str:
    """엔진이 본 요청이 0일 때의 사유. 아는 만큼만 좁혀 적는다.

    **성공 호출이 있는데 엔진이 본 요청이 0이면 엔드포인트 불일치가 확정이다.** 역할마다
    포트가 다르므로(8000~8005, `config/models.yaml`) 한 역할을 호출하면서 다른 역할의
    `/metrics`를 긁으면
    정확히 이 조합이 나온다. **장부가 항상 0인 것은 아니다** — 2026-10-12 서빙 run의
    `model_call` 45건 중 15건이 0이 아닌 `cached_tokens`를 든다(R6 ⏵ 2026-10-13).
    그래도 `ledger_only`에 기댈 수 없다: 장부가 0으로 오는 구간이 남고, 그런 구간에서는
    이 검사가 없으면 엔드포인트 불일치가 "캐시가 안 걸린다"로 보고된다.
    """
    if calls_ok is None:
        return "이 구간에 엔진이 본 요청이 0이다 — 엔드포인트 불일치이거나 요청이 전부 실패했다"
    if calls_ok > 0:
        return (
            f"이 구간에 성공한 호출이 {calls_ok}건인데 엔진이 본 요청은 0이다 — "
            "호출한 엔드포인트와 /metrics를 긁은 서버가 다르다(엔드포인트 불일치)"
        )
    return "이 구간에 성공한 호출이 0건이다 — 요청이 전부 실패했다. 캐시에 대해서는 아무것도 모른다"


def _ledger_substitutes(before: CacheCounters, after: CacheCounters) -> dict[str, object]:
    """장부(`Usage.cached_tokens`)가 비었을 때 쓸 수 있는 대체 수치와 그 한계.

    `prompt_tokens_cached_total`의 구간 delta는 응답의 `prompt_tokens_details.cached_tokens`가
    주었어야 할 값과 **같은 단위(토큰)**다. 서버가 응답에 그 값을 싣지 않아도(R6) 구간
    합계는 얻을 수 있다.

    **어느 값을 장부로 쓸지는 정하지 않는다.** `tokens_cached`는 local+external이고
    `hits`는 local만이라 하네스가 KV connector를 쓰기 시작하면 둘이 갈라진다. 지금
    정하면 external이 0인 환경에서만 맞는 선택이 박힌다. 그래서 셋을 모두 싣고,
    `external_prefix_cache_hits_delta`로 **갈라졌는지 여부가 사후에 판별되게** 한다.
    """
    fields: dict[str, object] = {}
    for key, metric, start, end in (
        (
            "prompt_tokens_cached_delta",
            METRIC_TOKENS_CACHED,
            before.tokens_cached,
            after.tokens_cached,
        ),
        (
            "prompt_tokens_recomputed_delta",
            METRIC_TOKENS_RECOMPUTED,
            before.tokens_recomputed,
            after.tokens_recomputed,
        ),
        (
            "external_prefix_cache_hits_delta",
            METRIC_EXTERNAL_HITS,
            before.external_hits,
            after.external_hits,
        ),
    ):
        if start is None or end is None:
            # 0과 구분한다. 0은 "쟀는데 없었다"이고 None은 "이 서버가 안 준다"이다.
            fields[key] = None
            fields[f"{key}_reason"] = f"{metric}가 /metrics에 없다"
            continue
        value = end - start
        if value < 0:
            # queries/hits에 적용한 것과 같은 기준이다. 0으로 접으면 "캐시 토큰 -50개"가
            # note에 실리거나 0으로 둔갑한다. 감소했다는 것은 그 필드의 구간이 무효라는 뜻이다.
            fields[key] = None
            fields[f"{key}_reason"] = f"{metric} 카운터가 감소했다({value:+.0f}) — 구간 무효"
            continue
        fields[key] = value
    return fields


def cache_config_labels(endpoint: str) -> tuple[dict[str, str], str | None]:
    """`vllm:cache_config_info`의 라벨 **전부**. (라벨, 실패 사유).

    `gpu_memory_utilization()`이 라벨 하나를 골라 float으로 주는 것과 달리 통째로 준다.
    프로브(`tools/probe/`)가 봐야 하는 값 중 `kv_cache_memory_bytes`·`num_gpu_blocks`처럼
    **메트릭이 아니라 이 게이지의 라벨인** 것들이 있는데, 그 키를 프로브가 문자열로 다시
    적으면 이름이 갈라질 자리가 그대로 생긴다 — vLLM이 내보내는 이름은 전부 이 모듈을
    지난다는 것이 `METRIC_NAMES`의 존재 이유다.

    **`kv_cache_memory_bytes`를 `METRIC_NAMES`에 넣는 것으로는 해결되지 않는다.** 그것은
    독립 메트릭이 아니라 라벨이라 `/metrics`에 그 이름의 샘플이 없고, 넣으면
    `tests/test_bench.py::test_bench_reads_only_names_that_exist_in_every_fixture`가 실측
    원문에서 즉시 깨진다. 라벨은 라벨로 다룬다.

    값을 해석하지 않고 문자열 그대로 준다. 실측에서 `kv_cache_memory_bytes="None"`,
    `gpu_memory_utilization="0.6"`처럼 **라벨마다 타입이 다르고** vLLM이 파이썬 `None`을
    문자열로 찍기 때문이다(2026-08-14 Slurm 원문). 해석은 쓰는 쪽이 한다.
    """
    text, error = fetch(metrics_url(endpoint))
    if text is None:
        return {}, error
    labels = config_labels_from(text)
    return (labels, None) if labels else ({}, f"{METRIC_CACHE_CONFIG}가 /metrics에 없다")


def config_labels_from(text: str) -> dict[str, str]:
    """**이미 받아 둔** 문서에서 `cache_config_info` 라벨을. 없으면 빈 dict.

    `counters_from()`과 같은 이유로 나눴다 — 원문을 손에 쥔 쪽이 라벨을 보려고 서버를
    다시 긁을 이유가 없다. 사유가 필요한 호출자는 `cache_config_labels()`를 쓴다.
    """
    for name, labels, _ in parse_prometheus(text):
        if name == METRIC_CACHE_CONFIG:
            return dict(labels)
    return {}


def gpu_memory_utilization(endpoint: str) -> tuple[float | None, str | None]:
    """`vllm:cache_config_info`의 라벨에서 읽는다.

    이 값 없이는 `mem_gb_ctx8k`를 다른 사람이 재현할 수 없다 — 같은 카드라도 이 비율이
    다르면 점유가 통째로 달라진다. 그래서 `--apply`의 전제 조건이다.
    """
    text, error = fetch(metrics_url(endpoint))
    if text is None:
        return None, error
    raw = _config_label(parse_prometheus(text), "gpu_memory_utilization")
    if raw is None:
        return None, f"{METRIC_CACHE_CONFIG}에 gpu_memory_utilization 라벨이 없다"
    try:
        return float(raw), None
    except ValueError:
        return None, f"{METRIC_CACHE_CONFIG}의 gpu_memory_utilization({raw!r})을 수로 읽지 못했다"


def kv_cache_perc(endpoint: str) -> float | None:
    """`vllm:kv_cache_usage_perc` — KV cache 점유율 게이지(1.0 = 100%).

    **counter가 아니라 gauge다.** 요청이 끝나면 KV cache가 반납되어 0.0으로 돌아간다
    (2026-08-14 Slurm 실측: 요청 6회를 끝낸 뒤 스크레이프한 원문에서 이 값이 0.0이다,
    `tests/fixtures/metrics/vllm_0191_slurm_sm86.txt`). 그래서 이 함수를 부르는 쪽은
    요청이 떠 있는 동안 반복해서 표본을 떠야 한다 — `_one_context` 참조.
    """
    text, _ = fetch(metrics_url(endpoint))
    if text is None:
        return None
    return _sum_metric(parse_prometheus(text), METRIC_KV_USAGE)


def nvidia_smi_mb() -> tuple[float | None, str | None]:
    """(MB, 실패 사유). 값을 못 얻으면 **왜 못 얻었는지**를 함께 돌려준다.

    **세 경우가 서로 다르고, 그 차이가 다음 사람을 어디로 보낼지를 정한다.**

    | 경우 | 사람이 할 일 |
    | --- | --- |
    | 실행 파일이 PATH에 없다 | 드라이버·PATH를 본다 |
    | 실행이 실패했다 / 비정상 종료 | 그 오류를 본다 |
    | **정상 종료했는데 `[N/A]`다** | **이 쿼리로는 고칠 게 없다.** 다른 쿼리·소스를 찾는다 |

    셋을 한 사유로 뭉치면 마지막 경우에서 거짓말이 된다 — 2026-08-17 ATOM(GB10) 실측:

        nvidia-smi --query-gpu=name,compute_cap,memory.total,memory.used --format=csv,noheader
        → NVIDIA GB10, 12.1, [N/A], [N/A]

    통합메모리라 별도 VRAM이 없어서다. **`--query-gpu`에 대한 이야기다** — 같은 장비에서
    `--query-compute-apps`는 프로세스별 값을 준다(2026-09-03 실측). 그쪽이 `mem_gb_ctx8k`를
    바로 채우지는 못한다(PID별이고 context 고정이 아니다) — 09-03 교대 보고서 §3.4.
    nvidia-smi는 PATH에 있고 정상 종료한다. 그런데
    사유가 "PATH에 없거나 실패했다"로 기록되면 다음 사람이 드라이버를 뒤진다. `None`과
    `0.0`을, `metrics_only`와 `neither`를 가른 것과 같은 이유다.
    """
    if shutil.which("nvidia-smi") is None:
        return None, "nvidia-smi가 PATH에 없다"
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"nvidia-smi 실행이 실패했다: {exc}"
    if out.returncode != 0:
        detail = (out.stderr or out.stdout).strip().splitlines()
        head = detail[0] if detail else "출력 없음"
        return None, f"nvidia-smi가 종료 코드 {out.returncode}로 끝났다: {head}"
    values = [float(tok) for tok in out.stdout.split() if tok.strip().isdigit()]
    if values:
        return max(values), None
    return None, (
        f"nvidia-smi가 정상 종료했지만 memory.used를 숫자로 주지 않았다({out.stdout.strip()!r}) — "
        "통합메모리 장비는 별도 VRAM이 없어 이 값을 보고하지 않는다(2026-08-17 GB10 실측). "
        "PATH·드라이버 문제가 아니다"
    )
