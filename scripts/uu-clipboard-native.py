#!/usr/bin/python3
"""Authenticated bitmap/file clipboard adapter. X11/XWayland selection owner."""
import argparse
import hashlib
import hmac
import io
import json
import os
from pathlib import Path
import queue
import select
import shutil
import signal
import socket
import struct
import subprocess
import tempfile
import threading
import time

from PIL import Image
from Xlib import X, Xatom, display, protocol
from Xlib.ext import xfixes
from Xlib.protocol import rq


# Ubuntu 20.04's python-xlib implements XFixes cursor requests only. Supply
# the XFixes selection wire structures locally without changing system Xlib.
class CompatSelectionNotify(rq.Event):
    _code = None
    _fields = rq.Struct(rq.Card8("type"), rq.Card8("sub_code"),
                        rq.Card16("sequence_number"), rq.Window("window"),
                        rq.Window("owner"), rq.Card32("selection"),
                        rq.Card32("timestamp"), rq.Card32("selection_timestamp"),
                        rq.Pad(8))


class CompatSelectionOwnerNotify(CompatSelectionNotify):
    pass


class CompatSelectSelectionInput(rq.Request):
    _request = rq.Struct(rq.Card8("opcode"), rq.Opcode(2), rq.RequestLength(),
                         rq.Window("window"), rq.Card32("selection"), rq.Card32("mask"))


SelectionNotify = getattr(xfixes, "SelectionNotify", CompatSelectionNotify)


def watch_selection(connection, window, selection):
    connection.xfixes_query_version()
    if hasattr(connection, "xfixes_select_selection_input"):
        connection.xfixes_select_selection_input(window, selection, 7)
    else:
        info = connection.query_extension("XFIXES")
        if not info.present:
            raise RuntimeError("XFixes selection notifications are unavailable")
        # All three subtypes share this layout. Register the entire event;
        # old python-xlib's subevent decoder uses Python 2's ord(bytes[index]).
        connection.extension_add_event(info.first_event, CompatSelectionOwnerNotify)
        CompatSelectSelectionInput(display=connection.display,
                                   opcode=connection.display.get_extension_major("XFIXES"),
                                   window=window, selection=selection, mask=7)

MAGIC = 0x43425555
MAX_BYTES = 64 * 1024 * 1024
MAX_PIXELS = 16 * 1024 * 1024
TEXT, PNG, DIB, GET_IMAGE, FILE, GET_HOST = range(6)
GET_HOST_IMAGE = 10
FILE_BEGIN, FILE_CHUNK, FILE_END, FILE_ABORT = range(6, 10)
MAX_FILES, MAX_BATCH = 64, 256 * 1024 * 1024
Image.MAX_IMAGE_PIXELS = MAX_PIXELS


def png_image(data):
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        raise ValueError("invalid PNG")
    width, height = struct.unpack_from(">II", data, 16)
    if width * height > MAX_PIXELS:
        raise ValueError("image too large")
    image = Image.open(io.BytesIO(data))
    if image.format != "PNG" or image.width * image.height > MAX_PIXELS:
        raise ValueError("unsupported image")
    return image.convert("RGBA")


def dib_image(data):
    if len(data) < 40:
        raise ValueError("short DIB")
    header, width, height, planes, bits, compression = struct.unpack_from("<IiiHHI", data)
    if header not in (40, 108, 124) or planes != 1 or width <= 0 or height == 0:
        raise ValueError("unsupported DIB header")
    if bits not in (24, 32) or compression not in (0, 3):
        raise ValueError("only uncompressed RGB24/BGRA32 supported")
    if width * abs(height) > MAX_PIXELS:
        raise ValueError("image too large")
    offset = header
    alpha = False
    if compression == 3:
        masks = struct.unpack_from("<III", data, 40)
        if masks != (0xFF0000, 0xFF00, 0xFF):
            raise ValueError("unsupported bitmap masks")
        if header == 40:
            offset += 12
        else:
            alpha = struct.unpack_from("<I", data, 52)[0] == 0xFF000000
    stride = ((width * bits + 31) // 32) * 4
    if offset + stride * abs(height) > len(data):
        raise ValueError("truncated pixels")
    image = Image.frombytes("RGBA" if bits == 32 else "RGB", (width, abs(height)),
                            data[offset:offset + stride * abs(height)],
                            "raw", "BGRA" if bits == 32 else "BGR",
                            stride, -1 if height > 0 else 1).convert("RGBA")
    if not alpha:
        image.putalpha(255)
    return image


def image_dib(image):
    header = bytearray(124)
    struct.pack_into("<IiiHHII", header, 0, 124, image.width, -image.height,
                     1, 32, 3, image.width * image.height * 4)
    struct.pack_into("<IIIII", header, 40, 0xFF0000, 0xFF00, 0xFF,
                     0xFF000000, 0x73524742)
    return bytes(header) + image.tobytes("raw", "BGRA")


def stage_file(packet, root):
    if len(packet) < 4:
        raise ValueError("short file packet")
    length, = struct.unpack_from("<I", packet)
    if not 0 < length <= 1024 or 4 + length > len(packet):
        raise ValueError("invalid filename")
    name = packet[4:4 + length].decode("utf-8")
    if name in (".", "..") or any(c in name for c in "/\\\0") or len(name.encode()) > 255:
        raise ValueError("invalid basename")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    folder = Path(tempfile.mkdtemp(prefix="copy-", dir=root))
    path = folder / name
    try:
        with path.open("xb") as output:
            os.chmod(path, 0o600)
            output.write(packet[4 + length:])
            output.flush()
            os.fsync(output.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        folder.rmdir()
        raise
    return path


def file_manifest(data):
    count, = struct.unpack_from("<I", data)
    if not 1 <= count <= MAX_FILES:
        raise ValueError("a copy supports 1 to 64 files")
    files, offset = [], 4
    for _ in range(count):
        length, size = struct.unpack_from("<Iq", data, offset)
        offset += 12
        if not 1 <= length <= 255 or offset + length > len(data):
            raise ValueError("invalid filename")
        name = data[offset:offset + length].decode("utf-8")
        offset += length
        if name in (".", "..") or any(c in name for c in "/\\\0"):
            raise ValueError("invalid filename")
        if not -1 <= size <= MAX_BYTES:
            raise ValueError("each file is limited to 64 MiB")
        files.append({"name": name, "size": None if size == -1 else size})
    if offset != len(data) or sum(f["size"] or 0 for f in files) > MAX_BATCH:
        raise ValueError("a copy is limited to 256 MiB")
    return files


class FileProgress:
    """Non-focusing GTK window; counters come only from received file bytes."""
    def __init__(self):
        self.window = None
        self.expires = None
        self.dismissed = False
        self.unknown_bars = []
        self.next_pulse = 0
        self.Gtk = None
        if os.environ.get("UURB_CLIPBOARD_PROGRESS", "1") == "0":
            return
        import gi
        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk
        if Gtk.init_check()[0]:
            self.Gtk = Gtk

    def update(self, data, new=False):
        if new:
            self.dismissed = False
            self.expires = None
        if not self.Gtk or self.dismissed:
            return
        Gtk = self.Gtk
        if self.window is None:
            self.window = Gtk.Window(title="UU 文件接收")
            self.window.set_accept_focus(False)
            self.window.set_focus_on_map(False)
            self.window.set_default_size(390, 150)
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            box.set_border_width(14)
            self.window.add(box)
            self.name, self.detail = Gtk.Label(xalign=0), Gtk.Label(xalign=0)
            self.name.set_line_wrap(True)
            self.detail.set_line_wrap(True)
            self.file_bar, self.total_bar = Gtk.ProgressBar(), Gtk.ProgressBar()
            self.file_bar.set_show_text(True)
            self.total_bar.set_show_text(True)
            for widget in (self.name, self.file_bar, self.total_bar, self.detail):
                box.pack_start(widget, False, False, 0)
            self.window.connect("destroy", self.closed)
            self.window.show_all()
        self.name.set_text(f'{data["file_index"]}/{data["file_count"]} · {data["filename"]}')
        self.unknown_bars = []
        for bar, received, total in (
            (self.file_bar, data["received"], data["total"]),
            (self.total_bar, data["bytes_received"], data["bytes_total"])
        ):
            if total is None:
                bar.pulse()
                if data["state"] == "receiving":
                    self.unknown_bars.append(bar)
                bar.set_text(f"已接收 {received:,} 字节 · 总量未知")
            else:
                bar.set_fraction(min(1, received / total) if total else 1)
                percent = min(100, received * 100 / total) if total else 100
                bar.set_text(f"{received:,} / {total:,} 字节 · {percent:.1f}%")
        state = data["state"]
        self.detail.set_text("全部接收完成，可以粘贴" if state == "completed" else
                             "接收失败：" + data.get("error", "") if state == "failed" else
                             "正在接收，完成后即可粘贴")
        if state == "completed":
            self.expires = time.monotonic() + 5

    def closed(self, *_):
        self.window = None
        self.dismissed = True

    def tick(self):
        if self.window and self.expires and time.monotonic() >= self.expires:
            self.window.destroy()
        if self.Gtk:
            if self.window and time.monotonic() >= self.next_pulse:
                for bar in self.unknown_bars:
                    bar.pulse()
                self.next_pulse = time.monotonic() + 0.1
            while self.Gtk.events_pending():
                self.Gtk.main_iteration_do(False)


class FileTransfer:
    def __init__(self, files, root):
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.folder = Path(tempfile.mkdtemp(prefix="copy-", dir=root))
        self.files = files
        self.paths, used = [], set()
        for entry in files:
            path = Path(entry["name"])
            name, index = path.name, 2
            while name in used:
                name = f"{path.stem} ({index}){path.suffix}"
                index += 1
            if len(name.encode("utf-8")) > 255:
                shutil.rmtree(self.folder)
                raise ValueError("duplicate filename exceeds basename limit")
            used.add(name)
            self.paths.append(self.folder / name)
        self.index, self.received, self.bytes_received = 0, 0, 0
        self.total = sum(f["size"] for f in files) if all(f["size"] is not None for f in files) else None
        self.state, self.error = "receiving", None
        self.output = self.paths[0].open("xb")
        self.digest = hashlib.sha256()
        self.hashes = []

    def snapshot(self):
        entry = self.files[self.index]
        total = entry["size"]
        return {"kind": "file", "state": self.state, "filename": entry["name"],
                "file_index": self.index + 1, "file_count": len(self.files),
                "received": self.received, "total": total,
                "percent": (None if total is None else min(100, self.received * 100 / total) if total else 100),
                "bytes_received": self.bytes_received, "bytes_total": self.total,
                "total_percent": (None if self.total is None else min(100, self.bytes_received * 100 / self.total) if self.total else 100),
                "error": self.error}

    def chunk(self, data):
        index, = struct.unpack_from("<I", data)
        block = data[4:]
        if index != self.index or not 0 < len(block) <= 65536:
            raise ValueError("invalid file chunk")
        received = self.received + len(block)
        size = self.files[index]["size"]
        if received > MAX_BYTES or self.bytes_received + len(block) > MAX_BATCH:
            raise ValueError("file exceeds 64 MiB or copy exceeds 256 MiB")
        if size is not None and received > size:
            raise ValueError("file data exceeds declared length")
        self.output.write(block)
        self.digest.update(block)
        self.received = received
        self.bytes_received += len(block)

    def end(self, data):
        if len(data) != 4 or struct.unpack("<I", data)[0] != self.index:
            raise ValueError("invalid file completion")
        size = self.files[self.index]["size"]
        if size is not None and size != self.received:
            raise ValueError("file ended before declared length")
        self.output.flush()
        os.fsync(self.output.fileno())
        self.output.close()
        self.files[self.index]["size"] = self.received
        self.hashes.append(self.digest.hexdigest())
        if self.index + 1 == len(self.files):
            self.total = self.bytes_received
            self.state = "completed"
            return True
        self.index += 1
        self.received = 0
        self.digest = hashlib.sha256()
        self.output = self.paths[self.index].open("xb")
        return False

    def abort(self, reason):
        self.output.close()
        shutil.rmtree(self.folder)
        self.state, self.error = "failed", reason


class Clipboard:
    def __init__(self, staging, status_file):
        self.d = display.Display()
        self.window = self.d.screen().root.create_window(
            0, 0, 1, 1, 0, X.CopyFromParent, X.InputOutput, X.CopyFromParent)
        self.clip = self.d.intern_atom("CLIPBOARD")
        self.targets = self.d.intern_atom("TARGETS")
        self.incr = self.d.intern_atom("INCR")
        self.values = {}
        self.transfers = {}
        self.staging = staging
        self.status_file = status_file
        self.image_digest = None
        self.text_digest = None
        self.image_dirty = False
        self.file_transfer = None
        self.file_progress = None
        self.progress_file = status_file.with_name("clipboard-transfer.json")
        self.last_progress = 0
        watch_selection(self.d, self.window, self.clip)
        self.d.flush()

    def record(self, data):
        self.status_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = self.status_file.with_suffix(".tmp")
        temporary.write_text(json.dumps({"timestamp": time.time(), **data}) + "\n")
        temporary.replace(self.status_file)

    def publish(self, values, primary=False):
        atoms = {self.d.intern_atom(k): v for k, v in values.items()}
        self.values[self.clip] = atoms
        self.window.set_selection_owner(self.clip, X.CurrentTime)
        if primary:
            self.values[Xatom.PRIMARY] = atoms
            self.window.set_selection_owner(Xatom.PRIMARY, X.CurrentTime)
        self.d.sync()
        if self.d.get_selection_owner(self.clip) != self.window:
            raise RuntimeError("clipboard owner was not acquired")

    def file_update(self, force=False, new=False):
        transfer = self.file_transfer
        if not transfer or (not force and time.monotonic() - self.last_progress < 0.1):
            return
        data = {"timestamp": time.time(), **transfer.snapshot()}
        self.progress_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = self.progress_file.with_suffix(".tmp")
        temporary.write_text(json.dumps(data) + "\n")
        temporary.replace(self.progress_file)
        if self.file_progress is None:
            self.file_progress = FileProgress()
        self.file_progress.update(data, new)
        self.last_progress = time.monotonic()

    def file_abort(self, reason):
        if self.file_transfer:
            self.file_transfer.abort(reason)
            self.file_update(force=True)
            self.file_transfer = None
        else:
            data = {"timestamp": time.time(), "kind": "file", "state": "failed",
                    "filename": "文件接收", "file_index": 0, "file_count": 0,
                    "received": 0, "total": None, "percent": None,
                    "bytes_received": 0, "bytes_total": None, "total_percent": None,
                    "error": reason}
            self.progress_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            self.progress_file.write_text(json.dumps(data) + "\n")
            if self.file_progress is None:
                self.file_progress = FileProgress()
            self.file_progress.update(data, new=True)

    def operation(self, kind, data):
        if kind == TEXT:
            text = data.decode("utf-8")
            if "\0" in text or not text or len(data) > 4194304:
                raise ValueError("invalid text")
            self.publish({"UTF8_STRING": data, "text/plain;charset=utf-8": data}, True)
            self.image_digest = None
            self.text_digest = hashlib.sha256(data).digest()
            self.record({"kind": "text", "direction": "controller-to-native"})
        elif kind in (PNG, DIB):
            image = png_image(data) if kind == PNG else dib_image(data)
            output = io.BytesIO()
            image.save(output, format="PNG")
            self.image_digest = hashlib.sha256(image.tobytes()).digest()
            self.text_digest = None
            self.publish({"image/png": output.getvalue()})
            self.record({"kind": "image", "direction": "controller-to-native",
                         "width": image.width, "height": image.height,
                         "pixel_rgb_sha256": hashlib.sha256(image.convert("RGB").tobytes()).hexdigest()})
        elif kind == FILE:
            path = stage_file(data, self.staging)
            uri = path.as_uri().encode()
            self.publish({"text/uri-list": uri + b"\r\n",
                          "x-special/gnome-copied-files": b"copy\n" + uri + b"\n"})
            self.image_digest = None
            self.text_digest = None
            self.record({"kind": "file", "direction": "controller-to-native",
                         "staged_path": str(path), "size": path.stat().st_size,
                         "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        elif kind == FILE_BEGIN:
            if self.file_transfer:
                self.file_abort("Replaced by a new copy")
            self.file_transfer = FileTransfer(file_manifest(data), self.staging)
            self.file_update(force=True, new=True)
        elif kind == FILE_CHUNK:
            if self.file_transfer is None:
                raise ValueError("file transfer has not started")
            self.file_transfer.chunk(data)
            self.file_update()
        elif kind == FILE_END:
            if self.file_transfer is None:
                raise ValueError("file transfer has not started")
            transfer = self.file_transfer
            if transfer.end(data):
                uris = [path.as_uri().encode() for path in transfer.paths]
                self.publish({"text/uri-list": b"\r\n".join(uris) + b"\r\n",
                              "x-special/gnome-copied-files": b"copy\n" + b"\n".join(uris) + b"\n"})
                self.image_digest = self.text_digest = None
                self.file_update(force=True)
                self.record({"kind": "file", "direction": "controller-to-native",
                             "state": "completed", "file_count": len(uris),
                             "bytes_received": transfer.bytes_received,
                             "staged_paths": [str(path) for path in transfer.paths],
                             "staged_path": str(transfer.paths[0]),
                             "size": transfer.files[0]["size"], "sha256": transfer.hashes[0],
                             "files": [{"name": entry["name"], "size": entry["size"], "sha256": digest}
                                       for entry, digest in zip(transfer.files, transfer.hashes)]})
                self.file_transfer = None
            else:
                self.file_update(force=True)
        elif kind == FILE_ABORT:
            self.file_abort(data.decode("utf-8")[:300])
        elif kind in (GET_IMAGE, GET_HOST, GET_HOST_IMAGE):
            if not self.image_dirty:
                return b""
            self.image_dirty = False
            if self.d.get_selection_owner(self.clip) == self.window:
                return b""
            try:
                targets = subprocess.run(["/usr/bin/xclip", "-selection", "clipboard",
                                          "-out", "-target", "TARGETS"],
                                         capture_output=True, timeout=1)
                offered = targets.stdout.decode(errors="replace").splitlines()
                target = next((name for name in ("image/png", "image/bmp")
                               if name in offered), None)
                if target is None and kind in (GET_HOST, GET_HOST_IMAGE):
                    target = next((name for name in ("UTF8_STRING", "text/plain;charset=utf-8")
                                   if name in offered), None)
                if target is None:
                    self.image_digest = self.text_digest = None
                    return b""
                result = subprocess.run(["/usr/bin/xclip", "-selection", "clipboard",
                                         "-out", "-target", target],
                                        capture_output=True, timeout=1)
                if result.returncode or len(result.stdout) > MAX_BYTES:
                    return b""
                if not target.startswith("image/"):
                    data = result.stdout
                    data.decode("utf-8")
                    if not data or b"\0" in data or len(data) > 4194304:
                        return b""
                    digest = hashlib.sha256(data).digest()
                    self.image_digest = None
                    if digest == self.text_digest:
                        return b""
                    self.text_digest = digest
                    self.record({"kind": "text", "direction": "native-to-wine-prepared"})
                    return struct.pack("<I", TEXT) + data
                if target == "image/png":
                    image = png_image(result.stdout)
                else:
                    image = Image.open(io.BytesIO(result.stdout))
                    if image.format != "BMP" or image.width * image.height > MAX_PIXELS:
                        raise ValueError("unsupported BMP")
                    image = image.convert("RGBA")
            except (ValueError, OSError, subprocess.TimeoutExpired, Image.DecompressionBombError):
                self.image_digest = None
                return b""
            digest = hashlib.sha256(image.tobytes()).digest()
            self.text_digest = None
            if digest == self.image_digest:
                return b""
            self.image_digest = digest
            self.record({"kind": "image", "direction": "native-to-wine-prepared",
                         "width": image.width, "height": image.height,
                         "pixel_rgb_sha256": hashlib.sha256(image.convert("RGB").tobytes()).hexdigest()})
            payload = image_dib(image)
            if kind == GET_HOST_IMAGE:
                if target == "image/png":
                    png = result.stdout
                else:
                    encoded = io.BytesIO()
                    image.save(encoded, format="PNG")
                    png = encoded.getvalue()
                bundled = struct.pack("<II", GET_HOST_IMAGE, len(payload)) + payload + png
                # Retain the existing large-image DIB path at the transport limit.
                return bundled if len(bundled) <= MAX_BYTES else struct.pack("<I", DIB) + payload
            return struct.pack("<I", DIB) + payload if kind == GET_HOST else payload
        else:
            raise ValueError("unknown clipboard operation")
        return None

    def event(self, event):
        if isinstance(event, SelectionNotify):
            if event.selection == self.clip and event.owner != self.window:
                if self.file_transfer:
                    self.file_abort("Cancelled because a new native clipboard copy replaced the files")
                self.image_dirty = True
        elif event.type == X.SelectionRequest:
            prop = event.property or event.target
            values = self.values.get(event.selection, {})
            if event.target == self.targets:
                event.requestor.change_property(prop, Xatom.ATOM, 32,
                                                [self.targets, *values])
            elif event.target in values:
                data = values[event.target]
                if len(data) > 65536:
                    event.requestor.change_attributes(event_mask=X.PropertyChangeMask)
                    event.requestor.change_property(prop, self.incr, 32, [len(data)])
                    self.transfers[(event.requestor.id, prop)] = (
                        event.requestor, event.target, data, 0, time.monotonic())
                else:
                    event.requestor.change_property(prop, event.target, 8, data)
            else:
                prop = X.NONE
            event.requestor.send_event(protocol.event.SelectionNotify(
                time=event.time, requestor=event.requestor, selection=event.selection,
                target=event.target, property=prop))
            self.d.flush()
        elif event.type == X.PropertyNotify and event.state == X.PropertyDelete:
            key = (event.window.id, event.atom)
            if key in self.transfers:
                window, target, data, offset, _ = self.transfers[key]
                chunk = data[offset:offset + 65536]
                window.change_property(event.atom, target, 8, chunk)
                if chunk:
                    self.transfers[key] = (window, target, data, offset + len(chunk),
                                           time.monotonic())
                else:
                    del self.transfers[key]
                self.d.flush()
        now = time.monotonic()
        self.transfers = {k: v for k, v in self.transfers.items() if now - v[4] < 10}


def exact(client, length):
    chunks = bytearray()
    while len(chunks) < length:
        chunk = client.recv(min(65536, length - len(chunks)))
        if not chunk:
            raise EOFError()
        chunks.extend(chunk)
    return bytes(chunks)


def serve(listener, token, work):
    while True:
        client, _ = listener.accept()
        with client:
            file_active, file_count = False, 0
            client.settimeout(2)
            try:
                magic, version = struct.unpack("<II", exact(client, 8))
                if magic != MAGIC or version != 1 or not hmac.compare_digest(exact(client, 64), token):
                    continue
                client.sendall(struct.pack("<IIII", MAGIC, 0, 1, 0))
                while True:
                    magic, sequence, length, kind = struct.unpack("<IIII", exact(client, 16))
                    if magic != MAGIC or length > MAX_BYTES or kind > GET_HOST_IMAGE:
                        raise ValueError("invalid request")
                    data = exact(client, length)
                    answer = queue.Queue(1)
                    work.put((kind, data, answer))
                    output, error = answer.get(timeout=3)
                    if kind == FILE_BEGIN and not error:
                        file_active = True
                        file_count, = struct.unpack_from("<I", data)
                    elif kind == FILE_ABORT or error or (kind == FILE_END and
                            len(data) == 4 and struct.unpack("<I", data)[0] + 1 == file_count):
                        file_active = False
                    # UU may need to fetch a delayed OLE stream before our next chunk.
                    client.settimeout(30 if file_active else 2)
                    size = len(output) if kind in (GET_IMAGE, GET_HOST, GET_HOST_IMAGE) else (length if not error else 0)
                    client.sendall(struct.pack("<IIII", MAGIC, sequence, size, error))
                    if kind in (GET_IMAGE, GET_HOST, GET_HOST_IMAGE) and output:
                        client.sendall(output)
            except (EOFError, OSError, ValueError, queue.Empty):
                pass
            finally:
                if file_active:
                    work.put((FILE_ABORT, b"File connection interrupted", None))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ready-file", type=Path, required=True)
    parser.add_argument("--staging", type=Path, default=Path.home() / ".local/share/uu-remote/clipboard-files")
    parser.add_argument("--status-file", type=Path, default=Path.home() / ".local/state/uu-remote-bridge/clipboard-status.json")
    args = parser.parse_args()
    token = os.environ["UURB_X11_CLIPBOARD_TOKEN"].encode()
    if len(token) != 64 or not all(c in b"0123456789abcdefABCDEF" for c in token):
        raise SystemExit("invalid clipboard token")
    os.umask(0o077)
    clipboard = Clipboard(args.staging, args.status_file)
    work = queue.Queue(8)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    threading.Thread(target=serve, args=(listener, token, work), daemon=True).start()
    args.ready_file.write_text(str(listener.getsockname()[1]) + "\n")
    def stop(*_):
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, stop)
    try:
        print("Native clipboard ready; text+bitmap+multi-file streaming; bitmap bidirectional.", flush=True)
        while True:
            if clipboard.file_progress:
                clipboard.file_progress.tick()
            while clipboard.d.pending_events():
                clipboard.event(clipboard.d.next_event())
            try:
                kind, data, answer = work.get_nowait()
            except queue.Empty:
                select.select([clipboard.d.fileno()], [], [], 0.002 if clipboard.file_transfer else 0.02)
                continue
            try:
                output = clipboard.operation(kind, data)
                if answer is not None:
                    answer.put((output or b"", 0))
            except (ValueError, OSError, RuntimeError, struct.error) as error:
                if FILE_BEGIN <= kind <= FILE_ABORT:
                    clipboard.file_abort(str(error))
                if answer is not None:
                    answer.put((b"", 0x3001))
    finally:
        if clipboard.file_transfer:
            clipboard.file_abort("Clipboard helper stopped")
        args.ready_file.unlink(missing_ok=True)
        listener.close()
        clipboard.d.close()


if __name__ == "__main__":
    main()
