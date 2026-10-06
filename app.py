#!/usr/bin/env python3
"""media-deck: native GTK4 single-window image / video / recording browser.

- Bundles the missing GStreamer H.264/AAC decoders in ./gst.
- Left stick is polled (full deflection keeps moving, release stops at once).
- Image and video share one zoom/pan stage implemented with Gtk.Fixed so the
  gestures are fully ours (no competition with GtkScrolledWindow gestures).
- Set MEDIA_DECK_DEBUG=1 to log input/gesture events to stderr.
"""
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
GST_DIR = APP_DIR / "gst"
if GST_DIR.is_dir():
    _prev = os.environ.get("GST_PLUGIN_PATH", "")
    os.environ["GST_PLUGIN_PATH"] = str(GST_DIR) + ((os.pathsep + _prev) if _prev else "")

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Pango", "1.0")
gi.require_version("Gst", "1.0")
gi.require_version("Graphene", "1.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gtk, Gdk, GLib, Gio, GObject, Graphene, Pango, Gst, GdkPixbuf

import media

Gst.init(None)

DEBUG = bool(os.environ.get("MEDIA_DECK_DEBUG"))

try:
    import evdev
    from evdev import ecodes
    HAVE_EVDEV = True
except Exception:
    HAVE_EVDEV = False

_executor = ThreadPoolExecutor(max_workers=2)
KIND_LABELS = {"image": "图片", "video": "视频", "recording": "录像"}
KIND_ORDER = ["all", "image", "video", "recording"]
KIND_TITLES = {"all": "全部", "image": "图片", "video": "视频", "recording": "录像"}
COLS = 4
IMG_H = 168
STICK_THRESHOLD = 20000
STICK_INTERVAL = 130
SEEK_STEP = 5.0
ZOOM_GAIN = 1.1
ANIM_EXT = {".gif", ".webp"}


def dbg(*args):
    if DEBUG:
        print("[media-deck]", *args, file=sys.stderr, flush=True)


def shorten_path(path):
    if not path:
        return ""
    home = str(Path.home())
    p = path
    if p.startswith(home):
        p = "~" + p[len(home):]
    parts = [x for x in p.split("/") if x]
    if len(parts) > 2:
        return ".../" + "/".join(parts[-2:])
    return p


def fmt_size(n):
    if not n:
        return ""
    units = ["B", "KB", "MB", "GB"]
    i = 0
    v = float(n)
    while v >= 1024 and i < len(units) - 1:
        v /= 1024.0
        i += 1
    if i == 0:
        return str(int(v)) + " B"
    return ("%.1f " % v if v < 10 else "%.0f " % v) + units[i]


def find_gamepad():
    if not HAVE_EVDEV:
        return None
    found = []
    for path in evdev.list_devices():
        try:
            d = evdev.InputDevice(path)
        except Exception:
            continue
        try:
            keys = d.capabilities().get(ecodes.EV_KEY, [])
        except Exception:
            continue
        if ecodes.BTN_SOUTH in keys and ecodes.BTN_EAST in keys:
            found.append(d)
    for d in found:
        n = d.name.lower()
        if "x-box" in n or "xbox" in n:
            return d
    return found[0] if found else None


class ScaledPaintable(GObject.Object, Gdk.Paintable):
    """Draws a texture scaled/translated inside whatever area the widget gives
    it. The widget itself never changes size while zooming, so zooming only
    triggers a repaint (no relayout) and stays smooth."""

    def __init__(self):
        super().__init__()
        self.texture = None
        self.zoom = 1.0
        self.pan = [0.0, 0.0]
        self.vp = (1280, 720)

    def _tex_size(self):
        t = self.texture
        if t is None:
            return 0, 0
        w = h = 0
        try:
            w = t.get_intrinsic_width()
        except Exception:
            w = 0
        try:
            h = t.get_intrinsic_height()
        except Exception:
            h = 0
        if not w:
            try:
                w = t.get_width()
            except Exception:
                w = 0
        if not h:
            try:
                h = t.get_height()
            except Exception:
                h = 0
        return w or 0, h or 0

    def do_get_intrinsic_width(self):
        return max(1, int(self.vp[0]))

    def do_get_intrinsic_height(self):
        return max(1, int(self.vp[1]))

    def do_get_intrinsic_aspect_ratio(self):
        return (self.vp[0] / float(self.vp[1])) if self.vp[1] else 1.0

    def do_snapshot(self, snapshot, width, height):
        t = self.texture
        if t is None:
            return
        mw, mh = self._tex_size()
        if not mw or not mh:
            return
        W = float(width if width and width > 1 else self.vp[0])
        H = float(height if height and height > 1 else self.vp[1])
        base = min(1.0, W / mw, H / mh)
        eff = base * self.zoom
        tw = mw * eff
        th = mh * eff
        ox = (W - tw) / 2.0
        oy = (H - th) / 2.0
        if tw > W:
            ox = max(W - tw, min(0.0, ox + self.pan[0]))
        if th > H:
            oy = max(H - th, min(0.0, oy + self.pan[1]))
        snapshot.save()
        snapshot.translate(Graphene.Point().init(ox, oy))
        snapshot.scale(eff, eff)
        try:
            t.snapshot(snapshot, mw, mh)
        except Exception:
            pass
        snapshot.restore()


class Tile(Gtk.FlowBoxChild):
    def __init__(self, item):
        super().__init__()
        self.item = item
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        box.set_margin_top(4)
        box.set_margin_bottom(4)
        box.set_margin_start(4)
        box.set_margin_end(4)
        self.pic = Gtk.Picture()
        self.pic.set_content_fit(Gtk.ContentFit.COVER)
        self.pic.set_hexpand(True)
        if item.get("kind") == "folder":
            tab = Gtk.Box()
            tab.add_css_class("folder-tab")
            tab.set_size_request(96, 12)
            tab.set_halign(Gtk.Align.START)
            box.append(tab)
            body = Gtk.Box()
            body.add_css_class("folder-body")
            self.pic.set_size_request(280, IMG_H)
            body.append(self.pic)
            box.append(body)
        else:
            self.pic.set_size_request(300, IMG_H)
            box.append(self.pic)
        name = Gtk.Label(label=item["name"])
        name.set_xalign(0.0)
        name.set_ellipsize(Pango.EllipsizeMode.END)
        name.set_max_width_chars(26)
        box.append(name)
        if item.get("kind") == "folder":
            sub = Gtk.Label(label="文件夹 · " + item["source"])
        else:
            sub = Gtk.Label(label=item["source"])
        sub.set_xalign(0.0)
        sub.set_ellipsize(Pango.EllipsizeMode.END)
        sub.set_max_width_chars(28)
        sub.add_css_class("dim-label")
        box.append(sub)
        self.set_child(box)

    def set_thumb(self, path):
        try:
            self.pic.set_filename(str(path))
        except Exception:
            pass


class MainWindow(Gtk.ApplicationWindow):
    def __init__(self, app, fullscreen=False, preopen=None):
        super().__init__(application=app, title="媒体库")
        self.set_default_size(1280, 800)
        self.pending_open = preopen
        self.flow = None
        self.stack = None
        self.items = []
        self.view = []
        self.tile_list = []
        self.folder = None
        self.mode = "files"
        self.kind = "all"
        self.q = ""
        self.sel_index = 0
        self.tiles = {}
        self.thumb_cache = {}
        self.media_stream = None
        self.media_kind = None
        self.media_size = None
        self.img_zoom = 1.0
        self.zoom0 = 1.0
        self.pan = [0.0, 0.0]
        self.pan0 = [0.0, 0.0]
        self._layout_pending = False
        self.token = 0
        self.gamepad = None
        self.triggers = [False, False]
        self.gif_iter = None
        self.gif_next = 0.0

        hb = Gtk.HeaderBar()
        self.set_titlebar(hb)
        kbox = Gtk.Box(spacing=4)
        self.kind_buttons = {}
        last = None
        for kid in KIND_ORDER:
            b = Gtk.ToggleButton(label=KIND_TITLES[kid])
            if last is not None:
                b.set_group(last)
            b.connect("toggled", self.on_kind, kid)
            self.kind_buttons[kid] = b
            kbox.append(b)
            if last is None:
                last = b
        self.kind_buttons["all"].set_active(True)
        hb.pack_start(kbox)
        self.nav_btn = Gtk.Button(label="文件夹")
        self.nav_btn.connect("clicked", self.on_nav)
        hb.pack_start(self.nav_btn)
        self.search = Gtk.SearchEntry()
        self.search.set_placeholder_text("搜索名称…")
        self.search.connect("search-changed", self.on_search)
        hb.pack_start(self.search)
        quit_btn = Gtk.Button(label="退出")
        quit_btn.connect("clicked", lambda *_: self.get_application().quit())
        hb.pack_end(quit_btn)
        refresh = Gtk.Button(label="刷新")
        refresh.connect("clicked", lambda *_: self.reload())
        hb.pack_end(refresh)
        self.status = Gtk.Label(label="扫描中…")
        self.status.add_css_class("dim-label")
        hb.pack_end(self.status)

        self.stack = Gtk.Stack()
        self.stack.set_hhomogeneous(False)
        self.stack.set_vhomogeneous(False)
        self.set_child(self.stack)

        self.flow = Gtk.FlowBox()
        self.flow.set_valign(Gtk.Align.START)
        self.flow.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.flow.set_activate_on_single_click(True)
        self.flow.set_homogeneous(True)
        self.flow.set_min_children_per_line(COLS)
        self.flow.set_max_children_per_line(COLS)
        self.flow.set_column_spacing(6)
        self.flow.set_row_spacing(6)
        self.flow.connect("child-activated", self.on_child_activated)
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_child(self.flow)
        self.stack.add_named(scroller, "grid")

        self.build_viewer()

        kc = Gtk.EventControllerKey()
        kc.connect("key-pressed", self.on_key)
        self.add_controller(kc)

        if fullscreen:
            self.set_resizable(False)
            self.fullscreen()

        self.reload()
        GLib.timeout_add(33, self._video_tick)
        self.start_gamepad()

    # ---------- viewer ----------
    def build_viewer(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        bar = Gtk.Box(spacing=8)
        bar.set_margin_top(6)
        bar.set_margin_bottom(6)
        bar.set_margin_start(8)
        bar.set_margin_end(8)
        back = Gtk.Button(label="‹ 返回 (B)")
        back.connect("clicked", lambda *_: self.close_viewer())
        bar.append(back)
        self.vtitle = Gtk.Label()
        self.vtitle.set_xalign(0.0)
        self.vtitle.set_hexpand(True)
        self.vtitle.set_ellipsize(Pango.EllipsizeMode.END)
        bar.append(self.vtitle)
        self.vmeta = Gtk.Label()
        self.vmeta.add_css_class("dim-label")
        bar.append(self.vmeta)
        prev = Gtk.Button(label="‹")
        prev.connect("clicked", lambda *_: self.step(-1))
        nxt = Gtk.Button(label="›")
        nxt.connect("clicked", lambda *_: self.step(1))
        bar.append(prev)
        bar.append(nxt)
        box.append(bar)

        # One Picture fills the stage and the paintable draws the zoomed/panned
        # content inside it, so the widget size never changes while zooming.
        self.paintable = ScaledPaintable()
        self.picture = Gtk.Picture.new_for_paintable(self.paintable)
        self.picture.set_can_shrink(True)
        self.picture.set_content_fit(Gtk.ContentFit.FILL)
        self.picture.set_hexpand(True)
        self.picture.set_vexpand(True)
        self.picture.set_halign(Gtk.Align.FILL)
        self.picture.set_valign(Gtk.Align.FILL)
        self.picture.connect("notify::width", self._on_stage_size)
        self.picture.connect("notify::height", self._on_stage_size)

        gz = Gtk.GestureZoom()
        gz.connect("begin", self.on_zoom_begin)
        gz.connect("scale-changed", self.on_zoom_changed)
        gz.connect("end", self.on_zoom_end)
        self.picture.add_controller(gz)
        gd = Gtk.GestureDrag()
        gd.connect("drag-begin", self.on_drag_begin)
        gd.connect("drag-update", self.on_drag_update)
        self.picture.add_controller(gd)

        stbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        stbox.set_valign(Gtk.Align.CENTER)
        stbox.set_halign(Gtk.Align.CENTER)
        self.vspinner = Gtk.Spinner()
        self.vstatus = Gtk.Label(label="")
        stbox.append(self.vspinner)
        stbox.append(self.vstatus)

        self.vstack = Gtk.Stack()
        self.vstack.set_hhomogeneous(False)
        self.vstack.set_vhomogeneous(False)
        self.vstack.set_vexpand(True)
        self.vstack.add_named(self.picture, "media")
        self.vstack.add_named(stbox, "status")
        box.append(self.vstack)

        # Native playback controls (play/pause + seek bar). There is no
        # Gtk.Video here, so this is the only progress bar in the window.
        self.controls = Gtk.MediaControls()
        self.controls.set_visible(False)
        box.append(self.controls)

        self.stack.add_named(box, "viewer")

    def show_media_page(self, which):
        self.media_kind = which
        self.img_zoom = 1.0
        self.pan = [0.0, 0.0]
        self.picture.set_visible(True)
        self.vstack.set_visible_child_name("media")
        self.controls.set_visible(which == "video")
        self.layout_media()

    def _on_stage_size(self, *_a):
        try:
            self.paintable.vp = (max(1, self.picture.get_width()), max(1, self.picture.get_height()))
        except Exception:
            pass
        try:
            self.picture.queue_draw()
        except Exception:
            pass

    def _relayout(self):
        self.layout_media()
        return False

    def _schedule_layout(self):
        if self._layout_pending:
            return
        self._layout_pending = True
        GLib.idle_add(self._do_layout)

    def _do_layout(self):
        self._layout_pending = False
        self.layout_media()
        return False

    def layout_media(self):
        self.paintable.zoom = self.img_zoom
        self.paintable.pan = [self.pan[0], self.pan[1]]
        try:
            self.picture.queue_draw()
            self.paintable.emit("invalidate-contents")
        except Exception:
            pass

    def set_status(self, text, spinning=False):
        self.vstatus.set_text(text)
        if spinning:
            self.vspinner.start()
        else:
            self.vspinner.stop()
        self.vstack.set_visible_child_name("status")

    def stop_media(self):
        if self.media_stream is not None:
            try:
                self.media_stream.pause()
            except Exception:
                pass
            self.media_stream = None
        self.gif_iter = None
        try:
            self.controls.set_media_stream(None)
        except Exception:
            pass
        self.paintable.texture = None
        try:
            self.picture.queue_draw()
        except Exception:
            pass

    def toggle_play(self):
        if self.media_stream is not None:
            try:
                if self.media_stream.get_playing():
                    self.media_stream.pause()
                else:
                    self.media_stream.play()
            except Exception:
                pass

    def seek_rel(self, sec):
        s = self.media_stream
        if s is None:
            return
        try:
            if not s.is_seekable():
                return
            cur = s.get_timestamp() or 0
            t = cur + int(sec * 1000000)
            dur = s.get_duration()
            if dur and dur > 0:
                t = min(t, dur - 1000000)
            s.seek(max(0, t))
        except Exception:
            pass

    def is_video_view(self):
        return self.media_stream is not None

    def shoulder(self, d):
        if self.stack.get_visible_child_name() == "viewer":
            if self.is_video_view():
                self.seek_rel(-SEEK_STEP if d < 0 else SEEK_STEP)
        else:
            self.cycle_kind(d)

    def cycle_kind(self, d):
        i = KIND_ORDER.index(self.kind)
        i = (i + d) % len(KIND_ORDER)
        self.kind = KIND_ORDER[i]
        btn = self.kind_buttons.get(self.kind)
        if btn is not None:
            btn.set_active(True)
        else:
            self.apply_filters()

    def open_item_by_index(self, idx):
        if not self.view:
            return
        self.sel_index = max(0, min(idx, len(self.view) - 1))
        self.show_current()
        self.stack.set_visible_child_name("viewer")

    def on_child_activated(self, flow, child):
        try:
            idx = self.tile_list.index(child)
        except ValueError:
            return
        it = self.view[idx]
        if it.get("kind") == "folder":
            self.folder = it["folder"]
            self.mode = "files"
            self.update_nav()
            self.apply_filters()
            return
        self.open_item_by_index(idx)

    def show_current(self):
        if not self.view:
            return
        item = self.view[self.sel_index]
        self.vtitle.set_text(item["name"])
        self.vmeta.set_text(item["source"] + " · " + KIND_LABELS.get(item["kind"], "") + " · " + fmt_size(item["size"]))
        self.stop_media()
        self.media_size = None
        self.token += 1
        token = self.token
        if item["kind"] == "image":
            self.show_media_page("image")
            self.load_image(item["path"])
        elif item["kind"] == "video":
            self.play_file(item["path"])
        else:
            self.set_status("录像转封装中，请稍候…", spinning=True)
            threading.Thread(target=self._remux_worker, args=(item, token), daemon=True).start()

    def _remux_worker(self, item, token):
        mp4 = media.clip_remux(item)
        GLib.idle_add(self._remux_done, token, str(mp4) if mp4 else None)

    def _remux_done(self, token, mp4):
        if token != self.token:
            return False
        if not mp4:
            self.set_status("这个录像转封装失败，无法播放")
            return False
        self.play_file(mp4)
        return False

    def play_file(self, path):
        try:
            stream = Gtk.MediaFile.new_for_filename(path)
        except Exception as e:
            self.set_status("无法打开：" + str(e))
            return
        self.media_stream = stream
        self.show_media_page("video")
        try:
            self.controls.set_media_stream(stream)
        except Exception:
            pass
        try:
            stream.set_loop(True)
        except Exception:
            pass
        stream.play()

    def load_image(self, path):
        """Show a still image, or autoplay it when it is an animated GIF/WebP."""
        if Path(path).suffix.lower() in ANIM_EXT:
            try:
                anim = GdkPixbuf.PixbufAnimation.new_from_file(path)
                if not anim.is_static_image():
                    it = anim.get_iter(None)
                    pb = it.get_pixbuf()
                    self.gif_iter = it
                    self.paintable.texture = Gdk.Texture.new_for_pixbuf(pb)
                    self.media_size = (pb.get_width(), pb.get_height())
                    delay = it.get_delay_time() or 100
                    self.gif_next = time.monotonic() + max(0.02, delay / 1000.0)
                    self.layout_media()
                    return
            except Exception as e:
                dbg("animation failed:", e)
        try:
            tex = Gdk.Texture.new_from_filename(path)
            self.paintable.texture = tex
            self.media_size = (tex.get_width(), tex.get_height())
            self.layout_media()
        except Exception as e:
            self.set_status("无法显示这张图片：" + str(e))

    def _advance_gif(self):
        it = self.gif_iter
        if it is None:
            return
        try:
            now = time.monotonic()
            if now < self.gif_next:
                return
            it.advance(None)
            pb = it.get_pixbuf()
            self.paintable.texture = Gdk.Texture.new_for_pixbuf(pb)
            self.picture.queue_draw()
            delay = it.get_delay_time() or 100
            self.gif_next = now + max(0.02, delay / 1000.0)
        except Exception as e:
            dbg("gif tick failed:", e)
            self.gif_iter = None

    def _video_tick(self):
        if self.gif_iter is not None:
            self._advance_gif()
        if self.media_kind == "video" and self.media_stream is not None:
            try:
                img = self.media_stream.get_current_image()
            except Exception:
                img = None
            if img is not None:
                self.paintable.texture = img
                self.picture.queue_draw()
        return True

    def on_zoom_begin(self, gesture, sequence):
        self.zoom0 = self.img_zoom
        dbg("ZOOM_BEGIN", round(self.img_zoom, 3))
        return True

    def on_zoom_changed(self, gesture, scale):
        self.img_zoom = max(0.2, min(8.0, self.zoom0 * (scale ** ZOOM_GAIN)))
        self.layout_media()
        dbg("ZOOM", round(scale, 3), round(self.img_zoom, 3))

    def on_zoom_end(self, gesture, sequence):
        dbg("ZOOM_END", round(self.img_zoom, 3))

    def on_drag_begin(self, gesture, x, y):
        self.pan0 = list(self.pan)
        dbg("DRAG_BEGIN", round(x, 1), round(y, 1), round(self.img_zoom, 3))

    def on_drag_update(self, gesture, ox, oy):
        if self.img_zoom <= 1.0:
            return
        self.pan = [self.pan0[0] + ox, self.pan0[1] + oy]
        self.layout_media()

    def close_viewer(self):
        self.stop_media()
        self.media_kind = None
        self.stack.set_visible_child_name("grid")
        self.set_sel(self.sel_index)

    def step(self, d):
        if not self.view:
            return
        self.sel_index = (self.sel_index + d) % len(self.view)
        self.show_current()

    # ---------- grid selection ----------
    def set_sel(self, j):
        if not self.tile_list:
            return
        j = max(0, min(j, len(self.tile_list) - 1))
        self.sel_index = j
        tile = self.tile_list[j]
        try:
            self.flow.select_child(tile)
            tile.grab_focus()
        except Exception:
            pass

    def move_sel(self, dx, dy):
        if not self.tile_list:
            return
        self.set_sel(self.sel_index + dx + dy * COLS)

    # ---------- keyboard ----------
    def on_key(self, ctrl, keyval, keycode, state):
        name = Gdk.keyval_name(keyval)
        viewing = self.stack.get_visible_child_name() == "viewer"
        if not viewing:
            if name in ("Return", "KP_Enter") and self.view:
                self.open_item_by_index(self.sel_index)
                return True
            return False
        if name == "Escape":
            self.close_viewer()
            return True
        if name == "Right":
            self.step(1) if not self.is_video_view() else self.seek_rel(SEEK_STEP)
            return True
        if name == "Left":
            self.step(-1) if not self.is_video_view() else self.seek_rel(-SEEK_STEP)
            return True
        if name == "space":
            self.toggle_play()
            return True
        if name in ("plus", "equal"):
            self.img_zoom = min(8.0, self.img_zoom * 1.25)
            self.layout_media()
            return True
        if name == "minus":
            self.img_zoom = max(0.2, self.img_zoom / 1.25)
            self.layout_media()
            return True
        return False

    # ---------- gamepad ----------
    def start_gamepad(self):
        if not HAVE_EVDEV:
            return
        threading.Thread(target=self._gamepad_loop, daemon=True).start()
        GLib.timeout_add(STICK_INTERVAL, self.poll_stick)

    def _gamepad_loop(self):
        while True:
            dev = find_gamepad()
            if dev is None:
                time.sleep(3)
                continue
            self.gamepad = dev
            dbg("gamepad:", dev.name, dev.path)
            try:
                for ev in dev.read_loop():
                    if ev.type == ecodes.EV_KEY and ev.value == 1:
                        GLib.idle_add(self._pad_button, ev.code)
                    elif ev.type == ecodes.EV_ABS:
                        GLib.idle_add(self._pad_abs, ev.code, ev.value)
            except Exception:
                self.gamepad = None
                time.sleep(2)

    def poll_stick(self):
        dev = self.gamepad
        if dev is not None and self.stack.get_visible_child_name() != "viewer":
            try:
                x = dev.absinfo(ecodes.ABS_X).value
                y = dev.absinfo(ecodes.ABS_Y).value
            except Exception:
                x = 0
                y = 0
            dx = 1 if x > STICK_THRESHOLD else (-1 if x < -STICK_THRESHOLD else 0)
            dy = 1 if y > STICK_THRESHOLD else (-1 if y < -STICK_THRESHOLD else 0)
            if dx or dy:
                self.move_sel(dx, dy)
        return True

    def _pad_button(self, code):
        viewing = self.stack.get_visible_child_name() == "viewer"
        if code == ecodes.BTN_SOUTH:
            if viewing:
                self.toggle_play()
            else:
                self.open_item_by_index(self.sel_index)
        elif code in (ecodes.BTN_EAST, ecodes.BTN_WEST):
            if viewing:
                self.close_viewer()
        elif code in (ecodes.BTN_TL, ecodes.BTN_TL2):
            self.shoulder(-1)
        elif code in (ecodes.BTN_TR, ecodes.BTN_TR2):
            self.shoulder(1)
        elif code == ecodes.BTN_DPAD_LEFT:
            if viewing and self.is_video_view():
                self.seek_rel(-SEEK_STEP)
            elif viewing:
                self.step(-1)
            else:
                self.move_sel(-1, 0)
        elif code == ecodes.BTN_DPAD_RIGHT:
            if viewing and self.is_video_view():
                self.seek_rel(SEEK_STEP)
            elif viewing:
                self.step(1)
            else:
                self.move_sel(1, 0)
        elif code == ecodes.BTN_DPAD_UP:
            self.move_sel(0, -1)
        elif code == ecodes.BTN_DPAD_DOWN:
            self.move_sel(0, 1)
        return False

    def _pad_abs(self, code, value):
        viewing = self.stack.get_visible_child_name() == "viewer"
        if code == ecodes.ABS_HAT0X:
            if value == 0:
                return False
            if viewing:
                if self.is_video_view():
                    self.seek_rel(SEEK_STEP if value > 0 else -SEEK_STEP)
                else:
                    self.step(1 if value > 0 else -1)
            else:
                self.move_sel(1 if value > 0 else -1, 0)
        elif code == ecodes.ABS_HAT0Y:
            if value == 0:
                return False
            if not viewing:
                self.move_sel(0, 1 if value > 0 else -1)
        elif code == ecodes.ABS_Z:
            self._analog_trigger(0, value, -1)
        elif code == ecodes.ABS_RZ:
            self._analog_trigger(1, value, 1)
        return False

    def _analog_trigger(self, idx, value, d):
        pressed = value > 100
        if pressed and not self.triggers[idx]:
            self.triggers[idx] = True
            self.shoulder(d)
        elif not pressed:
            self.triggers[idx] = False

    # ---------- filters / scan ----------
    def leave_viewer(self):
        if self.stack is None:
            return
        if self.stack.get_visible_child_name() == "viewer":
            self.stop_media()
            self.media_kind = None
            self.stack.set_visible_child_name("grid")

    def on_kind(self, btn, kid):
        if not btn.get_active():
            return
        self.kind = kid
        self.leave_viewer()
        self.apply_filters()

    def on_nav(self, *_a):
        if self.mode == "folders":
            self.mode = "files"
            self.folder = None
        else:
            self.mode = "folders"
        self.leave_viewer()
        self.update_nav()
        self.apply_filters()

    def update_nav(self):
        if self.mode == "folders":
            self.nav_btn.set_label("‹ 全部文件")
        elif self.folder is not None:
            self.nav_btn.set_label("‹ 文件夹")
        else:
            self.nav_btn.set_label("文件夹")

    def on_search(self, entry):
        self.q = entry.get_text().strip().lower()
        self.leave_viewer()
        self.apply_filters()

    def apply_filters(self):
        base = []
        for it in self.items:
            if self.kind != "all" and it["kind"] != self.kind:
                continue
            if self.mode == "files" and self.folder is not None and (it.get("folder") or it["source"]) != self.folder:
                continue
            if self.q and self.q not in it["name"].lower():
                continue
            base.append(it)
        if self.mode == "folders":
            self.view = []
            by_folder = {}
            for it in base:
                f = it.get("folder") or it["source"]
                by_folder.setdefault(f, []).append(it)
            for f, its in by_folder.items():
                rep = sorted(its, key=lambda x: x["mtime"], reverse=True)[0]
                self.view.append({
                    "kind": "folder",
                    "id": "folder:" + f,
                    "name": shorten_path(f),
                    "source": "%d 项" % len(its),
                    "folder": f,
                    "rep": rep,
                    "size": 0,
                    "mtime": rep["mtime"],
                })
            self.view.sort(key=lambda x: x["name"])
        else:
            self.view = base
        self.populate()

    def populate(self):
        if self.flow is None:
            return
        child = self.flow.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.flow.remove(child)
            child = nxt
        self.tile_list = []
        for it in self.view:
            key = it["id"]
            tile = self.tiles.get(key)
            if tile is None:
                tile = Tile(it)
                self.tiles[key] = tile
                cached = self.thumb_cache.get(key)
                if cached:
                    tile.set_thumb(cached)
                elif it.get("kind") == "folder":
                    _executor.submit(self._make_folder_thumb, it["rep"], key)
                else:
                    _executor.submit(self._make_thumb, it)
            self.tile_list.append(tile)
            self.flow.append(tile)
        self.sel_index = min(self.sel_index, max(0, len(self.tile_list) - 1))
        if self.tile_list:
            self.set_sel(self.sel_index)
        self.status.set_text("%d / %d 项" % (len(self.view), len(self.items)))

    def _make_thumb(self, item):
        path = media.thumb_for(item)
        GLib.idle_add(self._thumb_done, item["id"], str(path) if path else None)

    def _make_folder_thumb(self, rep, key):
        path = media.thumb_for(rep)
        GLib.idle_add(self._thumb_done, key, str(path) if path else None)

    def _thumb_done(self, iid, path):
        if path:
            self.thumb_cache[iid] = path
            tile = self.tiles.get(iid)
            if tile is not None:
                tile.set_thumb(path)
        return False

    def reload(self):
        self.status.set_text("扫描中…")
        threading.Thread(target=self._scan_worker, daemon=True).start()

    def _scan_worker(self):
        items = media.scan()
        GLib.idle_add(self._scan_done, items)

    def _scan_done(self, items):
        self.items = items
        self.update_nav()
        self.apply_filters()
        if self.pending_open:
            target = os.path.realpath(self.pending_open)
            self.pending_open = None
            for idx, it in enumerate(self.view):
                p = it.get("path")
                if p and os.path.realpath(p) == target:
                    self.open_item_by_index(idx)
                    break
        return False


class MediaDeckApp(Gtk.Application):
    def __init__(self, fullscreen=False, preopen=None):
        super().__init__(application_id="com.steamdeck.mediadeck", flags=Gio.ApplicationFlags.FLAGS_NONE)
        self.fullscreen = fullscreen
        self.preopen = preopen

    def do_startup(self):
        Gtk.Application.do_startup(self)
        css = Gtk.CssProvider()
        css.load_from_data(b".folder-tab { background-color: #5b8fd6; border-top-left-radius: 6px; border-top-right-radius: 6px; }\n.folder-body { background-color: rgba(91,143,214,0.16); border: 2px solid #5b8fd6; border-radius: 3px 10px 10px 10px; padding: 3px; }")
        display = Gdk.Display.get_default()
        if display is not None:
            Gtk.StyleContext.add_provider_for_display(display, css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def do_activate(self):
        win = self.props.active_window
        if not win:
            win = MainWindow(self, self.fullscreen, self.preopen)
        win.present()


def main():
    fullscreen = "--fullscreen" in sys.argv
    preopen = None
    if "--open" in sys.argv:
        i = sys.argv.index("--open")
        if i + 1 < len(sys.argv):
            preopen = sys.argv[i + 1]
    if not preopen:
        preopen = os.environ.get("MEDIA_DECK_OPEN") or None
    app = MediaDeckApp(fullscreen, preopen)
    return app.run([sys.argv[0]])


if __name__ == "__main__":
    raise SystemExit(main())
