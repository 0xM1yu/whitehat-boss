"""`tools/gt_guided/` 테스트의 공용 부품 — 모듈 적재, 실프로세스 차단, 가짜 vast 상자.

🔴 **이 도구는 돈이 걸린 경로다**(vast 인스턴스를 만들고 지운다). 그래서 이 테스트들은 실제
`ssh`·`scp`·`vastai`를 **한 번도 부르지 않고**, 그것을 `hermetic` 픽스처가 강제한다 — `subprocess`·
`urllib`을 막아 두고, 시도가 한 번이라도 있으면 그 테스트가 빨개진다. 가짜 상자(`FakeFleet`)는
`subprocess.run`을 **대신하는** 가짜이고 argv의 *모양*으로 호출을 가른다. 진짜 `ssh`·`vastai`가 그
모양을 받아들이는지는 여기서 알 수 없다(미실측).
"""

import builtins
import hashlib
import importlib.util
import io
import json
import os
import re
import shlex
import subprocess
import tarfile
import time
import urllib.request
from pathlib import Path
from types import ModuleType

import pytest

# GT_GUIDED_DIR는 고치기 전 판의 `tools/gt_guided/`(git archive로 푼 것)에 같은 테스트를 돌려 빨강을
# 확인하는 용도다 — 평소에는 저장소 것을 읽는다.
TOOL_DIR = Path(
    os.environ.get("GT_GUIDED_DIR")
    or Path(__file__).resolve().parent.parent / "tools" / "gt_guided"
)


def load(name: str) -> ModuleType:
    """`tools/gt_guided/<name>.py`를 새로 읽는다 — 패키지가 아니라 상자·PC에서 스크립트로 돈다.

    매번 새 모듈이라 모듈 수준 상태(`st` 등)가 테스트 사이에 새지 않는다."""
    spec = importlib.util.spec_from_file_location(f"gt_guided_{name}", TOOL_DIR / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def hermetic(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """어떤 테스트도 실제 프로세스·네트워크를 못 띄운다 — 띄우면 그 테스트가 빨개진다.

    막힌 시도는 기록해 두었다가 **끝에서** 단언한다: 도구가 `OSError`를 삼켜도(`ssh`가 없는 PC처럼)
    시도 자체는 남는다. HOME을 비워 사용자의 진짜 `~/.ft_gt_discord`(웹훅 주소)를 읽지 못하게 한다 —
    읽으면 테스트가 팀 채널에 글을 올린다."""
    spawned: list[object] = []

    def refuse(argv, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN202
        spawned.append(argv)
        raise OSError("hermetic suite - 실제 프로세스·네트워크를 띄우지 않는다")

    monkeypatch.setattr(subprocess, "run", refuse)
    monkeypatch.setattr(subprocess, "Popen", refuse)
    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    monkeypatch.setattr(time, "sleep", lambda seconds: None)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)
    yield
    assert spawned == [], f"테스트가 실제 프로세스·네트워크를 띄우려 했다: {spawned}"


@pytest.fixture
def cp949_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """encoding을 안 적은 텍스트 `open`이 사용자 PC처럼 cp949로 쓰고 읽게 한다.

    Windows 한국어 PC의 기본 코덱이다(리뷰가 직접 측정). 플랫폼과 무관하게 같은 결함을 재현하려고
    `open`의 기본값만 바꾼다 — `encoding="utf-8"`을 적은 호출은 건드리지 않는다."""
    real_open = io.open

    def opened(file, mode="r", buffering=-1, encoding=None, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN202
        if encoding in (None, "locale") and "b" not in str(mode):
            encoding = "cp949"
        return real_open(file, mode, buffering, encoding, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", opened)
    monkeypatch.setattr(io, "open", opened)


class FakeBoxError(BaseException):
    """가짜 상자가 모르는 명령을 받았다 — 도구의 `except Exception`이 못 삼키게 BaseException."""


class FakeBox:
    """vast 상자 하나의 `/root/gt` — 임시 디렉터리로 받친 가짜. `ssh`·`scp` 호출을 흉내 낸다."""

    def __init__(self, root: Path, ident: int, host: str, saved_pulled) -> None:  # noqa: ANN001
        self.id, self.host, self.root = ident, host, root
        for d in ("inbox", "running", "out"):
            (root / d).mkdir(parents=True, exist_ok=True)
        self.saved_pulled = saved_pulled  # 코디네이터가 디스크에 저장한 pulled 집합을 돌려준다
        self.down: str | None = None  # None | "timeout" | "refuse"
        self.jobs: dict[str, dict] = {}  # 받은 작업 JSON(utf-8로 읽어 낸 것)
        self.pushed: list[str] = []  # 받은 순서대로의 sid — 재배정이 몇 번 밀렸는지 센다
        self.deleted: list[tuple[list[str], set[str]]] = []  # (지운 sid들, 그때 저장돼 있던 pulled)
        self.ssh_calls: list[str] = []
        self.corrupt_downloads = 0  # 다음 N번의 내려받기를 한 바이트 깬다
        self.empty_sha = False  # tar는 성공하는데 sha256 출력이 비어 있다
        self.slow_download_s = 0.0  # 내려받기가 가짜 시계를 이만큼 민다
        self.clock = None  # `FakeClock`

    # ---- 작업자 쪽 흉내 ----
    def finish(self, sid: str, marker: str = "DONE", error: str | None = None) -> None:
        """작업자가 sid를 끝냈다 — inbox/running에서 빼고 out/<sid>/에 산출물과 표지를 둔다."""
        for d in ("inbox", "running"):
            (self.root / d / f"{sid}.json").unlink(missing_ok=True)
        od = self.root / "out" / sid
        od.mkdir(parents=True, exist_ok=True)
        build = {"sample_id": sid, "gt_in_source": 1}
        if error:
            build["error"] = error
        (od / "build.json").write_text(json.dumps(build), encoding="utf-8")
        (od / "auditor_inputs.jsonl.gz").write_bytes(b"x" * 64)
        (od / marker).write_text("1")

    def work(self, marker_of=lambda sid: "DONE", error_of=lambda sid: None) -> list[str]:  # noqa: ANN001
        """inbox에 있는 작업을 전부 끝낸다."""
        sids = sorted(p.stem for p in (self.root / "inbox").glob("*.json"))
        for sid in sids:
            self.finish(sid, marker_of(sid), error_of(sid))
        return sids

    def on_box(self, where: str) -> list[str]:
        return sorted(p.name.removesuffix(".json") for p in (self.root / where).iterdir())

    # ---- ssh ----
    def ssh(self, cmd: str) -> tuple[int, str]:
        self.ssh_calls.append(cmd)
        if "tar -xf jobs_in.tar" in cmd:
            return self._unpack_jobs()
        if "ls inbox | wc -l" in cmd:
            return 0, self._poll(old="ls out/*/DONE" in cmd)
        if "tar czf" in cmd:
            return self._tar(cmd)
        if "echo '#inbox'" in cmd:
            return 0, self._holdings()
        if "rm -f" in cmd or "rm -rf" in cmd:
            return self._remove(cmd)
        raise FakeBoxError(f"가짜 상자가 모르는 명령: {cmd!r}")

    def _poll(self, old: bool) -> str:
        """`old`는 표지가 DONE뿐이던 옛 코디네이터의 질의 모양(sid만, running 개수 한 줄 포함)."""
        inbox, running = len(self.on_box("inbox")), len(self.on_box("running"))
        counts = [str(inbox), str(running)] if old else [str(inbox)]
        marks = []
        for d in sorted((self.root / "out").iterdir()):
            for m in ("DONE", "FAILED", "RETRY"):
                if (d / m).exists() and (m == "DONE" or not old):
                    marks.append(d.name if old else f"out/{d.name}/{m}")
        return "\n".join([*counts, *marks]) + "\n"

    def _holdings(self) -> str:
        out = []
        for d in ("inbox", "running", "out"):
            out.append(f"#{d}")
            out += [p.name for p in sorted((self.root / d).iterdir())]
        return "\n".join(out) + "\n"

    def _unpack_jobs(self) -> tuple[int, str]:
        with tarfile.open(self.root / "jobs_in.tar") as tf:
            for m in tf.getmembers():
                raw = tf.extractfile(m).read()
                self.jobs[m.name.removesuffix(".json")] = json.loads(raw.decode("utf-8"))
                self.pushed.append(m.name.removesuffix(".json"))
                (self.root / "inbox" / m.name).write_bytes(raw)
        (self.root / "jobs_in.tar").unlink()
        return 0, ""

    def _tar(self, cmd: str) -> tuple[int, str]:
        m = re.search(r"tar czf /root/gt/(\S+) (.*?) && sha256sum", cmd)
        assert m, cmd
        name, sids = m[1], shlex.split(m[2])
        if any(not (self.root / "out" / s).is_dir() for s in sids):
            return 2, ""
        with tarfile.open(self.root / name, "w:gz") as tf:
            for s in sids:
                tf.add(self.root / "out" / s, arcname=s)
        if self.empty_sha:
            return 0, ""
        return 0, hashlib.sha256((self.root / name).read_bytes()).hexdigest() + "\n"

    def _remove(self, cmd: str) -> tuple[int, str]:
        for m in re.finditer(r"rm -f (\S+)", cmd):
            (self.root / m[1].removeprefix("/root/gt/")).unlink(missing_ok=True)
        m = re.search(r"rm -rf (.*)$", cmd)
        if m:
            sids = shlex.split(m[1])
            self.deleted.append((sids, set(self.saved_pulled())))
            for s in sids:
                _rmtree(self.root / "out" / s)
        return 0, ""

    # ---- scp ----
    def scp_up(self, local: str, remote: str) -> int:
        (self.root / remote.removeprefix("/root/gt/")).write_bytes(Path(local).read_bytes())
        return 0

    def scp_down(self, remote: str, local: str) -> int:
        src = self.root / remote.removeprefix("/root/gt/")
        if not src.is_file():
            return 1
        blob = bytearray(src.read_bytes())
        if self.corrupt_downloads:
            self.corrupt_downloads -= 1
            blob[-1] ^= 0xFF
        Path(local).write_bytes(bytes(blob))
        if self.clock:
            self.clock.now += self.slow_download_s
        return 0


def _rmtree(path: Path) -> None:
    import shutil

    shutil.rmtree(path, ignore_errors=True)


class FakeClock:
    """`time.time`을 대신한다 — `time.sleep`은 그만큼 앞으로 민다."""

    def __init__(self, start: float = 1_000_000.0) -> None:
        self.now = start

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(time, "time", lambda: self.now)
        monkeypatch.setattr(time, "sleep", lambda s: setattr(self, "now", self.now + s))


class FakeFleet:
    """`subprocess.run`을 대신해 ssh·scp·vastai 호출을 가짜 상자들에 나눠 준다."""

    def __init__(self, root: Path, hosts: dict[str, int], saved_pulled) -> None:  # noqa: ANN001
        self.boxes = {
            host: FakeBox(root / f"box{ident}", ident, host, saved_pulled)
            for host, ident in hosts.items()
        }
        self.vastai: list[list[str]] = []  # 불린 `vastai ...` argv (실행은 안 한다)
        self.vastai_rc = 0
        self.vastai_missing = False

    def box(self, ident: int) -> FakeBox:
        return next(b for b in self.boxes.values() if b.id == ident)

    def run(self, argv, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN202
        if argv[0] == "vastai":
            if self.vastai_missing:
                raise FileNotFoundError("vastai")
            self.vastai.append(list(argv))
            return subprocess.CompletedProcess(argv, self.vastai_rc, "destroying\n", "")
        target = next(a for a in argv if str(a).startswith("root@"))
        host = target.removeprefix("root@").split(":")[0]
        box = self.boxes[host]
        if box.down == "timeout":
            raise subprocess.TimeoutExpired(argv, kwargs.get("timeout"))
        if box.down == "refuse":
            return subprocess.CompletedProcess(argv, 255, "", "refused")
        text = bool(kwargs.get("text"))
        if argv[0] == "ssh":
            rc, out = box.ssh(argv[-1])
        elif argv[0] == "scp":
            src, dst = argv[-2], argv[-1]
            if str(dst).startswith("root@"):
                rc, out = box.scp_up(src, dst.split(":", 1)[1]), ""
            else:
                rc, out = box.scp_down(src.split(":", 1)[1], dst), ""
        else:
            raise FakeBoxError(f"가짜 상자가 모르는 프로그램: {argv!r}")
        return subprocess.CompletedProcess(
            argv, rc, out if text else out.encode(), "" if text else b""
        )
