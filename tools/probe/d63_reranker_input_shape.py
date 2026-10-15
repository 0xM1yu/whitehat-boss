"""D63 (a) — qwen3-reranker-4b를 **우리 입력 모양**에서 잰다.

D63([docs/open-questions/D/D63.md])의 선택지 (a): 코드 블록 8건(취약 3 · 안전한 문자열처리
1 · 무관 4)에 질의 3종을 각 3회 보낸다. 두 렌즈로 읽는다 — **전체-8 판정**("취약 3건이 무관
4건보다 전부 위인가")과 **top-5**(리랭커가 실제로 캡슐에 넣는 `DEFAULT_TOP_N=5`에 취약 몇 건).
배선 관문은 top-5다. ADR 0010 후속 1이 대상을 `qwen3-reranker-4b`로 고정했다(0.6b 관측 이식 금지).

질의 3종:
  Q1 `parse_header`                                   — 오늘의 실경로 그대로(식별자 하나)
  Q2 `parse_header memcpy len`                        — keywords가 배선되면 나올 모양
  Q3 `buffer overflow vulnerability in C string handling` — 관측 2와 같은 질의(대조군)

모드 2종([2026-09-08 로스터](docs/research/2026-09-08-roster.md) §2 배제셋 ②템플릿 / D63 미확인②):
  raw       — vllm_retrieval.py:83~95가 오늘 보내는 그대로 query/documents 원문
  templated — Qwen3-Reranker instruction 틀을 우리가 붙여 보낸다(⚠ 포맷은 추정)
  → 이 프로브가 말하는 것은 **「client 틀이 순위를 바꾸는가」까지**다. 「서버가 이미 붙이는가」는
     읽지 않는다 — 이중 적용이 반드시 나빠진다는 전제도, 내 틀 포맷이 서버 것과 같은지도 안 쟀다.
     확정은 vLLM 소스(미확인②).

⚠ 이 프로브가 **안 닫는 것**(D63 (a) 그대로): 문서 8건이지 32건 아님 · **사람이 골랐지 embedding이
고르지 않음** · 온전한 함수라 실제(40줄 경계에서 잘린 조각)보다 리랭커에 유리 · 크기 축 미측정(4B
하나). run_id 없음(비교표 밖).
"""

import json
import statistics
import sys
import urllib.request

# `qwen3-reranker-4b`의 포트(`config/models.yaml`의 endpoint).
# ⏵ 2026-10-15 — 8005에서 8004로 내려왔다.
ENDPOINT = "http://127.0.0.1:8004/v1/rerank"
MODEL = "qwen3-reranker-4b"
REPS = 3

# --- 문서 8건 -----------------------------------------------------------------
# label: (V=취약, S=안전, I=무관). 판정은 V 셋이 I 넷보다 전부 위인가.
DOCS: list[tuple[str, str, str]] = [
    ("V1", "V", """int parse_header(const uint8_t *pkt, size_t pkt_len) {
    char name[64];
    uint16_t nlen = (pkt[0] << 8) | pkt[1];
    memcpy(name, pkt + 2, nlen);            /* nlen from wire, no bound vs sizeof(name) */
    name[nlen] = '\\0';
    struct header h;
    h.version = pkt[2 + nlen];
    process_field(name);
    return nlen;
}"""),
    ("V2", "V", """void handle_request(int fd) {
    char line[128];
    char *raw = read_line(fd);             /* attacker-controlled length */
    strcpy(line, raw);                     /* no bound check -> stack overflow */
    if (strncmp(line, "GET ", 4) == 0)
        serve_path(line + 4);
    free(raw);
}"""),
    ("V3", "V", """void log_event(const char *user, const char *msg) {
    char buf[256];
    /* user and msg come straight from the request, unbounded */
    sprintf(buf, "user=%s action=%s ts=%ld", user, msg, time(NULL));
    write_log(buf);
}"""),
    ("S1", "S", """int parse_header_safe(const uint8_t *pkt, size_t pkt_len) {
    char name[64];
    uint16_t nlen = (pkt[0] << 8) | pkt[1];
    if (nlen >= sizeof(name) || (size_t)nlen + 2 > pkt_len)
        return -1;                         /* bounds enforced before copy */
    memcpy(name, pkt + 2, nlen);
    name[nlen] = '\\0';
    process_field(name);
    return nlen;
}"""),
    ("I1", "I", """long gcd(long a, long b) {
    while (b != 0) { long t = b; b = a % b; a = t; }
    return a < 0 ? -a : a;
}"""),
    ("I2", "I", """struct node *list_insert(struct node *head, int val) {
    struct node *n = malloc(sizeof(*n));
    n->val = val;
    n->next = head;
    return n;
}"""),
    ("I3", "I", """double dot(const double *a, const double *b, size_t n) {
    double s = 0.0;
    for (size_t i = 0; i < n; i++)
        s += a[i] * b[i];
    return s;
}"""),
    ("I4", "I", """int partition(int *arr, int lo, int hi) {
    int pivot = arr[hi], i = lo - 1;
    for (int j = lo; j < hi; j++)
        if (arr[j] < pivot) { i++; int t = arr[i]; arr[i] = arr[j]; arr[j] = t; }
    int t = arr[i + 1]; arr[i + 1] = arr[hi]; arr[hi] = t;
    return i + 1;
}"""),
]
LABELS = [d[0] for d in DOCS]
KIND = {d[0]: d[1] for d in DOCS}
TEXTS = [d[2] for d in DOCS]

QUERIES = {
    "Q1_realpath": "parse_header",
    "Q2_keywords": "parse_header memcpy len",
    "Q3_natural": "buffer overflow vulnerability in C string handling",
}

# Qwen3-Reranker 공식 instruction 틀(templated 모드). raw는 아무 것도 안 붙인다.
# ⚠ 이 포맷은 추정이다 — 서버가 무엇을 붙이는지의 확정은 vLLM 소스(D63 미확인②).
_INSTRUCT = (
    "Given a security-analysis query, retrieve code snippets that "
    "contain the vulnerability the query describes."
)
TOP_N = 5  # 리랭커가 실제로 캡슐에 넣는 개수(DEFAULT_TOP_N). production 관문은 이 렌즈다.


def _templated(query: str, doc: str) -> tuple[str, str]:
    q = f"<Instruct>: {_INSTRUCT}\n<Query>: {query}"
    d = f"<Document>: {doc}"
    return q, d


def _post(query: str, documents: list[str]) -> list[dict]:
    payload = {"model": MODEL, "query": query, "documents": documents,
               "top_n": len(documents)}
    req = urllib.request.Request(
        ENDPOINT, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=120) as r:  # noqa: S310
        return json.loads(r.read())["results"]


def _ranking(query: str, mode: str) -> list[tuple[str, float]]:
    """(label, score)를 점수 내림차순으로. mode=raw|templated."""
    if mode == "raw":
        q, docs = query, TEXTS
    else:
        q, docs = _templated(query, TEXTS[0])[0], [_templated(query, t)[1] for t in TEXTS]
    results = _post(q, docs)
    scored = [(LABELS[r["index"]], float(r["relevance_score"])) for r in results]
    scored.sort(key=lambda x: -x[1])
    return scored


def _verdict(ranking: list[tuple[str, float]]) -> bool:
    """전체-8 판정: 취약 셋(V)이 무관 넷(I)보다 전부 위인가. 안전(S)은 방해물이라 뺀다."""
    pos = {lab: i for i, (lab, _) in enumerate(ranking)}
    worst_v = max(pos[lab] for lab in LABELS if KIND[lab] == "V")
    best_i = min(pos[lab] for lab in LABELS if KIND[lab] == "I")
    return worst_v < best_i


def _top5_vuln(ranking: list[tuple[str, float]]) -> int:
    """production 렌즈: 리랭커가 캡슐에 넣는 top-N(=5)에 취약이 몇 건 드는가."""
    top = [lab for lab, _ in ranking[:TOP_N]]
    return sum(1 for lab in top if KIND[lab] == "V")


def run() -> None:
    vuln = [lab for lab in LABELS if KIND[lab] == "V"]
    safe = [lab for lab in LABELS if KIND[lab] == "S"]
    irr = [lab for lab in LABELS if KIND[lab] == "I"]
    print(f"모델={MODEL} 엔드포인트={ENDPOINT}")
    print(f"문서 8: 취약 {vuln} · 안전 {safe} · 무관 {irr}")
    summary = {}
    for qname, qtext in QUERIES.items():
        for mode in ("raw", "templated"):
            passes, top5, orders, det = 0, [], [], []
            for _ in range(REPS):
                try:
                    ranking = _ranking(qtext, mode)
                except Exception as exc:  # noqa: BLE001
                    print(f"\n[{qname}/{mode}] ERR {str(exc)[:160]}")
                    orders = None
                    break
                order = "".join(lab for lab, _ in ranking)
                orders.append(order)
                det.append(order)
                top5.append(_top5_vuln(ranking))
                if _verdict(ranking):
                    passes += 1
            if orders is None:
                summary[f"{qname}/{mode}"] = "ERR"
                continue
            deterministic = len(set(det)) == 1
            top5_med = int(statistics.median(top5))
            print(f"\n===== {qname} ({qtext!r}) · {mode} =====")
            for rep, order in enumerate(orders):
                steps = " > ".join(order[i:i + 2] for i in range(0, len(order), 2))
                print(f"  rep{rep + 1}: {steps}")
            det_ko = "예" if deterministic else "아니오"
            print(
                f"  → 전체-8 판정(V 셋 > I 넷) {passes}/{REPS} · "
                f"top-5 취약 {top5_med}/3 · 결정론={det_ko}"
            )
            summary[f"{qname}/{mode}"] = (
                f"전체-8 {passes}/{REPS} · top5 {top5_med}/3 "
                f"{'det' if deterministic else 'nondet'}"
            )

    print("\n========== 요약 ==========")
    for k, v in summary.items():
        print(f"  {k:26} {v}")
    print("\n열쇠: 배선 관문은 top-5(리랭커가 캡슐에 넣는 개수)다. 전체-8은 더 센 렌즈다.")
    print("templated≠raw면 client 틀이 순위를 바꾼다(서버가 무엇을 붙이는지는 vLLM 소스라야 확정).")


if __name__ == "__main__":
    run()
    sys.exit(0)
