"""What a file is, from its name and its first bytes -- never from what the
sender called it."""

from __future__ import annotations

import io
import tarfile
import zipfile

import pytest
from sqlalchemy import select

from lembas.db.models import User
from lembas.services import file_kinds, fs
from lembas.services.file_kinds import detect

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 16
PDF = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n"
MP4 = b"\x00\x00\x00\x20ftypisom\x00\x00\x02\x00"
AVIF = b"\x00\x00\x00\x1cftypavif\x00\x00\x00\x00"
M4A = b"\x00\x00\x00\x20ftypM4A \x00\x00\x00\x00"
WEBM = b"\x1a\x45\xdf\xa3" + b"\x00" * 16


def _zip() -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("a.txt", "a")
    return out.getvalue()


def _tar() -> bytes:
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w") as archive:
        info = tarfile.TarInfo("a.txt")
        info.size = 1
        archive.addfile(info, io.BytesIO(b"a"))
    return out.getvalue()


@pytest.mark.parametrize(
    "name, head, kind, mime",
    [
        ("notes.md", b"# Hello", "markdown", "text/markdown"),
        ("main.py", b"print(1)\n", "code", "text/x-python"),
        ("notes.txt", "Čaj o piatej".encode(), "text", "text/plain"),
        ("empty.txt", b"", "text", "text/plain"),
        ("page.html", b"<!doctype html>", "html", "text/html"),
        ("map.png", PNG, "image", "image/png"),
        ("photo.jpg", JPEG, "image", "image/jpeg"),
        ("anim.gif", b"GIF89a" + b"\x00" * 8, "image", "image/gif"),
        ("pic.webp", b"RIFF\x10\x00\x00\x00WEBPVP8 ", "image", "image/webp"),
        ("pic.avif", AVIF, "image", "image/avif"),
        ("logo.svg", b"<svg xmlns='http://www.w3.org/2000/svg'/>", "svg", "image/svg+xml"),
        ("paper.pdf", PDF, "pdf", "application/pdf"),
        ("song.mp3", b"ID3\x04\x00\x00\x00\x00\x00\x00", "audio", "audio/mpeg"),
        ("song.mp3", b"\xff\xfb\x90\x00" + b"\x00" * 8, "audio", "audio/mpeg"),
        ("take.wav", b"RIFF\x24\x00\x00\x00WAVEfmt ", "audio", "audio/wav"),
        ("take.ogg", b"OggS\x00\x02" + b"\x00" * 8, "audio", "audio/ogg"),
        ("take.flac", b"fLaC\x00\x00\x00\x22", "audio", "audio/flac"),
        ("take.m4a", M4A, "audio", "audio/mp4"),
        ("clip.mp4", MP4, "video", "video/mp4"),
        ("clip.webm", WEBM, "video", "video/webm"),
        ("clip.ogv", b"OggS\x00\x02" + b"\x00" * 8, "video", "video/ogg"),
        ("data.csv", b"a,b\n1,2\n", "table", "text/csv"),
        ("data.tsv", b"a\tb\n1\t2\n", "table", "text/tab-separated-values"),
        ("run.ipynb", b'{"cells": []}', "notebook", "application/x-ipynb+json"),
        ("ship.glb", b"glTF\x02\x00\x00\x00", "model3d", "model/gltf-binary"),
        ("ship.gltf", b'{"asset": {"version": "2.0"}}', "model3d", "model/gltf+json"),
        ("ship.obj", b"v 0 0 0\n", "model3d", "model/obj"),
        ("ship.stl", b"solid ship\n", "model3d", "model/stl"),
        ("ship.stl", b"\x00" * 80 + b"\x02\x00\x00\x00", "model3d", "model/stl"),
        ("all.zip", _zip(), "archive", "application/zip"),
        ("all.tar", _tar(), "archive", "application/x-tar"),
        ("all.tar.gz", b"\x1f\x8b\x08\x00" + b"\x00" * 8, "archive", "application/gzip"),
        ("thing.bin", b"\x00\x01\x02\x03", "binary", "application/octet-stream"),
    ],
)
def test_the_kind_of_a_file(name, head, kind, mime):
    found = detect(name, head[: file_kinds.HEAD_BYTES])
    assert (found.kind, found.mime) == (kind, mime)
    assert found.kind in file_kinds.KINDS


def test_the_bytes_outrank_the_name():
    # A picture called a text file is a picture; text called a picture is not one.
    assert detect("notes.txt", PNG).kind == "image"
    # Words called a picture are words, drawn escaped: never an image, never a page.
    for name, head in (("map.png", b"<script>alert(1)</script>"),
                       ("map.png", b"<svg onload='x()'/>"), ("clip.mp4", b"not a film")):
        assert (detect(name, head).kind, detect(name, head).mime) == ("text", "text/plain")
    assert detect("map.png", b"\x00\x01\x02").kind == "binary"
    # An office document is a zip by its bytes and not an archive to list.
    assert detect("letter.docx", _zip()).kind == "binary"
    # Bytes that are not text are never a text kind, whatever the name.
    assert detect("page.html", b"\xff\xfe\x00<").kind == "binary"
    assert detect("notes.md", b"a\x00b").kind == "binary"


def test_svg_is_its_own_kind_and_never_inline():
    svg = detect("logo.svg", b"<svg/>")
    assert svg.kind == "svg" and not svg.inline and svg.language == "xml"
    assert [k for k in file_kinds.KINDS if file_kinds.Kind(k, "").inline] == [
        "image", "audio", "video",
    ]
    assert "svg" not in file_kinds.INLINE and "html" not in file_kinds.INLINE
    assert "pdf" not in file_kinds.INLINE


def test_a_head_cut_inside_a_character_is_still_text():
    text = ("ž" * 400).encode()
    assert file_kinds.looks_like_text(text[:511])
    assert not file_kinds.looks_like_text(b"\xff" + text[:100])


def test_without_bytes_only_a_text_name_says_anything():
    assert detect("notes.md", None).kind == "markdown"
    assert detect("map.png", None).kind == "binary"


def test_a_write_settles_the_kind_and_ignores_the_claimed_type(db, registered):
    user = db.scalars(select(User).order_by(User.created_at)).first()
    home = fs.ensure_home(db, user).path
    v = fs.view(db, user)
    liar = fs.write(v, f"{home}/map.png", b"<script>alert(1)</script>", mime="image/png")
    assert (liar.content_kind, liar.mime) == ("text", "text/plain")
    real = fs.write(v, f"{home}/anything.txt", [PNG[:4], PNG[4:]], mime="text/plain")
    assert (real.content_kind, real.mime) == ("image", "image/png")
    # A write that changes what the file is changes its kind.
    again = fs.write(v, f"{home}/anything.txt", b"words now")
    assert (again.content_kind, again.mime) == ("text", "text/plain")
    # A file from before kinds were kept is read from its bytes when asked.
    again.content_kind = None
    assert fs.kind_of(db, again).kind == "text"
    old = fs.versions(v, again.path)[-1]
    assert fs.kind_of(db, again, old.blob_hash).kind == "image"
