"""`tools/cybergym_task.py` — 네트워크 없이(HTTP를 가짜로 바꾼다)."""

import hashlib
import http.client
import io
import json
import os
import subprocess
import sys
import urllib.error
from pathlib import Path

import pytest

from src.adapters.cybergym_oracle import checksum
from src.orchestrator.task_loader import check_task_dir, read_submit_metadata
from tools import cybergym_task as tool

ROOT = Path(__file__).resolve().parent.parent
REV = tool.DEFAULT_REVISION
# 정답 자료 — 트리 API는 이것들도 목록에 든다(실응답 그대로). 요청되면 안 된다.
ANSWER_FILES = ("error.txt", "patch.diff", "repo-fix.tar.gz")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class FakeHF:
    """`tool._open`을 대신한다 — 요청한 URL을 기록하고 트리·파일 바이트를 준다."""

    def __init__(self, family="arvo", number="1065"):
        self.contents = {"repo-vul.tar.gz": b"TARBALL" * 100, "description.txt": b"a bug"}
        self.prefix = f"data/{family}/{number}"
        self.requested: list[str] = []
        self.no_lfs: set[str] = set()  # 트리에서 `lfs` 키를 뺄 파일
        self.served: dict[str, bytes] = {}  # 파일 URL → 실제로 내줄 바이트(기본은 contents)
        self.tree_url = (
            f"https://huggingface.co/api/datasets/sunblaze-ucb/cybergym/tree/{REV}/{self.prefix}"
        )
        self.file_base = (
            f"https://huggingface.co/datasets/sunblaze-ucb/cybergym/resolve/{REV}/{self.prefix}"
        )

    def tree(self) -> bytes:
        names = [*self.contents, *ANSWER_FILES]
        entries = [
            {
                "type": "file",
                "path": f"{self.prefix}/{n}",
                "lfs": {
                    "oid": sha(self.contents.get(n, n.encode())),
                    "size": len(self.contents.get(n, n.encode())),
                },
            }
            for n in sorted(names)
        ]
        for entry in entries:
            if entry["path"].rsplit("/", 1)[-1] in self.no_lfs:
                del entry["lfs"]
        return json.dumps(entries).encode()

    def __call__(self, url: str):
        self.requested.append(url)
        if url == self.tree_url:
            return io.BytesIO(self.tree())
        name = url.rsplit("/", 1)[-1]
        if url == f"{self.file_base}/{name}" and name in self.contents:
            return io.BytesIO(self.served.get(url, self.contents[name]))
        raise AssertionError(f"예상 밖 URL: {url}")

    def file_requests(self) -> list[str]:
        return [u for u in self.requested if u != self.tree_url]


@pytest.fixture
def hf(monkeypatch):
    monkeypatch.setattr(tool, "RETRY_DELAY_S", 0)
    fake = FakeHF()
    monkeypatch.setattr(tool, "_open", fake)
    return fake


def run(*argv) -> int:
    return tool.main([*map(str, argv)])


def test_requests_only_the_two_files_and_the_tree(hf, tmp_path):
    assert run("arvo:1065", "--out", tmp_path / "t") == 0
    assert sorted(hf.requested) == sorted(
        [hf.tree_url, f"{hf.file_base}/repo-vul.tar.gz", f"{hf.file_base}/description.txt"]
    )
    assert not any(name in url for url in hf.requested for name in ANSWER_FILES)
    out = tmp_path / "t"
    assert sorted(p.name for p in out.iterdir()) == [
        "description.txt",
        "repo-vul.tar.gz",
        "submit.sh",
    ]
    assert (out / "repo-vul.tar.gz").read_bytes() == hf.contents["repo-vul.tar.gz"]
    check_task_dir(out)


def test_hash_mismatch_leaves_nothing(hf, tmp_path, capsys):
    hf.served[f"{hf.file_base}/repo-vul.tar.gz"] = b"X" * len(hf.contents["repo-vul.tar.gz"])
    assert run("arvo:1065", "--out", tmp_path / "t") == 1
    assert list((tmp_path / "t").iterdir()) == []  # 임시 폴더·부분 파일·submit.sh 모두 없다
    assert "실패" in capsys.readouterr().err


def test_foreign_file_in_out_is_rejected(hf, tmp_path):
    out = tmp_path / "t"
    out.mkdir()
    (out / "patch.diff").write_text("정답")
    assert run("arvo:1065", "--out", out) == 2
    assert hf.file_requests() == []
    assert [p.name for p in out.iterdir()] == ["patch.diff"]


def test_existing_data_file_with_other_hash_is_rejected(hf, tmp_path):
    out = tmp_path / "t"
    out.mkdir()
    (out / "description.txt").write_text("다른 내용")
    assert run("arvo:1065", "--out", out) == 2


def test_rerun_keeps_submit_sh_and_force_replaces_it(hf, tmp_path):
    out = tmp_path / "t"
    assert run("arvo:1065", "--out", out) == 0
    first = (out / "submit.sh").read_bytes()
    hf.requested.clear()
    assert run("arvo:1065", "--out", out) == 0
    assert (out / "submit.sh").read_bytes() == first
    assert hf.file_requests() == []  # 같은 번들이라 다시 받지 않는다
    assert run("arvo:1065", "--out", out, "--force-submit") == 0
    assert (out / "submit.sh").read_bytes() != first


def test_rerun_with_other_task_submit_sh_is_rejected(hf, tmp_path):
    out = tmp_path / "t"
    assert run("arvo:1065", "--out", out) == 0
    text = (out / "submit.sh").read_text().replace("arvo:1065", "arvo:9")
    (out / "submit.sh").write_text(text)
    assert run("arvo:1065", "--out", out) == 2


def test_submit_sh_is_readable_by_the_harness(hf, tmp_path, capsys):
    out = tmp_path / "t"
    assert run("arvo:1065", "--out", out, "--server", "http://10.0.0.5:8666/") == 0
    meta = read_submit_metadata(out)
    assert (meta.server, meta.task_id) == ("http://10.0.0.5:8666", "arvo:1065")
    assert json.loads((out / "submit.sh").read_text().split("metadata=")[1].split("' \\")[0])[
        "checksum"
    ] == checksum("arvo:1065", meta.agent_id)
    assert (out / "submit.sh").read_bytes().startswith(b"#!/bin/bash\n")
    assert b"\r" not in (out / "submit.sh").read_bytes()
    stdout = capsys.readouterr().out
    assert meta.agent_id in stdout and sha(hf.contents["description.txt"]) in stdout


def test_oss_fuzz_family_path(monkeypatch, tmp_path):
    monkeypatch.setattr(tool, "RETRY_DELAY_S", 0)
    fake = FakeHF("oss-fuzz", "368076871")
    monkeypatch.setattr(tool, "_open", fake)
    assert run("oss-fuzz:368076871", "--out", tmp_path / "t") == 0
    assert fake.requested and all("data/oss-fuzz/368076871" in u for u in fake.requested)


@pytest.mark.parametrize(
    "task",
    [
        "arvo",
        "arvo:",
        "arvo:12a",
        "arvo:../1",
        "foo:1",
        "oss-fuzz-latest:1",
        "arvo:1/../x",
        " arvo:1",
        "arvo:1065\n",
    ],
)
def test_bad_task_id_is_rejected_without_requests(hf, tmp_path, task):
    assert run(task, "--out", tmp_path / "t") == 2
    assert hf.requested == []


@pytest.mark.parametrize("flag", [["--revision", "main"], ["--server", "http://h/x y"]])
def test_bad_revision_or_server_is_rejected(hf, tmp_path, flag):
    assert run("arvo:1065", "--out", tmp_path / "t", *flag) == 2
    assert hf.requested == []


def test_transient_error_is_retried_but_404_is_not(hf, tmp_path, monkeypatch):
    real, calls = tool._open, []

    def flaky(url):
        calls.append(url)
        if len(calls) == 1:
            raise urllib.error.URLError("reset")
        return real(url)

    monkeypatch.setattr(tool, "_open", flaky)
    assert run("arvo:1065", "--out", tmp_path / "a") == 0

    def gone(url):
        calls.append(url)
        raise urllib.error.HTTPError(url, 404, "nf", {}, None)

    calls.clear()
    monkeypatch.setattr(tool, "_open", gone)
    assert run("arvo:1065", "--out", tmp_path / "b") == 1
    assert len(calls) == 1


VUL = "repo-vul.tar.gz"


def test_oversize_response_fails_on_first_attempt(hf, tmp_path, capsys):
    url = f"{hf.file_base}/{VUL}"
    hf.served[url] = hf.contents[VUL] + b"X" * (
        tool._CHUNK + 5
    )  # 청크 하나를 넘겨 읽는 도중 끊긴다
    out = tmp_path / "t"
    assert run("arvo:1065", "--out", out) == 1
    assert hf.requested.count(url) == 1  # 결정적 실패라 다시 걸지 않는다
    assert list(out.iterdir()) == []
    assert "넘었다" in capsys.readouterr().err


def test_same_size_wrong_bytes_is_not_retried(hf, tmp_path):
    url = f"{hf.file_base}/{VUL}"
    hf.served[url] = b"X" * len(hf.contents[VUL])
    assert run("arvo:1065", "--out", tmp_path / "t") == 1
    assert hf.requested.count(url) == 1


def test_second_file_failing_leaves_the_first_out_of_out(hf, tmp_path):
    hf.served[f"{hf.file_base}/description.txt"] = b"X" * len(hf.contents["description.txt"])
    out = tmp_path / "t"
    assert run("arvo:1065", "--out", out) == 1
    assert list(out.iterdir()) == []  # 첫 파일(repo-vul)은 검증을 통과했지만 옮기지 않았다


def test_second_move_failing_is_recovered_by_rerun(hf, tmp_path, monkeypatch):
    out = tmp_path / "t"
    real, calls = os.replace, []

    def flaky(src, dst):
        calls.append(dst)
        if len(calls) == 2:
            raise OSError("디스크 오류")
        real(src, dst)

    monkeypatch.setattr(tool.os, "replace", flaky)
    assert run("arvo:1065", "--out", out) == 1
    assert [p.name for p in out.iterdir()] == [VUL]  # 첫 파일만 옮겨졌고 임시 폴더는 지워졌다
    monkeypatch.setattr(tool.os, "replace", real)
    hf.requested.clear()
    assert run("arvo:1065", "--out", out) == 0
    assert hf.file_requests() == [f"{hf.file_base}/description.txt"]  # 첫 파일은 해시가 맞아 건너뜀
    assert sorted(p.name for p in out.iterdir()) == ["description.txt", VUL, "submit.sh"]


def test_rerun_with_other_server_is_rejected(hf, tmp_path, capsys):
    out = tmp_path / "t"
    assert run("arvo:1065", "--out", out) == 0
    first = (out / "submit.sh").read_bytes()
    hf.requested.clear()
    assert run("arvo:1065", "--out", out, "--server", "http://10.0.0.5:8666") == 2
    assert hf.requested == []  # 파일만 읽어 가르므로 트리 API도 부르지 않는다
    assert "--force-submit" in capsys.readouterr().err
    assert (out / "submit.sh").read_bytes() == first
    assert run("arvo:1065", "--out", out, "--server", "http://10.0.0.5:8666", "--force-submit") == 0
    assert read_submit_metadata(out).server == "http://10.0.0.5:8666"


def test_entry_without_lfs_is_refused_with_the_cause(hf, tmp_path, capsys):
    hf.no_lfs = {"description.txt"}
    assert run("arvo:1065", "--out", tmp_path / "t") == 1
    err = capsys.readouterr().err
    assert "LFS가 아니라 sha256 검증값이 없다" in err and "description.txt" in err
    assert hf.file_requests() == []


def test_unusable_out_is_an_input_problem(hf, tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    assert run("arvo:1065", "--out", blocker / "sub") == 2  # 부모가 파일이다
    assert run("arvo:1065", "--out", blocker) == 2


def test_leftover_part_dir_is_named_as_deletable(hf, tmp_path, capsys):
    out = tmp_path / "t"
    (out / ".part-abc").mkdir(parents=True)
    assert run("arvo:1065", "--out", out) == 2
    err = capsys.readouterr().err
    assert ".part-" in err and "지워도 된다" in err


class _BrokenBody:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, n=-1):
        raise http.client.IncompleteRead(b"par", 10)


@pytest.mark.parametrize("failures, code", [(1, 0), (tool.ATTEMPTS, 1)])
def test_incomplete_read_is_transient(hf, tmp_path, monkeypatch, failures, code):
    real, left = tool._open, [failures]

    def flaky(url):
        if url.endswith(VUL) and left[0] > 0:
            left[0] -= 1
            return _BrokenBody()
        return real(url)

    monkeypatch.setattr(tool, "_open", flaky)
    assert run("arvo:1065", "--out", tmp_path / "t") == code
    assert left[0] == 0


def test_script_runs_by_path_from_another_cwd(tmp_path):
    # #616 — 경로 실행은 sys.path[0]이 tools/라 `src` import가 깨졌다.
    # PYTHONPATH를 빼야 editable 설치·PYTHONPATH가 있는 환경에서도 이 성질을 문다.
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    done = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "cybergym_task.py"), "--help"],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert done.returncode == 0, done.stderr
    assert "arvo" in done.stdout


def test_default_revision_is_the_one_the_defect_note_cites():
    assert REV[:7] in (ROOT / "docs/open-questions/D/D97.md").read_text(encoding="utf-8")
