import argparse
from pathlib import Path

import pytest

from tm1814_led import installer as I


def args(**kw):
    base = dict(system=False, user=False, host="0.0.0.0", port=8080, controller_host=None, password=None,
                generate_password=False, dry_run=True, yes=True, purge=False)
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
def test_dry_run_install_changes_nothing_and_masks_password(os_name, capsys, monkeypatch, tmp_path):
    monkeypatch.setattr(I, "has_systemd", lambda: True)
    for var in ("HOME", "USERPROFILE", "LOCALAPPDATA", "XDG_DATA_HOME", "XDG_CONFIG_HOME"):
        monkeypatch.setenv(var, str(tmp_path))          # never look at (or touch) this machine's real install
    inst = I.Installer(args(password="topsecret"), os_name=os_name)
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
    inst = I.Installer(args(dry_run=False, generate_password=True), os_name="linux", runner=fake_run)
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


IFCONFIG = """lo0: flags=8049<UP,LOOPBACK,RUNNING,MULTICAST> mtu 16384
\tinet 127.0.0.1 netmask 0xff000000
en0: flags=8863<UP,BROADCAST,SMART,RUNNING,SIMPLEX,MULTICAST> mtu 1500
\tinet 192.168.100.163 netmask 0xffffff00 broadcast 192.168.100.255
utun12: flags=8051<UP,POINTOPOINT,RUNNING,MULTICAST> mtu 1280
\tinet 172.29.12.74 --> 172.29.12.74 netmask 0xffffffff
\tinet 169.254.1.2 netmask 0xffff0000
"""
IP_ADDR = "2: eth0    inet 192.168.1.20/24 brd 192.168.1.255 scope global eth0\n3: wg0    inet 10.8.0.2/32 scope global wg0\n"
IPCONFIG = "   IPv4 Address. . . . . . . . . . . : 192.168.1.77\n   Subnet Mask . . . . . . . . . . . : 255.255.255.0\n"


def test_parse_addresses_from_each_os_tool():
    assert I.parse_ipv4_addresses(IFCONFIG) == ["192.168.100.163", "172.29.12.74"]   # loopback and link-local skipped
    assert I.parse_ipv4_addresses(IP_ADDR) == ["192.168.1.20", "10.8.0.2"]
    assert I.parse_ipv4_addresses(IPCONFIG) == ["192.168.1.77"]


def test_vpn_address_never_wins_over_the_network_with_the_controller():
    ips = ["172.29.12.74", "192.168.100.163"]
    assert I.rank_addresses(ips) == ["192.168.100.163", "172.29.12.74"]            # 192.168 preferred
    assert I.rank_addresses(["10.8.0.2", "192.168.1.20"], "10.8.0.9")[0] == "10.8.0.2"   # but the controller's network wins
    assert I.rank_addresses(["8.8.8.8", "172.20.1.1", "10.0.0.5"]) == ["10.0.0.5", "172.20.1.1", "8.8.8.8"]
    assert I.rank_addresses(["172.29.12.74", "172.29.12.74"]) == ["172.29.12.74"]  # de-duplicated


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
    inst = I.Installer(args(dry_run=False, generate_password=True), os_name="linux", runner=fake_run)
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


IFCONFIG = """lo0: flags=8049<UP,LOOPBACK,RUNNING,MULTICAST> mtu 16384
\tinet 127.0.0.1 netmask 0xff000000
en0: flags=8863<UP,BROADCAST,SMART,RUNNING,SIMPLEX,MULTICAST> mtu 1500
\tinet 192.168.100.163 netmask 0xffffff00 broadcast 192.168.100.255
utun12: flags=8051<UP,POINTOPOINT,RUNNING,MULTICAST> mtu 1280
\tinet 172.29.12.74 --> 172.29.12.74 netmask 0xffffffff
\tinet 169.254.1.2 netmask 0xffff0000
"""
IP_ADDR = "2: eth0    inet 192.168.1.20/24 brd 192.168.1.255 scope global eth0\n3: wg0    inet 10.8.0.2/32 scope global wg0\n"
IPCONFIG = "   IPv4 Address. . . . . . . . . . . : 192.168.1.77\n   Subnet Mask . . . . . . . . . . . : 255.255.255.0\n"


def test_parse_addresses_from_each_os_tool():
    assert I.parse_ipv4_addresses(IFCONFIG) == ["192.168.100.163", "172.29.12.74"]   # loopback and link-local skipped
    assert I.parse_ipv4_addresses(IP_ADDR) == ["192.168.1.20", "10.8.0.2"]
    assert I.parse_ipv4_addresses(IPCONFIG) == ["192.168.1.77"]


def test_vpn_address_never_wins_over_the_network_with_the_controller():
    ips = ["172.29.12.74", "192.168.100.163"]
    assert I.rank_addresses(ips) == ["192.168.100.163", "172.29.12.74"]            # 192.168 preferred
    assert I.rank_addresses(["10.8.0.2", "192.168.1.20"], "10.8.0.9")[0] == "10.8.0.2"   # but the controller's network wins
    assert I.rank_addresses(["8.8.8.8", "172.20.1.1", "10.0.0.5"]) == ["10.0.0.5", "172.20.1.1", "8.8.8.8"]
    assert I.rank_addresses(["172.29.12.74", "172.29.12.74"]) == ["172.29.12.74"]  # de-duplicated


def test_reinstall_keeps_existing_password(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(I, "has_systemd", lambda: True)
    inst = I.Installer(args(no_auth=False), os_name="linux")
    env = tmp_path / "x.env"
    env.write_text("TM1814_PORT=8080\nTM1814_PASSWORD=keepme\n")
    inst.paths.env_file = env
    assert inst.password() == "keepme"


def test_a_password_is_always_set(tmp_path, monkeypatch):
    monkeypatch.setattr(I, "has_systemd", lambda: True)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False, raising=False)
    env = tmp_path / "x.env"

    inst = I.Installer(args(), os_name="linux")
    inst.paths.env_file = env
    pw = inst.password()                                  # nothing given, nothing existing, not interactive -> generated
    assert pw and inst.generated == pw and len(pw) >= 12

    env.write_text("TM1814_PASSWORD=keepme\n")
    inst2 = I.Installer(args(), os_name="linux")
    inst2.paths.env_file = env
    assert inst2.password() == "keepme" and inst2.generated is None      # a reinstall keeps the existing password

    inst3 = I.Installer(args(generate_password=True), os_name="linux")
    inst3.paths.env_file = env
    assert inst3.password() != "keepme"                                  # --generate-password replaces it
    assert I.Installer(args(password="explicit"), os_name="linux").password() == "explicit"


def test_there_is_no_way_to_install_without_a_password():
    assert "--no-auth" not in I.build_parser().format_help()


def test_cli_link_location_per_mode_and_os(monkeypatch):
    monkeypatch.setattr(I, "is_admin", lambda os_name: True)
    assert str(I.Installer(args(), os_name="linux").cli_link_path()) == "/usr/local/bin/tm1814-led"
    assert I.Installer(args(user=True), os_name="linux").cli_link_path().parts[-3:] == (".local", "bin", "tm1814-led")
    assert I.Installer(args(), os_name="macos").cli_link_path().parts[-3:] == (".local", "bin", "tm1814-led")
    assert I.Installer(args(), os_name="windows").cli_link_path() is None


def test_install_links_the_command_and_uninstall_removes_only_its_own_link(tmp_path, monkeypatch):
    class Done:
        returncode, stdout, stderr = 0, "", ""
    monkeypatch.setattr(I, "has_systemd", lambda: True)
    monkeypatch.setattr(I, "is_admin", lambda os_name: False)
    for var, sub in (("HOME", ""), ("XDG_DATA_HOME", "data"), ("XDG_CONFIG_HOME", "cfg")):
        monkeypatch.setenv(var, str(tmp_path / sub) if sub else str(tmp_path))
    monkeypatch.setattr(I.Path, "home", classmethod(lambda cls: tmp_path))
    inst = I.Installer(args(dry_run=False, generate_password=True), os_name="linux", runner=lambda c, **k: Done())
    inst.install()
    link = tmp_path / ".local" / "bin" / "tm1814-led"
    assert link.is_symlink() and str(link.resolve()) == str(inst.paths.exe.resolve()) or link.is_symlink()
    inst.a.yes = True
    inst.uninstall()
    assert not link.exists() and not link.is_symlink()
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to("/bin/ls")                              # someone else's command of the same name is left alone
    I.Installer(args(dry_run=False, yes=True), os_name="linux", runner=lambda c, **k: Done()).uninstall()
    assert link.is_symlink()
