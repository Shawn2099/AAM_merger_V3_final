"""Static and dry-run checks for the Windows deployment scripts (WS4).

Real NSSM, services and firewall rules are never touched: the installer runs in
-DryRun mode, and nssm.exe need not exist.
"""

from __future__ import annotations

import importlib.util
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
POWERSHELL = shutil.which("powershell.exe") or shutil.which("powershell")
needs_ps = pytest.mark.skipif(POWERSHELL is None, reason="powershell.exe not on PATH")


def _load_verify():
    spec = importlib.util.spec_from_file_location("_verify_backup", SCRIPTS / "_verify_backup.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_verify_backup"] = mod
    spec.loader.exec_module(mod)
    return mod


@needs_ps
@pytest.mark.parametrize("script", sorted(SCRIPTS.glob("*.ps1")), ids=lambda p: p.name)
def test_ps1_parses(script: Path) -> None:
    cmd = (
        "$e=$null; [void][System.Management.Automation.Language.Parser]::ParseFile("
        f"'{script}',[ref]$null,[ref]$e); "
        "if ($e -and $e.Count -gt 0) { $e | ForEach-Object { Write-Output $_.Message }; exit 1 }"
    )
    r = subprocess.run(
        [POWERSHELL, "-NoProfile", "-Command", cmd], capture_output=True, text=True, timeout=60
    )
    assert r.returncode == 0, r.stdout + r.stderr


# --- _verify_backup.py ----------------------------------------------------


def _make_db(path: Path, docs: list[tuple[str, str]], extra_audit: int = 0) -> None:
    con = sqlite3.connect(path)
    con.executescript(
        "CREATE TABLE documents (id INTEGER PRIMARY KEY, stored_path TEXT, sha256_hash TEXT);"
        "CREATE TABLE line_items (id INTEGER PRIMARY KEY);"
        "CREATE TABLE po_sets (id INTEGER PRIMARY KEY);"
        "CREATE TABLE audit_log (id INTEGER PRIMARY KEY);"
    )
    con.executemany("INSERT INTO documents (stored_path, sha256_hash) VALUES (?, ?)", docs)
    con.execute("INSERT INTO po_sets DEFAULT VALUES")
    for _ in range(1 + extra_audit):
        con.execute("INSERT INTO audit_log DEFAULT VALUES")
    con.commit()
    con.close()


def _docs(tmp_path: Path) -> list[tuple[str, str]]:
    import hashlib

    out = []
    for i in range(2):
        f = tmp_path / f"d{i}.pdf"
        f.write_bytes(b"%PDF-" + bytes([i]) * 50)
        out.append((str(f), hashlib.sha256(f.read_bytes()).hexdigest()))
    return out


def test_verify_backup_identical_passes(tmp_path: Path) -> None:
    v = _load_verify()
    docs = _docs(tmp_path)
    _make_db(tmp_path / "live.db", docs)
    _make_db(tmp_path / "bak.db", docs)
    checks = v.verify(tmp_path / "live.db", tmp_path / "bak.db")
    assert all(c.ok for c in checks), checks
    assert sum(c.name.startswith("file:") for c in checks) == 2
    assert (
        v.main(["--source", str(tmp_path / "live.db"), "--backup", str(tmp_path / "bak.db")]) == 0
    )


def test_verify_backup_missing_row_fails(tmp_path: Path) -> None:
    v = _load_verify()
    docs = _docs(tmp_path)
    _make_db(tmp_path / "live.db", docs, extra_audit=1)
    _make_db(tmp_path / "bak.db", docs)
    args = ["--source", str(tmp_path / "live.db"), "--backup", str(tmp_path / "bak.db")]
    assert v.main(args) == 1
    assert any(
        not c.ok and c.name == "rows:audit_log"
        for c in v.verify(tmp_path / "live.db", tmp_path / "bak.db")
    )
    assert v.main([*args, "--allow-source-newer"]) == 0


def test_verify_backup_bad_file_hash_fails(tmp_path: Path) -> None:
    v = _load_verify()
    docs = _docs(tmp_path)
    _make_db(tmp_path / "live.db", docs)
    _make_db(tmp_path / "bak.db", docs)
    Path(docs[0][0]).write_bytes(b"tampered")
    checks = v.verify(tmp_path / "live.db", tmp_path / "bak.db")
    assert any(not c.ok and c.name.startswith("file:") for c in checks)


# --- installer dry run ----------------------------------------------------


def _installer(
    tmp_path: Path, hosts: str, *extra: str, dry: bool = True, prefect: bool = True
) -> subprocess.CompletedProcess[str]:
    import re

    cfg = (ROOT / "config.example.yaml").read_text(encoding="utf-8")
    cfg = re.sub(r"(?m)^(\s*)allowed_hosts:.*$", rf"\1allowed_hosts: {hosts}", cfg)
    (tmp_path / "config.yaml").write_text(cfg, encoding="utf-8")
    args = [
        POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
        str(SCRIPTS / "install_nssm_services.ps1"),
        "-ConfigPath", str(tmp_path / "config.yaml"),
        "-PythonExe", sys.executable,
    ]  # fmt: skip
    if dry:
        args.append("-DryRun")
    if prefect:
        args.append("-WithPrefectServer")
    return subprocess.run(
        [*args, *extra], capture_output=True, text=True, timeout=120, cwd=tmp_path
    )


@needs_ps
def test_installer_dry_run_orders_web_after_prefect(tmp_path: Path) -> None:
    r = _installer(tmp_path, '["pc.local"]')
    out = r.stdout.replace("\r", "") + r.stderr
    assert r.returncode == 0, out
    install_prefect = out.index("nssm install AAMMerger-PrefectServer")
    dep_worker = out.index("nssm set AAMMerger-Worker DependOnService AAMMerger-PrefectServer")
    assert install_prefect < dep_worker
    # The web service must start even when Prefect fails at boot: it has no Prefect dependency.
    assert "nssm set AAMMerger-Web DependOnService" not in out
    assert "Dry run complete" in out
    assert "[DRY-RUN] ensure log directory" in out


@needs_ps
def test_installer_dry_run_has_no_side_effects(tmp_path: Path) -> None:
    r = _installer(tmp_path, '["pc.local"]')
    assert r.returncode == 0, r.stdout + r.stderr
    assert sorted(p.name for p in tmp_path.iterdir()) == ["config.yaml"]


@needs_ps
def test_installer_refuses_wildcard_hosts(tmp_path: Path) -> None:
    r = _installer(tmp_path, '["*"]')
    out = r.stdout + r.stderr
    assert r.returncode != 0, out
    assert "allowed_hosts" in out
    assert "Nothing was installed" in out
    assert "[DRY-RUN] nssm" not in out
    assert "Administrator" not in out  # the gate runs before the admin check
    assert sorted(p.name for p in tmp_path.iterdir()) == ["config.yaml"]


@needs_ps
def test_installer_allow_any_host_warns_and_continues(tmp_path: Path) -> None:
    r = _installer(tmp_path, '["*"]', "-AllowAnyHost")
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert "WARNING" in out and "-AllowAnyHost" in out


@needs_ps
def test_installer_real_run_refuses_foreign_config_path(tmp_path: Path) -> None:
    r = _installer(tmp_path, '["pc.local"]', dry=False)
    out = r.stdout + r.stderr
    assert r.returncode != 0, out
    assert "-ConfigPath" in out and "Nothing was installed" in out
    assert "Administrator" not in out


@needs_ps
def test_installer_without_prefect_server_has_no_prefect_steps(tmp_path: Path) -> None:
    r = _installer(tmp_path, '["pc.local"]', prefect=False)
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert "No service dependency (external Prefect)" in out
    assert "AAMMerger-PrefectServer" not in out.replace("No service dependency", "")
    assert "DependOnService" not in out


def test_setup_test_rig_declares_allow_any_host() -> None:
    text = (SCRIPTS / "setup_test_rig.ps1").read_text(encoding="utf-8")
    assert "[switch]$AllowAnyHost" in text
    assert '$installParams["AllowAnyHost"] = $true' in text
    assert "$LASTEXITCODE -ne 0" in text


def test_setup_test_rig_install_checks_are_in_place() -> None:
    text = (SCRIPTS / "setup_test_rig.ps1").read_text(encoding="utf-8")
    assert '-c "import pymupdf"' in text and "vc_redist.x64.exe" in text
    assert "Microsoft Store placeholder" in text
    assert "Get-NetConnectionProfile" in text
    assert "for ($i = 1; $i -le 120; $i++)" in text
    assert "Stop-Service -Name $svcName -Force" in text
    assert "Prefect work pool and the midnight sync were not registered" in text
    assert "If this PC gets a new IP address (DHCP)" in text


def test_nssm_download_failure_says_how_to_bundle_it() -> None:
    text = (SCRIPTS / "install_nssm_services.ps1").read_text(encoding="utf-8")
    assert "Download nssm-2.24.zip from https://nssm.cc/release/nssm-2.24.zip" in text
    assert "extract win64\\nssm.exe to" in text


def test_no_control_bytes_in_deployment_files() -> None:
    files = [
        ROOT / "docs" / "DELIVERY.md",
        *SCRIPTS.glob("*.ps1"),
        SCRIPTS / "_verify_backup.py",
        Path(__file__),
    ]
    bad = {
        f.name: sorted({b for b in f.read_bytes() if b < 32 and b not in (9, 10, 13)})
        for f in files
    }
    assert not {k: v for k, v in bad.items() if v}, bad
