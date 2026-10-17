"""Exercise manager hover through real TigerVNC without touching the user's X server."""
from contextlib import ExitStack
import os
from pathlib import Path
import shutil
import socket
import struct
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

from test_manager_capture import isolated_display

ROOT = Path(__file__).resolve().parents[1]


def await_ready(check, message):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.05)
    raise AssertionError(message)


def rfb_client(port):
    client = socket.create_connection(('127.0.0.1', port), timeout=3)

    def read(size):
        data = b''
        while len(data) < size:
            part = client.recv(size - len(data))
            if not part:
                raise AssertionError('VNC disconnected')
            data += part
        return data

    assert read(12).startswith(b'RFB 003.')
    client.sendall(b'RFB 003.008\n')
    assert 1 in read(read(1)[0])
    client.sendall(b'\x01')
    assert read(4) == b'\0' * 4
    client.sendall(b'\x01')
    header = read(24)
    length = struct.unpack('!I', header[20:])[0]
    assert length < 16384
    read(length)
    return client


@unittest.skipUnless(all(shutil.which(name) for name in ('Xvfb', 'x11vnc', 'vncviewer', 'openbox', 'gcc')),
                     'X11/VNC integration tools required')
class ManagerVncHoverTests(unittest.TestCase):
    def check_hover(self, fullscreen):
        from Xlib import X, Xatom, display

        with tempfile.TemporaryDirectory(prefix='uu-vnc-hover-') as temporary, ExitStack() as stack:
            directory = Path(temporary)
            physical = stack.enter_context(isolated_display(directory/'physical', compositor=False))
            private = stack.enter_context(isolated_display(directory/'private', compositor=False))
            with patch.dict(os.environ, physical):
                host = display.Display()
            stack.callback(host.close)
            with patch.dict(os.environ, private):
                gui = display.Display()
            stack.callback(gui.close)
            log = stack.enter_context((directory/'processes.log').open('wb'))

            def start(arguments, environment):
                process = subprocess.Popen(arguments, env=environment, stdout=log, stderr=log)
                def stop():
                    if process.poll() is None:
                        process.terminate()
                        try:
                            process.wait(timeout=2)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=2)
                stack.callback(stop)
                return process

            bridge = (ROOT/'scripts/uu-remote-bridge').read_text()
            config = directory/'openbox.xml'
            config.write_text(bridge.split('<<\'XML\'\n', 1)[1].split('\nXML', 1)[0])
            start(['openbox', '--debug', '--config-file', str(config)], private)
            portfile = directory/'desktop.port'
            start(['x11vnc', '-norc', '-display', physical['DISPLAY'], '-auth', physical['XAUTHORITY'],
                   '-autoport', '5900', '-rfbportv6', '-1', '-no6', '-listen', '127.0.0.1', '-nopw',
                   '-cursor', 'most', '-nocursorshape', '-nocursorpos',
                   '-wait', '5', '-defer', '0', '-setdefer', '0', '-nonap', '-sb', '0', '-nowireframe',
                   '-forever', '-shared', '-quiet', '-o', str(directory/'desktop.log'),
                   '-flag', str(portfile)], physical)
            # -flag records PORT=... after the listener binds.
            await_ready(lambda: portfile.exists() and 'PORT=' in portfile.read_text(), 'Desktop VNC did not start')
            port = int(portfile.read_text().strip().split('=')[-1])
            viewer_env = dict(private)
            if not fullscreen:
                guard = directory/'relay-guard.so'
                subprocess.run(['gcc', '-O2', '-Wall', '-Wextra', '-Werror', '-fPIC', '-shared',
                                str(ROOT/'src/uu_vnc_relay_guard.c'), '-o', str(guard), '-ldl', '-pthread'],
                               check=True, capture_output=True)
                viewer_env['LD_PRELOAD'] = str(guard)
            viewer = start(['vncviewer', '-FullScreen='+str(int(fullscreen)), '-FullscreenSystemKeys=0',
                            '-geometry', '800x600+0+0', '-RemoteResize=0', '-MenuKey=',
                            '-Shared=1', '-SecurityTypes=None', '127.0.0.1::'+str(port)], viewer_env)

            def relay_id():
                result = subprocess.run(['xdotool', 'search', '--onlyvisible', '--pid', str(viewer.pid),
                                         '--class', 'TigerVNC Viewer'], env=private, capture_output=True, text=True)
                return result.stdout.splitlines()
            await_ready(relay_id, 'TigerVNC did not map')
            relay = relay_id()[-1]
            subprocess.run(['xdotool', 'windowmove', relay, '0', '0', 'windowsize', relay, '800', '600'],
                           env=private, check=True, capture_output=True)
            window = gui.screen().root.create_window(100, 100, 400, 300, 0, gui.screen().root_depth,
                                                     X.InputOutput, X.CopyFromParent,
                                                     background_pixel=0x00ff00, event_mask=X.PointerMotionMask)
            window.set_wm_class('gameviewer.exe', 'gameviewer.exe')
            window.set_wm_name('UU Remote')
            window.change_property(gui.intern_atom('_NET_WM_PID'), Xatom.CARDINAL, 32, [os.getpid()])
            window.map()
            gui.sync()
            time.sleep(0.3)
            module = directory/'capture.so'
            source = ROOT/'src/uu_manager_capture.c'
            if fullscreen:
                # Negative control: restore the frame-only raise and immediate
                # injection used before the WM acknowledgement fix.
                text = source.read_text()
                begin = text.index('            if (isolated_manager && !keyboard) {')
                cursor = text.index('{', begin)
                depth = 1
                while depth:
                    cursor += 1
                    depth += (text[cursor] == '{') - (text[cursor] == '}')
                source = directory/'legacy-capture.c'
                source.write_text(text[:begin] + text[cursor+1:])
            subprocess.run(['gcc', '-O2', '-Wall', '-Wextra', '-Werror', '-fPIC', '-shared',
                            '-I', str(ROOT/'src'), str(source), '-o', str(module), '-ldl', '-pthread'],
                           check=True, capture_output=True)
            ready = directory/'capture.ready'
            manager_port = directory/'manager.port'
            capture_env = dict(private, LD_PRELOAD=str(module), UURB_MANAGER_WINDOW_ID=str(window.id),
                               UURB_MANAGER_INPUT_TRACE='1',
                               UURB_MANAGER_EXPECTED_DISPLAY=private['DISPLAY'], UURB_MANAGER_CAPTURE_ISOLATE_ROOT='1',
                               UURB_MANAGER_CAPTURE_LOG=str(directory/'capture.log'),
                               UURB_MANAGER_CAPTURE_READY_FILE=str(ready),
                               UURB_MANAGER_CAPTURE_NONCE='00000000-0000-4000-8000-000000000001')
            capture = start(['x11vnc', '-norc', '-nothreads', '-display', private['DISPLAY'], '-auth', private['XAUTHORITY'],
                   '-sid', str(window.id), '-autoport', str(port+1), '-rfbportv6', '-1', '-no6',
                   '-listen', '127.0.0.1', '-nopw', '-forever', '-shared', '-nosel', '-quiet',
                   '-flag', str(manager_port)], capture_env)
            try:
                await_ready(lambda: ready.exists() and manager_port.exists(), 'Manager capture did not start')
            except AssertionError as error:
                raise AssertionError(str(error) + '\n' + (directory/'processes.log').read_text()[-4000:] +
                                     '\n' + (directory/'capture.log').read_text()) from error
            client = rfb_client(int(manager_port.read_text().strip().split('=')[-1]))
            stack.callback(client.close)

            def point(connection):
                pointer = connection.screen().root.query_pointer()
                return pointer.root_x, pointer.root_y
            time.sleep(0.3)
            before = point(host)
            delivered = 0
            observed = []
            for x,y in ((30,40), (80,60), (140,90), (200,130), (250,180)):
                subprocess.run(['xdotool', 'windowactivate', '--sync', relay], env=private,
                               check=True, capture_output=True)
                client.sendall(struct.pack('!BBHH', 5, 0, x, y))
                time.sleep(0.2)
                if point(host) != before:
                    if not fullscreen:
                        raise AssertionError('Unexpected physical movement: '+str((before,point(host),point(gui)))+
                                             '\n'+(directory/'capture.log').read_text())
                    return True
                origin = gui.screen().root.translate_coords(window, 0, 0)
                delivered += point(gui) == (origin.x+x, origin.y+y)
                observed.append((point(gui), (origin.x+x, origin.y+y)))
            if not fullscreen:
                observed.append([hex(w.id) for w in gui.screen().root.query_tree().children])
                observed.append(('parent',hex(window.query_tree().parent.id), 'root child',
                                 hex(gui.screen().root.translate_coords(gui.screen().root,origin.x+250,origin.y+180).child.id)))
                observed.append(subprocess.run(['xprop', '-id', relay, '_NET_WM_STATE', '_NET_WM_WINDOW_TYPE'],
                                               env=private, capture_output=True, text=True).stdout)
                self.assertEqual(delivered, 5, str(observed) + '\n' + (directory/'capture.log').read_text() +
                                 '\n' + (directory/'processes.log').read_text()[:6000])
                self.assertEqual(gui.get_input_focus().focus.id, int(relay), 'Hover stole keyboard focus')
                # Genuine remote desktop motion must still pass through the
                # guarded viewer after management stops covering its pointer.
                from Xlib.ext import xtest
                subprocess.run(['xdotool', 'windowactivate', '--sync', relay], env=private,
                               check=True, capture_output=True)
                xtest.fake_input(gui, X.MotionNotify, x=650, y=450)
                gui.sync()
                await_ready(lambda: point(host) == (650,450), 'Desktop pointer input was disabled by the guard')
                # XGetImage excludes the X server cursor. A difference in the
                # private desktop pixels proves VNC burned it into the video.
                native_pixels = host.screen().root.get_image(640,440,32,32,X.ZPixmap,0xffffffff).data
                await_ready(lambda: gui.screen().root.get_image(640,440,32,32,X.ZPixmap,0xffffffff).data != native_pixels,
                            'Physical cursor was not visible in the relayed video')
                # Closing the local manager viewer ends Manual redirection.
                # Its last hover left the manager above the relay without
                # taking focus. The old focus-only supervisor does not act.
                client.sendall(struct.pack('!BBHH',5,0,30,40))
                await_ready(lambda: point(gui) == (origin.x+30,origin.y+40),'Closing hover did not reach manager')
                self.assertEqual(gui.get_input_focus().focus.id,int(relay))
                capture.terminate()
                try: capture.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    capture.kill(); capture.wait(timeout=2)
                sample_x, sample_y = origin.x+200, origin.y+150
                def manager_visible():
                    pixels = gui.screen().root.get_image(sample_x,sample_y,1,1,X.ZPixmap,0xffffffff).data
                    return int.from_bytes(pixels[:3],'little') == 0x00ff00
                await_ready(manager_visible,'Close regression did not expose the unredirected manager')
                old_supervisor = 'active=$(xdotool getactivewindow); if [[ "$active" != "$RELAY" ]]; then xdotool windowactivate "$RELAY"; fi'
                subprocess.run(['bash','-c',old_supervisor],env=dict(private,RELAY=relay),check=True,capture_output=True)
                time.sleep(.1)
                self.assertTrue(manager_visible(),'Focus-only negative control unexpectedly restored stacking')
                console = (ROOT/'scripts/uu-remote-console').read_text().split('case "${1:-open}" in',1)[0]
                console += '\ndiscover_bridge() { bridge_display="$DISPLAY"; bridge_xauthority="$XAUTHORITY"; }\nrelease_client\n'
                cleanup_env = dict(private,HOME=str(directory),XDG_RUNTIME_DIR=str(directory))
                result = subprocess.run(['bash','-c',console],env=cleanup_env,capture_output=True,text=True,timeout=5)
                self.assertEqual(result.returncode,0,result.stderr)
                relay_window = gui.create_resource_object('window',int(relay))
                await_ready(lambda: not manager_visible(),'Manager remained in remote video after closing its viewer\n'+
                            repr((relay_window.get_wm_name(),relay_window.get_wm_class()))+'\n'+result.stderr[-2200:])
                self.assertEqual(point(host),(650,450),'Closing management moved the physical pointer')
            return False

    def test_normal_relay_does_not_feed_manager_hover_back_to_desktop(self):
        self.assertFalse(self.check_hover(False))

    def test_fullscreen_negative_control_reproduces_pointer_feedback(self):
        self.assertTrue(self.check_hover(True))
