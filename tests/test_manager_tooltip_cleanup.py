"""Exercise tooltip dismissal on an owned X server, including unrelated popups."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from test_manager_capture import isolated_display

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('Xvfb'), 'Xvfb required')
class ManagerTooltipCleanupTests(unittest.TestCase):
    def test_cleanup_hides_only_the_lobby_process_non_input_tooltip(self):
        from Xlib import X, Xatom, Xutil, display
        text = (ROOT / 'scripts/uu-remote-console').read_text()
        helper = 'dismiss_manager_tooltips() {' + text.split(
            'dismiss_manager_tooltips() {', 1)[1].split('\ncleanup_window() {', 1)[0]
        with tempfile.TemporaryDirectory(prefix='uu-tooltip-test-') as temporary:
            with isolated_display(Path(temporary) / 'display', compositor=False) as environment:
                with patch.dict(os.environ, environment):
                    connection = display.Display()
                try:
                    screen = connection.screen()
                    visual = next(depth.visuals[0].visual_id for depth in screen.allowed_depths
                                  if depth.depth == 32)
                    colormap = screen.root.create_colormap(visual, X.AllocNone)

                    def window(*, pid=1234, title='GameViewer', depth=32, width=36,
                               height=16, override=True, accepts_input=False,
                               wm_class='gameviewer.exe', save=True):
                        options = dict(background_pixel=0, border_pixel=0,
                                       override_redirect=override, save_under=save)
                        if depth == 32:
                            options['colormap'] = colormap
                        result = screen.root.create_window(30, 40, width, height, 0,
                            depth, X.InputOutput, visual if depth == 32 else X.CopyFromParent,
                            **options)
                        result.set_wm_class(wm_class, wm_class)
                        result.set_wm_name('')
                        result.change_property(connection.intern_atom('_NET_WM_NAME'),
                            connection.intern_atom('UTF8_STRING'), 8, title.encode())
                        result.change_property(connection.intern_atom('_NET_WM_PID'),
                            Xatom.CARDINAL, 32, [pid])
                        result.change_property(connection.intern_atom('_NET_WM_WINDOW_TYPE'),
                            Xatom.ATOM, 32, [connection.intern_atom('_NET_WM_WINDOW_TYPE_NORMAL')])
                        result.set_wm_hints(flags=Xutil.InputHint, input=accepts_input)
                        result.map()
                        return result

                    main = window(title='网易UU远程', depth=24, width=640, height=480,
                                  override=False, accepts_input=True)
                    tooltip = window()
                    unrelated = [window(pid=5678), window(wm_class='other.exe'),
                        window(accepts_input=True), window(width=400), window(height=100),
                        window(override=False), window(title='Connection'),
                        window(depth=24), window(save=False)]
                    connection.sync()
                    script = helper + '\n' + '''
window_role="$TEST_ROLE"
client_window="$TEST_WINDOW"
bridge_display="$DISPLAY"
bridge_xauthority="$XAUTHORITY"
dismiss_manager_tooltips
'''
                    def invoke(role):
                        result = subprocess.run(['bash', '-c', script], env=dict(environment,
                            TEST_ROLE=role, TEST_WINDOW=str(main.id)),
                            capture_output=True, text=True, timeout=5)
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertEqual(result.stderr, '')
                        connection.sync()

                    invoke('controller')
                    self.assertEqual(tooltip.get_attributes().map_state, X.IsViewable)
                    invoke('manager')
                    self.assertEqual(tooltip.get_attributes().map_state, X.IsUnmapped)
                    for candidate in [main, *unrelated]:
                        self.assertEqual(candidate.get_attributes().map_state, X.IsViewable)
                    invoke('manager')  # Already dismissed remains safe.
                finally:
                    connection.close()
