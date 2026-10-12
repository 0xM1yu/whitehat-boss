#!/usr/bin/env python3
"""harness_final_v1 정답 위치로 취약점 슬라이스를 만든다 — 체크아웃 → 인덱스 → (서브셋) →
slicer_target(backward·forward) → attach → 기록.

    python run_gt.py --dataset ~/harness_final_v1 --out /work/out --work /work/cache \\
      --joern-bin /opt/joern/joern-cli/joern --limit 30

## 무엇을 쓰나

- **full_v1만** 쓴다(`notes.part == "full_v1"`). sample500은 심볼이 없어 건너뛴다.
- vuln: `locations.sink/source` + `notes.sink_label.symbols`.
- patch: `locations_in_fixed_revision.sink/source` + **짝 vuln의 심볼**(patch GT에는 심볼이 없다).
- root_cause는 슬라이서에 넘기지 않는다. 슬라이스에 담겼는지만 `gt_check`에 적는다.

## Joern에 무엇을 올리나 (`--scope`)

- `full` — 저장소 전체
- `subset` — `subset.py`가 고른 GT 함수 주변 파일(호출 그래프 `--hops`단 + include)
- `auto`(기본) — C/C++ 파일이 `--full-max-files` 이하면 full, 넘으면 subset

attach(원본 함수 붙이기)는 scope와 무관하게 **저장소 전체**와 전체 인덱스로 한다.

## Joern 서버

`--joern-bin`을 주면 표본마다 새 서버를 띄우고 끝나면 내린다(그래프가 쌓이지 않고, 한 표본의
폭주가 다음 표본으로 안 번진다). 안 주면 `--joern-endpoint`의 서버 하나를 계속 쓴다 — 표본마다
프로젝트를 닫고 지운다(`slicer_target.cleanup`).
"""

import argparse
import json
import os
import random
import signal
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import Request, urlopen

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "attach"))
sys.path.insert(0, str(HERE))

import subset  # noqa: E402
from export_code import export as export_code  # noqa: E402

TOLERANCE = 2


# --- 데이터셋 ------------------------------------------------------------------------------


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def load_sample(dataset: Path, kind: str, sample_id: str) -> dict:
    """표본 하나의 metadata·spec·root_cause. full_v1이 아니면 `skip` 사유를 든다."""
    base = dataset / "dataset" / kind / sample_id
    meta = read_json(base / "public" / "metadata.json")
    gt = read_json(base / "private_gt" / "ground_truth.json")
    notes = gt.get("notes") or {}
    # sample500_v1v3도 받는다 — 위치(sink·root_cause, 일부 source)가 있고, whole_slice(최신본 3,261개)에
    # 33개가 들어 있다. 위치가 없는 표본은 아래 role()에서 None이 된다
    if notes.get("part") not in ("full_v1", "sample500_v1v3"):
        return {"meta": meta, "skip": f"part={notes.get('part')}"}
    project = notes.get("project")
    # normal: the dataset has no private_gt for normals; the normal run writes one in the vuln layout
    # (pseudo sink/source picked inside the benign target function -- map_normal.py, 2026-10-04)
    if kind in ("vuln", "normal"):
        locations = gt["locations"]
        symbols = (notes.get("sink_label") or {}).get("symbols") or {}
    else:
        locations = gt["locations_in_fixed_revision"]
        vuln = read_json(
            dataset / "dataset" / "vuln" / gt["vuln_sample_id"] / "private_gt" / "ground_truth.json"
        )
        symbols = ((vuln.get("notes") or {}).get("sink_label") or {}).get("symbols") or {}
        project = project or vuln["notes"].get("project")

    def role(name: str) -> dict | None:
        loc = locations.get(name)
        if not loc:
            return None
        return {
            "file": loc["file"],
            "line": loc["line"],
            "line_end": loc.get("line_end") or loc["line"],
            "function": loc.get("function"),
            "symbol": symbols.get(name),
            **({"mapped": loc["mapped"]} if "mapped" in loc else {}),
            # 최신본 매핑이 찾은 GT 함수 범위(MAP-C2) — 슬라이서가 시작점을 그 안에서 고른다(MAP-C1)
            **({"function_span": loc["function_span"]} if loc.get("function_span") else {}),
        }

    return {
        "meta": meta,
        "spec": {"sink": role("sink"), "source": role("source")},
        "root_cause": role("root_cause"),
        "cwe": gt.get("cwe") or gt.get("fixed_cwe"),
        "project": project,
    }


def pick_samples(dataset: Path, args: argparse.Namespace) -> list[tuple[str, str]]:
    """(kind, sample_id) 목록. `--ids`가 있으면 그것, 없으면 full_v1 vuln에서 무작위 + 짝 patch."""
    if args.ids:
        rows = []
        for line in args.ids.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                kind, _, sample_id = line.partition("/") if "/" in line else ("vuln", "", line)
                rows.append((kind, sample_id))
        return rows
    pairs = [json.loads(line) for line in (dataset / "pairs.jsonl").read_text().splitlines()]
    full = []
    for pair in pairs:
        gt = read_json(
            dataset / "dataset" / "vuln" / pair["vuln_sample_id"] / "private_gt" /
            "ground_truth.json"
        )
        if (gt.get("notes") or {}).get("part") == "full_v1":
            full.append(pair)
    random.Random(args.random_seed).shuffle(full)
    rows = []
    for pair in full[: args.limit]:
        if "vuln" in args.kinds:
            rows.append(("vuln", pair["vuln_sample_id"]))
        if "patch" in args.kinds:
            rows.append(("patch", pair["patch_sample_id"]))
    return rows


# --- 저장소 · 인덱스 ----------------------------------------------------------------------


def run(cmd: list[str], cwd: Path | None = None, timeout: float | None = None) -> None:
    subprocess.run(cmd, cwd=cwd, check=True, timeout=timeout, stdout=subprocess.DEVNULL,
                   stderr=subprocess.PIPE)


def checkout(meta: dict, cache: Path) -> Path:
    """repo@commit — 커밋마다 한 번. 이미 있으면 HEAD를 확인하고 쓴다."""
    repo = cache / "repos" / meta["commit"]
    if (repo / ".git").is_dir():
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True,
                              text=True).stdout.strip()
        if head == meta["commit"]:
            return repo
    if repo.exists():
        subprocess.run(["rm", "-rf", str(repo)], check=True)
    repo.mkdir(parents=True)
    run(["git", "init", "-q"], repo)
    run(["git", "fetch", "-q", "--depth", "1", meta["repo_url"], meta["commit"]], repo, 3600)
    run(["git", "checkout", "-q", "--detach", "FETCH_HEAD"], repo, 600)
    return repo


def source_files(repo: Path, suffixes: set[str]) -> list[Path]:
    found = []
    for directory, dirs, files in os.walk(repo, followlinks=False):
        dirs[:] = [d for d in dirs if d != ".git" and not (Path(directory) / d).is_symlink()]
        for name in files:
            path = Path(directory) / name
            if path.suffix in suffixes and not path.is_symlink():
                found.append(path)
    return sorted(found)


def build_index(repo: Path, cache: Path, commit: str) -> Path:
    """tree-sitter 인덱스 — 커밋마다 한 번(`indexer.py`를 같은 프로세스에서 부른다)."""
    # 파일 이름의 꼬리는 인덱서 판 — 정의 클로저 표(var_types 등)가 없는 옛 캐시를 다시 쓰지 않게.
    out = cache / "index" / f"{commit}.closure1.json"
    if out.is_file():
        return out
    import indexer

    paths = source_files(repo, set(indexer._GRAMMARS))
    index = indexer.build_index(paths, repo)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(index, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    tmp.replace(out)
    return out


# --- Joern 서버 ----------------------------------------------------------------------------


def joern_ready(endpoint: str, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    body = json.dumps({"query": "1"}).encode()
    while time.monotonic() < deadline:
        try:
            request = Request(endpoint.rstrip("/") + "/query-sync", body,
                              {"Content-Type": "application/json"}, method="POST")
            with urlopen(request, timeout=10) as response:
                if json.loads(response.read()).get("success") in (True, "true"):
                    return True
        except Exception:
            pass
        time.sleep(2)
    return False


class JoernServer:
    """표본 하나 동안 사는 전용 서버. 프로세스 그룹째 내린다(`joern`은 JVM을 자식으로 띄운다)."""

    def __init__(self, binary: Path, port: int, heap: str, workdir: Path, log: Path):
        workdir.mkdir(parents=True, exist_ok=True)
        self.log = log.open("ab")
        self.process = subprocess.Popen(
            [str(binary), f"-J-Xmx{heap}", "-J-Xss64m", "--server", "--server-host",
             "127.0.0.1", "--server-port", str(port)],
            cwd=workdir, stdin=subprocess.DEVNULL, stdout=self.log, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        self.endpoint = f"http://127.0.0.1:{port}"

    def stop(self) -> None:
        """SIGTERM → 그룹이 10초 안에 비면 끝, 아니면 SIGKILL. 런처(sh)가 먼저 죽어도 JVM 자식이
        남을 수 있어 런처가 아니라 **그룹**이 비었는지 본다."""
        group = self.process.pid
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(group, sig)
            except ProcessLookupError:
                break
            deadline = time.monotonic() + 10
            empty = False
            while time.monotonic() < deadline:
                self.process.poll()  # 런처를 거둬야 좀비로 그룹에 남지 않는다
                try:
                    os.killpg(group, 0)
                except ProcessLookupError:
                    empty = True
                    break
                time.sleep(0.2)
            if empty:
                break
        self.log.close()


# --- 표본 하나 ----------------------------------------------------------------------------


def in_slice(
    points: set[tuple[str, int]], loc: dict | None, dependency: set[tuple[str, int]]
) -> dict | None:
    if not loc:
        return None
    lines = range(loc["line"], loc["line_end"] + 1)
    near = range(loc["line"] - TOLERANCE, loc["line_end"] + TOLERANCE + 1)
    return {
        "exact": any((loc["file"], n) in points for n in lines),
        f"within_{TOLERANCE}": any((loc["file"], n) in points for n in near),
        # 데이터 의존 줄만으로 — 문맥 보충 줄(감싼 제어문·return)이나 degraded의 GT 줄 복사는 안 센다.
        # `exact`는 문맥 줄까지 세어 데이터 의존이 없는 degraded(libtiff)도 참이었다(리뷰 2026-10-04 #9)
        "in_data_dependency": any((loc["file"], n) in dependency for n in lines),
    }


def gt_check(item: dict, sample: dict) -> dict:
    """정답 위치가 슬라이스에 담겼는지 — 기록만 한다(슬라이스를 고치지 않는다)."""
    points = {(p["file"], p["line"]) for p in item["lines"]}
    provenance = item.get("line_provenance") or {}
    dependency = {tuple(p) for p in provenance.get("data_dependency") or []}
    other = "source" if item["direction"] == "backward" else "sink"
    return {
        "root_cause_in_slice": in_slice(points, sample["root_cause"], dependency),
        f"{other}_in_slice": in_slice(points, sample["spec"][other], dependency),
        "target_reached": item["analysis"].get("target_reached"),
        "analysis_kind": item.get("analysis_kind"),
    }


def process(kind: str, sample_id: str, args: argparse.Namespace, graphs: dict) -> dict:
    from attach import attach_document
    from index_graph import GraphIndex
    from system import SystemHeaders

    if "system" not in graphs:
        graphs["system"] = None if args.no_system else SystemHeaders(args.compiler)

    started = time.monotonic()
    row: dict = {"kind": kind, "sample_id": sample_id}
    sample = load_sample(args.dataset, kind, sample_id)
    if "skip" in sample:
        return {**row, "status": "skipped", "error": sample["skip"]}
    meta = sample["meta"]
    row.update(project=sample["project"], commit=meta["commit"], cwe=sample["cwe"])
    if not sample["spec"]["sink"]:
        return {**row, "status": "skipped", "error": "no sink"}
    repo = checkout(meta, args.work)
    index_path = build_index(repo, args.work, meta["commit"])
    if meta["commit"] not in graphs:
        # 커밋별로 묶어 돌므로 인덱스는 하나만 들고 있는다(시스템 헤더 캐시는 남긴다)
        for key in [k for k in graphs if k != "system"]:
            del graphs[key]
        graphs[meta["commit"]] = (GraphIndex(index_path), read_json(index_path))
    graph, index = graphs[meta["commit"]]
    file_count = len(index.get("files", []))
    scope = args.scope
    if scope == "auto":
        scope = "full" if file_count <= args.full_max_files else "subset"
    joern_repo = repo
    subset_info = None
    if scope == "subset":
        seeds = [
            (s["file"], s.get("function"))
            for s in (sample["spec"]["sink"], sample["spec"]["source"])
            if s
        ]
        subset_info = subset.select_files(
            repo, index, seeds, hops=args.hops, max_files=args.subset_max_files
        )
        joern_repo = subset.materialize(
            repo, subset_info["files"], args.work / "subsets" / f"{kind}_{sample_id}"
        )
    sample_dir = args.out / kind
    sample_dir.mkdir(parents=True, exist_ok=True)
    spec_path = sample_dir / f"{sample_id}.spec.json"
    spec_path.write_text(json.dumps(sample["spec"], ensure_ascii=False, indent=2), "utf-8")
    raw_path = sample_dir / f"{sample_id}.slices.json"
    server = None
    endpoint = args.joern_endpoint
    try:
        if args.joern_bin:
            server = JoernServer(args.joern_bin, args.joern_port, args.joern_heap,
                                 args.work / "joern-workspace",
                                 args.out / "logs" / f"joern_{kind}_{sample_id}.log")
            endpoint = server.endpoint
            if not joern_ready(endpoint, args.joern_start_timeout):
                return {**row, "status": "failed", "error": "joern server did not start"}
        command = [
            sys.executable, str(HERE / "slicer" / "slicer_target.py"),
            "--repo", str(joern_repo), "--spec", str(spec_path), "--out", str(raw_path),
            "--joern-endpoint", endpoint,
            "--progress", str(args.out / "logs" / f"progress_{kind}_{sample_id}.jsonl"),
        ]
        if args.joern_bin:
            command += ["--joern-version-command", str(args.joern_bin)]
        for flag, value in (
            ("--backward-depth", args.depth),
            ("--backward-nodes", args.nodes),
            ("--query-timeout-seconds", args.query_timeout),
            ("--max-call-depth", args.max_call_depth),
            ("--max-ascent", args.max_ascent),
            ("--ascent-fanout", args.ascent_fanout),
        ):
            if value is not None:
                command += [flag, str(value)]
        try:
            subprocess.run(command, timeout=args.sample_timeout, check=False)
        except subprocess.TimeoutExpired:
            return {**row, "status": "failed", "error": f"sample timeout {args.sample_timeout}s"}
    finally:
        if server is not None:
            server.stop()
    if not raw_path.is_file():
        return {**row, "status": "failed", "error": "slicer wrote no result"}
    document = read_json(raw_path)
    # 취약점 분류(CWE) — 해제 상대 지점(release_site)을 해제 후 사용·이중 해제·누수·경쟁 조건에서만 켠다(검수
    # 2026-10-10 r7). 분류 레이블일 뿐 수정 줄·패치 내용은 아니다
    document.setdefault("target", {})["cwe"] = sample.get("cwe")
    attach_document(document, graph, repo, None, index=index, system=graphs["system"],
                    pov_options={})
    for item in document["slices"]:
        item["gt_check"] = gt_check(item, sample)
    document["sample"] = {
        "sample_id": sample_id,
        "kind": kind,
        "repo_url": meta["repo_url"],
        "commit": meta["commit"],
        "language": meta.get("language"),
        "joern_scope": scope,
        "repo_files_indexed": file_count,
        "subset": subset_info and {k: v for k, v in subset_info.items() if k != "files"}
        | {"file_count": len(subset_info["files"])},
    }
    out_path = sample_dir / f"{sample_id}.json"
    out_path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", "utf-8")
    # 방향별 세 파일 — v1 sliced/auditor-input과 입력 파서 문맥의 pov-input.
    code_files = export_code(document, sample_dir, sample_id)
    raw_path.unlink()
    row.update(
        status=document["status"],
        error=document.get("error"),
        scope=scope,
        joern_files=len(subset_info["files"]) if subset_info else file_count,
        seconds=round(time.monotonic() - started, 1),
        located=document.get("target", {}).get("located"),
        code_files=[path.name for path in code_files],
        slices={
            item["direction"]: {
                "lines": len(item["lines"]),
                "truncated": item["truncated"],
                "attached_chars": item["attached"]["stats"]["chars"],
                "pov_chars": item["pov_attached"]["stats"]["chars"],
                "definitions": {
                    k: v for k, v in item["attached"]["stats"]["definition_closure"].items()
                    if k != "system"
                },
                **item["gt_check"],
            }
            for item in document["slices"]
        },
    )
    return row


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--work", required=True, type=Path, help="체크아웃·인덱스·서브셋 캐시")
    parser.add_argument("--ids", type=Path, help="한 줄에 `vuln/<id>` 또는 `patch/<id>`")
    parser.add_argument("--limit", type=int, default=30, help="--ids가 없을 때 고를 vuln 짝 수")
    parser.add_argument("--random-seed", type=int, default=0)
    parser.add_argument("--kinds", default="vuln,patch")
    parser.add_argument("--scope", choices=("auto", "full", "subset"), default="auto")
    parser.add_argument("--full-max-files", type=int, default=3000)
    parser.add_argument("--hops", type=int, default=2)
    parser.add_argument("--subset-max-files", type=int, default=400)
    parser.add_argument("--joern-bin", type=Path, help="표본마다 새 서버를 띄운다")
    parser.add_argument("--joern-port", type=int, default=16242)
    parser.add_argument("--joern-heap", default="16g")
    parser.add_argument("--joern-start-timeout", type=float, default=300)
    parser.add_argument("--joern-endpoint", default="http://127.0.0.1:16242")
    parser.add_argument("--depth", type=int, default=30, help="방향마다 DDG 깊이 상한")
    parser.add_argument("--nodes", type=int, default=5000, help="방향마다 방문 상태 상한")
    # 피호출 안으로 내려가는 깊이(두 방향). 지정 안 하면 Joern 기본 4였다 — backward가 반환값을 따라 변환 함수
    # 수십 개의 내부까지 내려갔다(2026-10-10 4회차 leptonica `pixConvertTo32` 사슬, 1단계 852줄). 5회차부터 2
    parser.add_argument("--max-call-depth", type=int, default=2)
    parser.add_argument("--ascent-fanout", type=int, default=4,
                        help="backward: callers kept per no-stack ascent, 0 = all (2026-10-10 round 3, review S1-C7)")
    parser.add_argument("--max-ascent", type=int, default=2,
                        help="backward: caller levels climbed with no call stack (v21a: 2 -- 3 only added noise)")
    parser.add_argument("--query-timeout", type=float, default=1800)
    parser.add_argument("--sample-timeout", type=float, default=7200)
    parser.add_argument("--compiler", default="gcc", help="저장소 밖 상수·타입을 읽을 C 전처리기")
    parser.add_argument("--no-system", action="store_true", help="시스템 헤더 조회를 끈다")
    args = parser.parse_args(argv)
    args.dataset = args.dataset.expanduser().resolve()
    args.out = args.out.resolve()
    args.work = args.work.resolve()
    args.kinds = set(args.kinds.split(","))
    (args.out / "logs").mkdir(parents=True, exist_ok=True)
    samples = pick_samples(args.dataset, args)
    # 같은 커밋을 붙여 돈다 — 체크아웃·인덱스를 한 번만 만든다.
    commits = {}
    for kind, sample_id in samples:
        meta = read_json(args.dataset / "dataset" / kind / sample_id / "public" / "metadata.json")
        commits[(kind, sample_id)] = meta["commit"]
    samples.sort(key=lambda s: (commits[s], s))
    (args.out / "samples.txt").write_text(
        "".join(f"{k}/{s}\n" for k, s in samples), encoding="utf-8"
    )
    graphs: dict = {}
    summary = (args.out / "summary.jsonl").open("a", encoding="utf-8")
    failures = 0
    for number, (kind, sample_id) in enumerate(samples, 1):
        try:
            row = process(kind, sample_id, args, graphs)
        except Exception as exc:  # 표본 하나의 실패가 배치를 멈추지 않게
            import traceback
            row = {"kind": kind, "sample_id": sample_id, "status": "failed",
                   "error": f"{type(exc).__name__}: {exc}",
                   "traceback": traceback.format_exc()[-3000:]}
        failures += row["status"] == "failed"
        summary.write(json.dumps(row, ensure_ascii=False) + "\n")
        summary.flush()
        print(f"[{number}/{len(samples)}] {kind}/{sample_id} {row['status']} "
              f"{row.get('error') or ''}", flush=True)
    summary.close()
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
