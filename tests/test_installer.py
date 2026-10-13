import argparse
from pathlib import Path

import pytest

from tm1814_led import installer as I


def args(**kw):
    base = dict(system=False, user=False, host="0.0.0.0", port=8080, controller_host=None, password=None,
                generate_password=False, no_auth=True, dry_run=True, yes=True, purge=False)
    base.update(kw)
    return argparse.Namespace(**base)


def test_paths_per_os(tmp_path):
    sysp = I.build_paths("linux", "system")
    assert str(sysp.exe) == "/opt/tm1814-led/venv/bin/tm1814-led" and str(sysp.data_dir) == "/var/lib/tm1814-led"
    usr = I.build_paths("linux", "user", home=tmp_path, env={})
    assert usr.service_file == tmp_path / ".config/systemd/user/tm1814-led.service"
    mac = I.build_paths("macos", "user", home=tmp_path)
    assert mac.service_file.name == "com.jewettg.tm1814-led.plist" and "Application Support" in str(mac.venv)
    win = I.build_paths("windows", "user", home=tmp_path, env={"LOCALAPPDATA": str(tmp_path / "L")})
    assert win.exe.name == "tm1814-led.exe" and win.python.name == "pythonw.exe"
    with pytest.raises(I.InstallError):
        I.build_paths("plan9", "user")


def test_systemd_unit_system_and_user():
    unit = I.render_systemd_unit(I.build_paths("linux", "system"))
    assert "User=tm1814" in unit and "ProtectSystem=strict" in unit and "WantedBy=multi-user.target" in unit
    assert "ExecStart=/opt/tm1814-led/venv/bin/tm1814-led serve" in unit and "Restart=always" in unit
    user = I.render_systemd_unit(I.build_paths("linux", "user", home=Path("/home/pi"), env={}))
    assert "User=" not in user and "WantedBy=default.target" in user and "ProtectSystem" not in user


def test_launchd_plist_is_valid_xml():
    import plistlib
    p = I.build_paths("macos", "user", home=Path("/Users/x"))
    data = plistlib.loads(I.render_launchd_plist(p).encode())
    assert data["Label"] == I.LAUNCHD_LABEL and data["KeepAlive"] is True
    assert data["ProgramArguments"][1:3] == ["serve", "--env-file"]


def test_windows_task_command_and_cron_line(tmp_path):
    win = I.build_paths("windows", "user", home=tmp_path, env={"LOCALAPPDATA": str(tmp_path)})
    assert "ONLOGON" in I.windows_task_command(win, admin=False) and "SYSTEM" in I.windows_task_command(win, admin=True)
    assert "--env-file" in I.windows_task_command(win, admin=False)[5]
    assert I.cron_line(I.build_paths("linux", "user", home=Path("/home/pi"), env={})).startswith("@reboot ")


def test_env_file_contents():
    text = I.render_env_file(Path("/data"), "0.0.0.0", 8080, "pw", "10.0.0.5")
    assert "TM1814_PASSWORD=pw" in text and "TM1814_CONTROLLER_HOST=10.0.0.5" in text and "TM1814_DATA_DIR=/data" in text
    assert "PASSWORD" not in I.render_env_file(Path("/data"), "0.0.0.0", 8080, None, None)


@pytest.mark.parametrize("os_name", ["linux", "macos", "windows"])
def test_dry_run_install_changes_nothing_and_masks_password(os_name, capsys, monkeypatch):
    monkeypatch.setattr(I, "has_systemd", lambda: True)
    inst = I.Installer(args(password="topsecret", no_auth=False), os_name=os_name)
    inst.install()
    out = capsys.readouterr().out
    assert "DRY RUN" in out and "topsecret" not in out and "TM1814_PASSWORD=********" in out
    assert inst.paths.venv.exists() is False


def test_mode_selection(monkeypatch):
    monkeypatch.setattr(I, "is_admin", lambda os_name: False)
    assert I.Installer(args(), os_name="linux").mode == "user"
    monkeypatch.setattr(I, "is_admin", lambda os_name: True)
    assert I.Installer(args(), os_name="linux").mode == "system"
    assert I.Installer(args(user=True), os_name="linux").mode == "user"
    with pytest.raises(I.InstallError):
        I.Installer(args(system=True, user=True), os_name="linux")
    with pytest.raises(I.InstallError):
        I.Installer(args(system=True), os_name="macos")
    monkeypatch.setattr(I, "is_admin", lambda os_name: False)
    with pytest.raises(I.InstallError):
        I.Installer(args(system=True, dry_run=False), os_name="linux")


def test_install_runs_expected_commands_with_fake_runner(tmp_path, monkeypatch):
    calls = []

    class Done:
        returncode, stdout, stderr = 0, "", ""

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return Done()
    monkeypatch.setattr(I, "has_systemd", lambda: True)
    monkeypatch.setattr(I, "is_admin", lambda os_name: False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    inst = I.Installer(args(dry_run=False, no_auth=False, generate_password=True), os_name="linux", runner=fake_run)
    inst.install()
    flat = [" ".join(map(str, c)) for c in calls]
    assert any("-m venv" in c for c in flat) and any("pip install" in c for c in flat)
    assert any(c.startswith("systemctl --user enable --now tm1814-led.service") for c in flat)
    unit = (tmp_path / "cfg/systemd/user/tm1814-led.service").read_text()
    assert "ExecStart=" in unit
    env = (tmp_path / "cfg/tm1814-led/tm1814-led.env").read_text()
    assert f"TM1814_PASSWORD={inst.generated}" in env and oct((tmp_path / "cfg/tm1814-led/tm1814-led.env").stat().st_mode)[-3:] == "600"


def test_uninstall_dry_run_keeps_data(capsys, monkeypatch):
    monkeypatch.setattr(I, "has_systemd", lambda: True)
    I.Installer(args(), os_name="linux").uninstall()
    out = capsys.readouterr().out
    assert "disable --now" in out and "kept your data" in out
    I.Installer(args(purge=True), os_name="linux").uninstall()
    assert "remove" in capsys.readouterr().out
