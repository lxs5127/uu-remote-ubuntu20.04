"""Exercise VNC installation without a GNOME RDP server or relay toolchain."""
import os
from pathlib import Path
import subprocess
import shutil
import tempfile
import unittest
import importlib.util
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


class VncInstallerTests(unittest.TestCase):
    def test_registry_repair_uses_saved_private_wine_and_its_matching_server(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            config = home / '.config/uu-remote-bridge'
            config.mkdir(parents=True)
            wine = home / 'wine runtime/bin/wine'
            (config / 'environment').write_text('UURB_WINE_BIN=' + str(wine) + '\n')
            helpers = home / '.local/libexec'
            helpers.mkdir(parents=True)
            cleaner = helpers / 'uu-clean-wine-device-registry'
            cleaner.write_text('#!/bin/sh\nprintf "%s\\n" "$WINE_BIN" "$WINESERVER_BIN"\n')
            cleaner.chmod(0o755)
            environment = dict(os.environ, HOME=str(home))
            for name in ('WINE_BIN', 'WINESERVER_BIN', 'UURB_WINE_BIN'):
                environment.pop(name, None)
            result = subprocess.run(['bash', str(ROOT / 'scripts/uu-remote'), 'repair-registry'],
                                    env=environment, text=True, capture_output=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.splitlines(), [str(wine), str(wine.parent / 'wineserver')])

    @unittest.skipUnless(shutil.which("Xvfb"), "Xvfb required")
    def test_desktop_geometry_uses_the_entire_x11_root(self):
        from test_manager_capture import isolated_display
        source = (ROOT / "scripts/uu-remote-bridge").read_text()
        function = source[source.index("desktop_geometry() {"):
                          source.index("persist_relay_resolution() {")]
        with tempfile.TemporaryDirectory() as temporary:
            with isolated_display(Path(temporary) / "display", "3840x1080x24",
                                  compositor=False) as environment:
                result = subprocess.run(["bash", "-c", function + "\ndesktop_geometry"],
                                        env=dict(environment, desktop_display=environment["DISPLAY"],
                                                 desktop_xauthority=environment["XAUTHORITY"],
                                                 desktop_session_type="x11"),
                                        text=True, capture_output=True, timeout=5)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), "3840x1080")

    def test_supervisor_wait_reports_child_exit_on_host_bash(self):
        source = (ROOT / "scripts/uu-remote-bridge").read_text()
        block = source[source.index('exited_pid=""\n'):
                       source.index('log "A bridge process exited')]
        script = ('set +e\nbash -c "sleep 0.05; exit 7" &\n'
                  'critical_pids=("$!")\n' + block + '\nprintf "%s\\n" "$child_status"\n')
        result = subprocess.run(["bash", "-c", script],
                                text=True, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "7")

    @unittest.skipUnless(shutil.which("Xvfb"), "Xvfb required")
    def test_clipboard_receives_real_xfixes_selection_changes(self):
        from test_manager_capture import isolated_display
        from Xlib import X, display
        spec = importlib.util.spec_from_file_location(
            "ubuntu20_clipboard", ROOT / "scripts/uu-clipboard-native.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with isolated_display(root / "display", compositor=False) as environment:
                with patch.dict(os.environ, environment):
                    clipboard = module.Clipboard(root / "files", root / "status.json")
                    publisher = display.Display()
                    try:
                        owner = publisher.screen().root.create_window(
                            0, 0, 1, 1, 0, X.CopyFromParent)
                        owner.set_selection_owner(publisher.intern_atom("CLIPBOARD"), X.CurrentTime)
                        publisher.sync()
                        deadline = time.monotonic() + 3
                        while not clipboard.image_dirty and time.monotonic() < deadline:
                            while clipboard.d.pending_events():
                                clipboard.event(clipboard.d.next_event())
                            time.sleep(0.01)
                        self.assertTrue(clipboard.image_dirty,
                                        "XFixes did not report the real clipboard owner change")
                    finally:
                        publisher.close()
                        clipboard.d.close()

    @unittest.skipUnless(shutil.which("gcc"), "GCC required")
    def test_action_child_closes_inherited_descriptors_on_host_libc(self):
        # Exercise the real post-fork path without a desktop or a D-Bus change.
        harness = r'''
#define _GNU_SOURCE
#include <unistd.h>
#include <stdarg.h>
static int fixture_execl(const char *path, const char *argument, ...);
#define execl fixture_execl
#define main helper_main
#include "uu_x11_input.c"
#undef main
#undef execl
static int fixture_execl(const char *path, const char *argument, ...)
{
    (void)path;
    (void)argument;
    for (int fd = 3; fd < 256; ++fd)
        if (fcntl(fd, F_GETFD) != -1 || errno != EBADF)
            _exit(99);
    const char result[] = "(<true>,)\n";
    if (write(STDOUT_FILENO, result, sizeof(result) - 1) != sizeof(result) - 1)
        _exit(98);
    _exit(0);
}
int main(void)
{
    int fd = open("/dev/null", O_RDONLY);
    if (fd < 0 || fcntl(fd, F_DUPFD, 127) < 0)
        return 1;
    bool active = false;
    return overview_state(&active) && active ? 0 : 2;
}
'''
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "child.c"
            binary = Path(temporary) / "child"
            source.write_text(harness)
            subprocess.run(["gcc", "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror",
                            "-I", str(ROOT / "src"), str(source), "-o", str(binary), "-ldl"],
                           check=True, capture_output=True, timeout=30)
            subprocess.run([str(binary)], check=True, capture_output=True, timeout=5)

    def test_vnc_preflight_does_not_require_gnome_rdp_commands(self):
        source = (ROOT / "install.sh").read_text()
        block = source[source.index("desktop_commands=()"):
                       source.index('release_manifest="$(realpath')]
        script = r'''
set -Eeuo pipefail
grdctl_bin=/usr/bin/grdctl
openssl_bin=/usr/bin/openssl
python_bin=/usr/bin/python3
secret_tool_bin=/usr/bin/secret-tool
wine_bin=/opt/wine-stable/bin/wine
wineserver_bin=/opt/wine-stable/bin/wineserver
command() {
    case "$2" in
        /usr/bin/grdctl|/usr/libexec/gnome-remote-desktop-daemon) return 1 ;;
        *) return 0 ;;
    esac
}
''' + block
        for relay, expected in (("vnc", 0), ("rdp", 1)):
            with self.subTest(relay=relay):
                result = subprocess.run(["bash", "-c", script],
                                        env=dict(os.environ, desktop_relay=relay),
                                        capture_output=True, text=True, timeout=5)
                self.assertEqual(result.returncode, expected, result.stderr)

    def test_vnc_does_not_build_or_verify_unused_rdp_runtime(self):
        source = (ROOT / "install.sh").read_text()
        block = source[source.index('system_libei_version="$('):
                       source.index("bridge_was_active=false")]
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            scripts = directory / "scripts"
            scripts.mkdir()
            for name in ("build-compat.sh", "build-winpr.sh", "build-libei.sh",
                         "verify-freerdp-runtime.py"):
                path = scripts / name
                path.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "${0##*/}" >>"$calls"\n')
                path.chmod(0o755)
            log = directory / "calls"
            environment = dict(os.environ, repo_dir=str(directory),
                               calls=str(log), compat_build="compat-output",
                               freerdp_build="rdp-output", libei_build="libei-output",
                               python_bin="bash", saved_libei_mode="backport",
                               wine_tools_dir="/opt/wine-stable/bin",
                               UURB_LIBEI_MODE="backport")
            for relay, expected in (
                ("vnc", ["build-compat.sh"]),
                ("rdp", ["build-compat.sh", "build-winpr.sh", "build-libei.sh",
                         "verify-freerdp-runtime.py"]),
            ):
                with self.subTest(relay=relay):
                    log.write_text("")
                    result = subprocess.run(["bash", "-Eeuo", "pipefail", "-c", block],
                                            env=dict(environment, desktop_relay=relay),
                                            text=True, capture_output=True, timeout=5)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(log.read_text().splitlines(), expected)


if __name__ == "__main__":
    unittest.main()
