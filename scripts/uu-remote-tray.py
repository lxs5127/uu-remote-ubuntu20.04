#!/usr/bin/env python3
"""Native host indicator for the UU service; never attach to Wine's display."""
import ctypes as C
import ctypes.util
import fcntl
import os
from pathlib import Path
import signal
import subprocess
import sys

import gi
gi.require_version('GLib', '2.0')
from gi.repository import GLib


class Tray:
    def __init__(self):
        # Use the stable public C API: Focal has the runtime library without
        # its optional GI typelib, so this needs no additional APT packages.
        self.gtk = C.CDLL('libgtk-3.so.0')
        self.gobject = C.CDLL('libgobject-2.0.so.0')
        library = (ctypes.util.find_library('appindicator3') or
                   ctypes.util.find_library('ayatana-appindicator3'))
        if not library:
            raise RuntimeError('An AppIndicator runtime library is required')
        self.indicators = C.CDLL(library)
        def bind(lib, name, arguments, result=None):
            function = getattr(lib, name)
            function.argtypes, function.restype = arguments, result
            return function
        ptr, string, integer = C.c_void_p, C.c_char_p, C.c_int
        self.init = bind(self.gtk, 'gtk_init_check', [ptr, ptr], integer)
        self.menu_new = bind(self.gtk, 'gtk_menu_new', [], ptr)
        self.item_new = bind(self.gtk, 'gtk_menu_item_new_with_label', [string], ptr)
        self.separator_new = bind(self.gtk, 'gtk_separator_menu_item_new', [], ptr)
        self.append = bind(self.gtk, 'gtk_menu_shell_append', [ptr, ptr])
        self.show_all = bind(self.gtk, 'gtk_widget_show_all', [ptr])
        self.sensitive = bind(self.gtk, 'gtk_widget_set_sensitive', [ptr, integer])
        self.label = bind(self.gtk, 'gtk_menu_item_set_label', [ptr, string])
        self.connect = bind(self.gobject, 'g_signal_connect_data',
                            [ptr, string, ptr, ptr, ptr, integer], C.c_ulong)
        self.new_indicator = bind(self.indicators, 'app_indicator_new', [string, string, integer], ptr)
        self.set_menu = bind(self.indicators, 'app_indicator_set_menu', [ptr, ptr])
        self.set_status = bind(self.indicators, 'app_indicator_set_status', [ptr, integer])
        self.set_title = bind(self.indicators, 'app_indicator_set_title', [ptr, string])
        self.set_secondary = bind(self.indicators, 'app_indicator_set_secondary_activate_target', [ptr, ptr])
        if not self.init(None, None):
            raise RuntimeError('The host graphical session is unavailable')
        self.callbacks, self.children = [], []
        self.loop = GLib.MainLoop()
        self.menu = self.menu_new()
        self.status_item = self.item('UU远程：检查状态中')
        self.sensitive(self.status_item, False)
        self.append(self.menu, self.separator_new())
        self.open_item = self.item('打开 UU远程', 'open')
        self.start_item = self.item('启动后台远控', 'start')
        self.stop_item = self.item('停止后台远控（断开连接）', 'stop')
        self.show_all(self.menu)
        icon = Path.home() / '.local/share/icons/hicolor/64x64/apps/024B_GameViewer.0.png'
        self.indicator = self.new_indicator(b'uu_remote_bridge',
            os.fsencode(icon) if icon.is_file() else b'network-server', 0)
        if not self.indicator:
            raise RuntimeError('The host indicator could not be created')
        self.set_title(self.indicator, 'UU远程'.encode())
        self.set_menu(self.indicator, self.menu)
        self.set_secondary(self.indicator, self.open_item)
        self.set_status(self.indicator, 1)  # APP_INDICATOR_STATUS_ACTIVE
        self.refresh()
        GLib.timeout_add_seconds(2, self.refresh)

    def item(self, title, action=None):
        widget = self.item_new(title.encode())
        self.append(self.menu, widget)
        if action is not None:
            callback_type = C.CFUNCTYPE(None, C.c_void_p, C.c_void_p)
            callback = callback_type(lambda *_: self.launch(action))
            self.callbacks.append(callback)  # Keep C callbacks alive.
            self.connect(widget, b'activate', C.cast(callback, C.c_void_p), None, None, 0)
        return widget

    def launch(self, action):
        command = ([str(Path.home() / '.local/bin/uu-remote'), 'open'] if action == 'open' else
                   ['/usr/bin/systemctl', '--user', action, 'uu-remote-bridge.service'])
        try:
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, close_fds=True)
            self.children.append((action, process))
        except OSError as failure:
            print('UU tray action failed: ' + str(failure), file=sys.stderr, flush=True)
        self.refresh()

    def refresh(self):
        self.children = [(action, child) for action, child in self.children if child.poll() is None]
        busy = any(action != 'open' for action, _ in self.children)
        try:
            status = subprocess.run(['/usr/bin/systemctl', '--user', 'is-active',
                'uu-remote-bridge.service'], capture_output=True, text=True, timeout=1)
            active = status.stdout.strip() == 'active'
            message = 'UU远程：后台运行' if active else 'UU远程：远控已停止'
        except (OSError, subprocess.TimeoutExpired):
            active, message = False, 'UU远程：状态暂不可用'
        self.label(self.status_item, message.encode())
        self.sensitive(self.open_item, active and not busy)
        self.sensitive(self.start_item, not active and not busy)
        self.sensitive(self.stop_item, active and not busy)
        return GLib.SOURCE_CONTINUE

    def run(self):
        for number in (signal.SIGTERM, signal.SIGINT):
            signal.signal(number, lambda *_: self.loop.quit())
        try:
            self.loop.run()
        finally:
            self.set_status(self.indicator, 0)


def main():
    os.umask(0o077)
    runtime = Path(os.environ.get('XDG_RUNTIME_DIR', f'/run/user/{os.getuid()}'))
    # GDM may import DISPLAY before XAUTHORITY into the user manager.
    authority = runtime / 'gdm/Xauthority'
    if not os.environ.get('XAUTHORITY') and authority.is_file():
        os.environ['XAUTHORITY'] = str(authority)
    descriptor = os.open(runtime / 'uu-remote-tray.lock',
                         os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(descriptor)
        return
    try:
        Tray().run()
    finally:
        os.close(descriptor)


if __name__ == '__main__':
    try:
        main()
    except (OSError, RuntimeError) as failure:
        print('UU tray: ' + str(failure), file=sys.stderr)
        sys.exit(1)
