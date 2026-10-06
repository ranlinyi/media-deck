#!/usr/bin/env python3
"""media-deck core: scan media folders, build thumbnails, remux Steam clips.

Shared by the native GTK4 app (app.py). No HTTP, no browser.
"""
import hashlib
import os
import re
import shutil
import subprocess
import threading
from pathlib import Path

HOME = Path.home()
STEAM = Path(os.environ.get("MEDIA_DECK_STEAM", HOME / ".local/share/Steam"))
CACHE = Path(os.environ.get("MEDIA_DECK_CACHE", HOME / ".cache/media-deck"))

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".jfif", ".webp", ".gif", ".bmp", ".avif", ".tif", ".tiff"}
VIDEO_EXT = {".mp4", ".webm", ".mov", ".mkv", ".m4v", ".avi", ".mpg", ".mpeg", ".ts", ".m2ts"}

CLIP_LOCKS = {}
CLIP_LOCKS_GUARD = threading.Lock()


def canonical(path):
    try:
        return str(Path(path).resolve())
    except OSError:
        return str(Path(path).absolute())


def collect_roots(extra=None):
    roots = []

    def add(source, path):
        p = Path(path)
        if p.is_dir():
            roots.append((source, p))

    add("我的图片", HOME / "Pictures")
    add("视频", HOME / "Videos")
    add("壁纸引擎", STEAM / "steamapps/workshop/content/431960")
    udroot = STEAM / "userdata"
    if udroot.is_dir():
        for ud in sorted(udroot.iterdir()):
            if ud.is_dir():
                add("Steam 截图", ud / "760/remote")
                add("Steam 录制", ud / "gamerecordings/clips")
    for base in (STEAM / "steamapps/compatdata", HOME / "Games"):
        if base.is_dir():
            for g in sorted(base.iterdir()):
                if g.is_dir():
                    for p in g.glob("pfx/**/drive_c/users/*/Pictures"):
                        add("游戏截图", p)
    for e in (extra or []):
        add("自定义", e)
    return roots


COLLAPSE_DIRS = {"thumbnails", "image", "screenshots"}


def folder_of(dirpath):
    """Containing folder used for grouping, with known sub-folders collapsed
    so e.g. .../<appid>/screenshots and .../<appid> share one group."""
    parts = list(Path(dirpath).parts)
    while len(parts) > 1 and parts[-1].lower() in COLLAPSE_DIRS:
        parts.pop()
    return str(Path(*parts))


def dir_size(path):
    total = 0
    for dp, _dn, fns in os.walk(path):
        for fn in fns:
            try:
                total += os.path.getsize(os.path.join(dp, fn))
            except OSError:
                pass
    return total


def build_item(source, realpath, name, kind, thumb, folder):
    try:
        st = os.stat(realpath)
        size = st.st_size
        mtime = st.st_mtime
    except OSError:
        size = 0
        mtime = 0
    return {
        "id": hashlib.sha1(realpath.encode("utf-8", "replace")).hexdigest()[:16],
        "source": source,
        "path": realpath,
        "name": name,
        "kind": kind,
        "size": size,
        "mtime": mtime,
        "thumb": thumb,
        "folder": folder,
    }


def scan(extra=None):
    items = []
    seen_files = set()
    seen_dirs = set()
    for source, root in collect_roots(extra):
        for dirpath, dirnames, filenames in os.walk(root, followlinks=True):
            dp = Path(dirpath)
            try:
                st = dp.stat()
                dkey = (st.st_dev, st.st_ino)
            except OSError:
                dirnames[:] = []
                continue
            if dkey in seen_dirs:
                dirnames[:] = []
                continue
            seen_dirs.add(dkey)
            # Steam keeps duplicate low-res copies under "thumbnails"; skip them
            dirnames[:] = [d for d in dirnames if d.lower() != "thumbnails"]
            if "session.mpd" in filenames or "clip.pb" in filenames:
                rp = canonical(dp)
                dirnames[:] = []
                if rp in seen_files:
                    continue
                seen_files.add(rp)
                thumb = dp / "thumbnail.jpg"
                it = build_item(source, rp, dp.name, "recording", str(thumb) if thumb.exists() else None, folder_of(dp))
                it["size"] = dir_size(dp)
                items.append(it)
                continue
            for fn in filenames:
                if fn.startswith("."):
                    continue
                ext = os.path.splitext(fn)[1].lower()
                if ext in IMAGE_EXT:
                    kind = "image"
                elif ext in VIDEO_EXT:
                    kind = "video"
                else:
                    continue
                rp = canonical(dp / fn)
                if rp in seen_files:
                    continue
                seen_files.add(rp)
                items.append(build_item(source, rp, fn, kind, None, folder_of(dp)))
    items.sort(key=lambda x: x["mtime"], reverse=True)
    return items


def _stream_indices(segdir):
    idxs = set()
    for p in segdir.glob("init-stream*.m4s"):
        m = re.search(r"init-stream(\d+)\.m4s$", p.name)
        if m:
            idxs.add(int(m.group(1)))
    return sorted(idxs)


def _concat_stream(segdir, idx, dest):
    init = segdir / ("init-stream" + str(idx) + ".m4s")
    chunks = []
    for p in segdir.glob("chunk-stream" + str(idx) + "-*.m4s"):
        m = re.search(r"-(\d+)\.m4s$", p.name)
        if m:
            chunks.append((int(m.group(1)), p))
    chunks.sort()
    if not init.exists() or not chunks:
        return False
    try:
        with open(dest, "wb") as w:
            w.write(init.read_bytes())
            for _, p in chunks:
                w.write(p.read_bytes())
    except OSError:
        return False
    return dest.exists() and dest.stat().st_size > 0


def _find_segment_dir(d):
    mpds = sorted(d.rglob("session.mpd"))
    if mpds:
        return mpds[0], mpds
    init = next(iter(sorted(d.rglob("init-stream0.m4s"))), None)
    if init is not None:
        return init, []
    return None, []


def clip_remux(item):
    """Remux a Steam Game Recording (DASH m4s) folder into a playable mp4."""
    out = CACHE / "clips" / (item["id"] + ".mp4")
    if out.exists() and out.stat().st_size > 0:
        return out
    with CLIP_LOCKS_GUARD:
        lock = CLIP_LOCKS.setdefault(item["id"], threading.Lock())
    with lock:
        if out.exists() and out.stat().st_size > 0:
            return out
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_name(out.name + ".tmp.mp4")
        d = Path(item["path"])
        ok = False
        segdir, mpds = _find_segment_dir(d)
        if segdir is not None:
            parts = []
            for idx in _stream_indices(segdir):
                part = out.with_name(out.name + ".s" + str(idx) + ".mp4")
                if _concat_stream(segdir, idx, part):
                    parts.append(part)
            if parts:
                cmd = ["ffmpeg", "-y", "-v", "error"]
                for part in parts:
                    cmd += ["-i", str(part)]
                for i in range(len(parts)):
                    cmd += ["-map", str(i)]
                cmd += ["-c", "copy", str(tmp)]
                try:
                    subprocess.run(cmd, capture_output=True, timeout=1200)
                except (OSError, subprocess.SubprocessError):
                    pass
                ok = tmp.exists() and tmp.stat().st_size > 0
                for part in parts:
                    try:
                        part.unlink()
                    except OSError:
                        pass
        if not ok and mpds:
            try:
                subprocess.run(["ffmpeg", "-y", "-v", "error", "-allowed_extensions", "ALL", "-i", str(mpds[0]), "-c", "copy", str(tmp)], capture_output=True, timeout=1200)
            except (OSError, subprocess.SubprocessError):
                pass
            ok = tmp.exists() and tmp.stat().st_size > 0
        if ok:
            os.replace(tmp, out)
            return out
    return None


def _pixbuf_thumb(src, out):
    try:
        import gi
        gi.require_version("GdkPixbuf", "2.0")
        from gi.repository import GdkPixbuf
    except Exception:
        return False
    try:
        pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(src), 360, -1, True)
        pb.savev(str(out), "jpeg", ["quality"], ["85"])
    except Exception:
        return False
    return out.exists() and out.stat().st_size > 0


def thumb_for(item):
    out = CACHE / "thumbs" / (item["id"] + ".jpg")
    if out.exists() and out.stat().st_size > 0 and (item["kind"] == "recording" or out.stat().st_mtime >= item["mtime"]):
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    sources = []
    if item["kind"] == "recording":
        if item.get("thumb") and Path(item["thumb"]).exists():
            sources.append(("copy", item["thumb"]))
        mp4 = clip_remux(item)
        if mp4:
            sources.append(("video", str(mp4)))
    elif item["kind"] == "video":
        sources.append(("video", item["path"]))
    else:
        sources.append(("image", item["path"]))
    for skind, src in sources:
        if skind == "copy":
            try:
                shutil.copyfile(src, out)
                return out
            except OSError:
                continue
        if skind == "image" and _pixbuf_thumb(src, out):
            return out
        attempts = [["-ss", "0.5", "-i", src], ["-i", src]] if skind == "video" else [["-i", src]]
        for a in attempts:
            cmd = ["ffmpeg", "-y", "-v", "error"] + a + ["-frames:v", "1", "-vf", "scale=360:-2:force_original_aspect_ratio=decrease", "-q:v", "6", str(out)]
            try:
                subprocess.run(cmd, capture_output=True, timeout=60)
            except (OSError, subprocess.SubprocessError):
                continue
            if out.exists() and out.stat().st_size > 0:
                return out
    return None
