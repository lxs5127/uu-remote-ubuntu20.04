#!/usr/bin/env python3
"""Install and restore the UU terminal's owned Wine runtime files."""
from __future__ import annotations
import argparse
import hashlib
import os
from pathlib import Path
import shutil
import tempfile

VENDOR_CONPTY_SHA256 = "c46dcd04f52b97f6a8cf53e8f547c85a821660bed18de2b3344afcd4a8389ad6"
TRIAL_CONPTY_SHA256 = "22a8b0195e2dc6a4e12199b240be36a6a41232fb4c9a0eea23b116679f32f80d"
VENDOR_MUX_SHA256 = "ea50a845e3e614e87b57a98656ba9c391ed8c4cddc673647f9e2e9ac20a978d5"
# UU 4.43.1.3614 from the hash-pinned official installer recorded in its manifest.
VENDOR_MUX_4431_SHA256 = "d3eed5449ed64166c30b1fb7541630d0bd3f4c8f14eeaa99634eacd46534d5b4"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def same(left: Path, right: Path) -> bool:
    return left.is_file() and right.is_file() and digest(left) == digest(right)


def wine_builtin(path: Path) -> bool:
    return path.is_file() and b"Wine builtin DLL" in path.read_bytes()[:128]


def atomic_copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".uu-terminal-", dir=target.parent)
    os.close(descriptor)
    temporary = Path(name)
    try:
        shutil.copy2(source, temporary)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


class TerminalRuntime:
    def __init__(self, prefix: Path):
        self.prefix = prefix
        self.bin = prefix / "drive_c/Program Files/Netease/GameViewer/bin"
        self.compat = prefix / "compat"
        self.conpty = self.bin / "conpty.dll"
        self.original = self.bin / "conpty.dll.uu-original"
        self.managed = self.compat / "uu-conpty-compat.dll"
        self.mux = self.bin / "uuyc-mux.exe"
        self.mux_original = self.bin / "uuyc-mux.exe.uu-original"
        self.mux_managed = self.compat / "uuyc-mux.exe"
        self.mux_vendor = self.bin / "uu-terminal-vendor/uuyc-mux.exe"
        self.system = prefix / "drive_c/windows/system32/WindowsPowerShell/v1.0"
        self.shell = self.system / "powershell.exe"
        self.shell_original = self.system / "powershell.exe.uu-original"
        self.links = [(self.shell, self.bin / "powershell.exe"),
                      (self.system / "uu-terminal-bridge.runtime",
                       self.bin / "uu-terminal-bridge.runtime"),
                      (self.mux_vendor.parent / "conpty.dll", self.conpty)]

    @staticmethod
    def owned_link(path: Path, target: Path) -> bool:
        return path.is_symlink() and path.resolve() == target.resolve()

    def check_links(self) -> None:
        for path, target in self.links:
            if path.is_symlink():
                if not self.owned_link(path, target):
                    raise ValueError(f"Unmanaged terminal link: {path}")
            elif path.exists() and (path != self.shell or not wine_builtin(path)):
                raise ValueError(f"Unmanaged terminal system file: {path}")
        if self.shell_original.exists() and not wine_builtin(self.shell_original):
            raise ValueError("PowerShell backup is not a Wine builtin")

    def check_conpty(self) -> None:
        if self.original.exists() and digest(self.original) != VENDOR_CONPTY_SHA256:
            raise ValueError("ConPTY vendor backup differs from the audited build")
        if self.conpty.exists():
            current = digest(self.conpty)
            if current == VENDOR_CONPTY_SHA256:
                return
            if self.original.exists() and (same(self.conpty, self.managed) or
                                           current == TRIAL_CONPTY_SHA256):
                return
            raise ValueError("Unmanaged GameViewer conpty.dll")
        if not self.original.exists():
            raise ValueError("Missing audited vendor ConPTY and backup")

    def detach_system_links(self) -> None:
        """Restore the builtin before wineboot can follow an owned symlink."""
        self.check_links()
        for path, target in self.links:
            if self.owned_link(path, target):
                path.unlink()
                if path == self.shell and self.shell_original.exists():
                    atomic_copy(self.shell_original, self.shell)

    def check_mux(self) -> None:
        vendor_hashes = (VENDOR_MUX_SHA256, VENDOR_MUX_4431_SHA256)
        if self.mux_vendor.exists() and digest(self.mux_vendor) not in vendor_hashes:
            raise ValueError("Unmanaged terminal vendor MUX runner")
        if self.mux_original.exists() and digest(self.mux_original) not in vendor_hashes:
            raise ValueError("MUX vendor backup differs from the audited build")
        if self.mux.exists():
            if digest(self.mux) in vendor_hashes:
                return
            if self.mux_original.exists() and same(self.mux, self.mux_managed):
                return
            raise ValueError("Unmanaged GameViewer uuyc-mux.exe")
        if not self.mux_original.exists():
            raise ValueError("Missing audited vendor MUX and backup")

    def install(self, build: Path) -> None:
        self.check_conpty()
        self.check_links()
        self.check_mux()
        source = build / "uu-conpty-compat.dll"
        proxy = build / "uu-terminal-proxy.exe"
        if not source.is_file() or not proxy.is_file():
            raise ValueError("Missing built terminal compatibility DLL or proxy")
        if not self.original.exists():
            atomic_copy(self.conpty, self.original)
        if self.shell.exists() and not self.shell.is_symlink():
            atomic_copy(self.shell, self.shell_original)
        if not self.mux_original.exists():
            atomic_copy(self.mux, self.mux_original)
        atomic_copy(source, self.conpty)
        atomic_copy(source, self.managed)
        atomic_copy(proxy, self.mux)
        atomic_copy(proxy, self.mux_managed)
        atomic_copy(self.mux_original, self.mux_vendor)
        self.system.mkdir(parents=True, exist_ok=True)
        for path, target in self.links:
            if self.owned_link(path, target):
                continue
            path.unlink(missing_ok=True)
            path.symlink_to(target)

    def verify(self) -> None:
        self.check_conpty()
        self.check_links()
        self.check_mux()
        if not same(self.conpty, self.managed) or not self.original.is_file():
            raise ValueError("ConPTY compatibility DLL or vendor backup is missing/stale")
        if not same(self.mux, self.mux_managed) or not self.mux_original.is_file():
            raise ValueError("MUX recovery proxy or vendor backup is missing/stale")
        if not same(self.mux_vendor, self.mux_original):
            raise ValueError("MUX vendor runner is missing/stale")
        for path, target in self.links:
            if not self.owned_link(path, target):
                raise ValueError(f"Terminal system alias missing: {path}")

    def restore(self, dry_run: bool = False) -> None:
        self.check_links()
        if self.original.exists() or self.managed.exists():
            self.check_conpty()
        if self.mux_original.exists() or self.mux_managed.exists():
            self.check_mux()
        if dry_run:
            return
        self.detach_system_links()
        if self.original.exists():
            atomic_copy(self.original, self.conpty)
        if self.mux_original.exists():
            atomic_copy(self.mux_original, self.mux)
            self.mux_vendor.unlink(missing_ok=True)
            try:
                self.mux_vendor.parent.rmdir()
            except OSError:
                pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["install", "verify", "restore", "detach-system-links"])
    parser.add_argument("prefix", type=Path)
    parser.add_argument("--build", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    runtime = TerminalRuntime(args.prefix)
    try:
        if args.action == "install":
            if args.build is None:
                parser.error("install requires --build")
            runtime.install(args.build)
        elif args.action == "verify":
            runtime.verify()
        elif args.action == "restore":
            runtime.restore(args.dry_run)
        else:
            runtime.detach_system_links()
    except (OSError, ValueError) as error:
        parser.exit(1, f"terminal runtime: {error}\n")
    print(f"terminal-runtime={args.action} PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
