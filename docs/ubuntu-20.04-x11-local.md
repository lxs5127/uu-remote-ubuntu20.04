> Publication note (2026-10-17): the installed host uses Wine 11.0 and UU 4.43.1.3614. A phone has connected and controlled the desktop. The historical sections below include earlier stages and are not a claim that every issue is resolved. See [the consolidated adaptation and migration record](ubuntu-20.04-adaptation-zh.md) for capture, tooltip and native-tray changes, measured limits, and the unvalidated 22.04 status.

# Ubuntu 20.04 / X11 experimental local adaptation

## Reinstallation on 2026-10-17

The local port and its mouse, cursor, capture-cadence and manager-close fixes
were recovered from the previous patch records after removal. The pinned 4.42
CDN URL now requires download authorization. The official download API supplied
UU 4.43.1.3614 instead; `patches/uu-remote-4.43.1.3614.json` records the downloaded
installer and executable hashes. The Ubuntu 20.04 entry script selects this
local manifest by default. Explicit installer selection keeps a future rolling
API response from silently changing the installed release.

The two virtual-input settings signatures were unchanged and unique in 4.43.1,
at file offsets `0x5b1b52` and `0x5b1c04`. Full surrounding disassembly retained
the enabled-by-default settings read and the setter's flag store and semantic
logging. Other masked candidates were rejected. A disposable server copy passed
patch, hash verification and byte-identical restore. The installer and verifier
recognize the unchanged audited devcon identity and the structured-log release;
the terminal manager also accepts the exact new official MUX hash.

The 44 targeted Ubuntu 20.04, console-focus, manager/VNC and terminal-runtime
checks passed. Remote phone-controller behavior still needs validation after
the user signs in again. The removed account state was not recovered.

This local branch adapts the existing VNC/legacy-input backend for x86-64
Ubuntu 20.04. It is not an upstream-supported platform. The desktop must be
logged in using Xorg; Wayland is rejected by the entry script.

Run the dependency phase as the regular desktop user in a local terminal:

```bash
bash $HOME/uu-remote-ubuntu-plus/scripts/install-ubuntu20-x11.sh --packages-only
```

The dependency phase uses sudo for APT packages and WineHQ's Focal repository.
Enter the sudo password in the terminal. It does not install or launch UU yet.

Then install without repeating system package installation:

```bash
bash $HOME/uu-remote-ubuntu-plus/scripts/install-ubuntu20-x11.sh \
  --skip-packages --skip-account-login
uu-remote open
```

Sign in to UU in its management window, then connect from a separate UU client.

## Changes

- Use the existing X11/VNC relay with legacy input and direct X11 keyboard input.
- Skip GNOME RDP commands, settings, keyring credentials, FreeRDP runtime build
  and libei backport for VNC installations. RDP installations keep their build
  and runtime verification.
- Generate a private random console VNC password without a GNOME RDP prompt.
- Install WineHQ's development package for the winegcc launcher build.
- Provide a glibc 2.31 fallback for closing inherited file descriptors in the
  X11 helper's child, and remove a Python 3.9 string API from connection status.
- Enable MinGW C99 formatting, declare the Windows API versions required by
  SRW locks and ConPTY, and support Bash 5.0's `wait -n` without `-p`.
- Use TigerVNC 1.10-compatible arguments and x11vnc capture scaling. Follow
  the source root-window dimensions by default, including a combined dual-screen
  desktop, without changing the physical displays.
- Backport the XFixes selection notification wire structures within the
  clipboard helper for Focal's older Python Xlib, without modifying system Xlib.
- Create the tools directory before installing its first file on a fresh host.
- Pin the official 4.42 installer CDN URL; the rolling NetEase endpoint now
  returns a newer installer whose hash does not match the audited patches.
- Raise the privately redirected manager frame before injecting hover input,
  while preserving keyboard focus. This prevents the underlying fullscreen
  VNC relay from receiving manager motion and feeding it back to the physical
  mouse. The regression test raises the relay before every hover and checks
  that motion reaches only the manager, with keyboard focus still on the relay.
  TigerVNC uses a normal undecorated maximized window: Openbox also treats an
  exact screen-sized undecorated window as legacy fullscreen unless maximized.
  The manager requests WM restacking and checks the actual hit window before
  injecting motion, suppressing input if the manager cannot be brought above
  the relay. A relay-only preload module consumes X crossing events to prevent
  FLTK/TigerVNC from forwarding the old pointer position on cover/uncover.
  Two isolated Xvfb desktops with real Openbox, TigerVNC and x11vnc validate
  five delivered management hover events without physical-pointer changes,
  retained keyboard focus, and subsequent genuine desktop pointer delivery.
  A legacy-code/fullscreen negative control reproduces the original feedback.
- For VNC with the active direct X11 helper, inject the dedicated
  `uu-input-bridge-x11.dll`: all remote mouse/keyboard INPUTs go through the
  broker and authenticated physical-desktop helper, without a Wine foreground
  or VNC pointer hop. The GUI management sidecar keeps its separate input path.
  The owned x11vnc desktop server disables cursor-shape/position extensions so
  the real cursor is drawn into the relayed video, independently of Wine's
  hidden native-window cursor. The isolated mouse acceptance with
  `scripts/test-x11-mouse.sh --bridge-hook` exercises the actual IAT hook,
  absolute/relative motion, click and vertical/horizontal wheels. The VNC hover
  integration also checks that physical cursor pixels appear in the video.
- Keep the owned local VNC capture responsive with `-wait 5 -defer 0
  -setdefer 0 -nonap -sb 0`. Physical input now enters through X11 rather than
  the VNC socket, so the capture server must not rely on VNC input activity to
  exit idle throttling. A separate 1280x720/60 Hz synthetic drawing benchmark
  repeatedly delivered about 52 local RFB updates per second with these flags;
  that is not a measurement of phone video FPS or end-to-end latency. The same
  values can be applied live using authenticated X11 local server control.
- Discover the systemd cgroup hierarchy under `systemd`/`unified` as well as
  the cgroup-v2 root so `uu-agent` diagnostics work on Focal's hybrid setup.
- Restore the private desktop relay's stacking as well as its focus after
  the local manager closes. Closing its isolated capture removes Manual
  redirection, making the still-mapped Wine manager visible again. The native
  X11 mouse route controls the physical desktop, so this private window cannot
  be closed from the phone. The old relay finder passed two independent
  patterns to `xdotool search`, which accepts only one trailing pattern;
  `--name` was interpreted as an unknown command, silently skipping recovery.
  Search by class and separately filter each window's title. Raise the relay
  explicitly on release, and have the VNC supervisor restore stacking even
  when it already has focus. Skip that periodic raise while the isolated
  manager session is present so it cannot race management hover injection.
  The two-Xvfb/Openbox integration now closes the real capture, observes the
  green manager obscuring the relay, reproduces the old focus-only guard's
  inaction, runs the actual console release helper and verifies that the
  manager disappears from the remote desktop without physical cursor motion.
- Disable the owned capture server's window-drag wireframe heuristic with
  `-nowireframe`. Native X11 broker input is also monitored as local input by
  x11vnc, so clicking near window edges can pause video while it guesses
  whether a drag will follow. The isolated 1920x1080 benchmark in
  `build/bench-page-relay.py --click` paints at 60 Hz and injects XTest edge
  presses held for 300 ms. With the previously installed low-wait settings,
  it observed 41 local updates/s and a maximum update gap of 188 ms. Adding
  only `-nowireframe` observed 60 updates/s and a maximum gap of 25 ms.
  Disabling scroll-copy detection or bogging adaptation individually did not
  remove the pause. A shorter 50 ms press (`--click-short`) reproduced a
  104 ms maximum gap with the default heuristic and 24 ms with it disabled
  (55 versus 60 local updates/s). These are local relay measurements, not phone video FPS
  or proof that all remote stutter is resolved. Raw/Tight/ZRLE all sustained
  roughly 60 updates/s for separate large-area synthetic switches/fades;
  retain the existing raw encoding while fixing the demonstrated edge-click
  pause. The wireframe setting can be applied live over authenticated X11
  server control without restarting UU or reconnecting the phone.

The official UU installer hashes and binary-patch verification are retained.
The source desktop VNC listener remains bound to loopback as in the existing
backend. Remote access still goes through UU.

## Verification and remaining work

On Ubuntu 20.04.6, GCC 9, MinGW 9 and glibc 2.31 compile the Linux and Windows
compatibility components. The user installed the system dependencies, including
WineHQ stable 10.0 and wine-stable-dev. UU 4.42 and the adapted bridge are
installed in the dedicated user prefix; the enabled systemd user service runs.

`scripts/verify.sh --quick` has passed all its checks: current source digest,
audited patched binaries and backups, registry hygiene, VNC relay, input hooks,
broker, direct X11 keys, semantic text and clipboard helpers, source/canvas
geometry, native PTY, and managed terminal runtime. The service has remained
running without restarts after the Bash 5.0 fix. The source was observed as
3840x1080 with two displays and later 1920x1080; following preserves both cases.

Selected port, patch, host-action, terminal, clipboard, connection-status,
desktop-selection, console-focus and memory-budget tests have passed. The
new port tests include a real private-Xvfb selection-owner notification and
the actual Bash child-exit path.

Wine 10.0 and a private WineHQ 10.6 trial both left the GUI at its splash window.
Wine 11.0 was built locally and **resolved this startup problem**: the private
trial showed the complete 920x680 management window, device page and login
button. The installed bridge now uses that Wine 11 runtime and passes all quick
verification checks. `uu-remote open` opens its management viewer on the host
desktop. Account sign-in and remote-controller video/input still require the
user's account and a separate controller; they are **not yet validated**.

Wine source: `https://dl.winehq.org/wine/source/11.0/wine-11.0.tar.xz`, SHA-256
`c07a6857933c1fc60dff5448d79f39c92481c1e9db5aa628db9d0358446e0701`.
The source and build are under `build/wine11`; the installed runtime is
`~/.local/opt/uu-wine11`. GnuTLS headers were privately extracted from Focal's
`libgnutls28-dev`; the installed host GnuTLS library is used. Configure command:

```bash
env GNUTLS_CFLAGS="-I$PWD/../native/root/usr/include" \
  GNUTLS_LIBS="-L$PWD/../native/lib -lgnutls" \
  ../source/wine-11.0/configure \
  --prefix="$HOME/.local/opt/uu-wine11" --enable-win64 --with-mingw \
  --without-wayland --without-vulkan
make -j6
make install
```

Run those commands from `build/wine11/build64` after preparing the source and
private GnuTLS headers/library link. This build runs the 64-bit UU client and
server. It does not include 32-bit Wine support; bootstrap installation used
the system Wine 10.0. The 32-bit uninstaller, WebView setup and aria2 helper are
outside the verified runtime path. An installer upgrade requiring 32-bit
components needs a matching full Wine build or a separate bootstrap phase.

`UURB_WINE_BIN` selects the runtime and is persisted by `install.sh` in
`~/.config/uu-remote-bridge/environment`. The bridge, console, CLI, registry
repair, verifier and runtime refresher use this selection and matching tools;
`winlogon.exe.so` is rebuilt with the selected Wine's `winegcc`. To redeploy:

```bash
UURB_WINE_BIN="$HOME/.local/opt/uu-wine11/bin/wine" \
  bash scripts/install-ubuntu20-x11.sh --skip-packages --skip-account-login
```

System Wine is retained. Before the switch, the stopped main prefix and its
configuration were backed up to `build/wine11/main-prefix-backup` and
`build/wine11/environment.before-wine11`. Shell aliases were detached before
Wine's prefix update, then reinstalled by the bridge installer.

The VNC path does not use the SDL/RDP-specific live display-mode flow; canvas
changes may reconnect the bridge. Successful backend checks alone do not prove
a working authenticated remote session.

Worktree branch: `compat/ubuntu-20.04-x11`.
