"""A viewer for every kind of file: what the page is given, and what it is not.

The pages here are read as text. That shows what the server puts into them --
which element draws a kind, that everything out of a file is escaped, the
headers a file's bytes go out under. What a browser then does with it (that a
script in an SVG does not run, that a PDF is drawn) is
`tests/test_file_viewers_browser.py`.
"""

from __future__ import annotations

import base64
import gzip
import io
import json
import tarfile
import zipfile
from urllib.parse import quote

import pytest
from sqlalchemy import select

from lembas.db.models import User
from lembas.security import sealed
from lembas.services import archives, file_ui, fs, notebooks

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" width="40" height="40" onload="window.ran=1">'
    b'<script>window.ran = 1; fetch("/api/v2/me")</script>'
    b'<rect width="40" height="40" fill="green"/></svg>'
)
WAV = b"RIFF" + (36).to_bytes(4, "little") + b"WAVEfmt " + (16).to_bytes(4, "little") + bytes(16) \
    + b"data" + (0).to_bytes(4, "little")
PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
NOTEBOOK = {
    "metadata": {"language_info": {"name": "python"}},
    "cells": [
        {"cell_type": "markdown", "source": ["# Title\n", "<script>window.nb = 1</script>"]},
        {
            "cell_type": "code",
            "execution_count": 3,
            "source": "print('<b>hi</b>')",
            "outputs": [
                {"output_type": "stream", "text": ["<img src=x onerror=alert(1)>\n"]},
                {"output_type": "display_data",
                 "data": {"text/html": "<script>window.nb = 2</script>"}},
                {"output_type": "display_data",
                 "data": {"image/png": base64.b64encode(PNG).decode(), "text/plain": "<Figure>"}},
                {"output_type": "display_data",
                 "data": {"image/png": '"><script>window.nb = 3</script>'}},
                {"output_type": "error", "ename": "ValueError", "evalue": "no",
                 "traceback": ["\x1b[0;31mValueError\x1b[0m: <no>"]},
            ],
        },
    ],
}


def _zip(names: dict[str, bytes]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        for name, data in names.items():
            archive.writestr(name, data)
    return out.getvalue()


def _tar_gz(names: dict[str, bytes]) -> bytes:
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w:gz") as archive:
        for name, data in names.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return out.getvalue()


@pytest.fixture
def home(client, db, registered):
    owner = db.scalars(select(User).order_by(User.created_at)).first()
    path = fs.ensure_home(db, owner).path
    view = fs.view(db, owner)

    def put(name: str, data: bytes) -> str:
        fs.write(view, f"{path}/{name}", data)
        db.commit()
        return f"{path}/{name}"

    return {"path": path, "put": put, "view": view}


def _page(client, path: str, **params) -> str:
    query = "".join(f"&{k}={v}" for k, v in params.items())
    answer = client.get(f"/files/view?path={quote(path)}{query}")
    assert answer.status_code == 200, answer.text[:300]
    return answer.text


# --- Which viewer draws what -------------------------------------------------------------
@pytest.mark.parametrize(
    "name,data,view,kind",
    [
        ("a.png", PNG, "image", "image"),
        ("a.svg", SVG, "svg", "code"),            # a picture to look at, code to edit
        ("a.wav", WAV, "audio", "binary"),
        ("a.pdf", PDF, "pdf", "binary"),
        ("a.csv", b"a,b\n1,2\n", "table", "text"),
        ("a.ipynb", json.dumps(NOTEBOOK).encode(), "notebook", "code"),
        ("a.zip", _zip({"x.txt": b"x"}), "archive", "binary"),
        ("a.glb", b"glTF" + bytes(20), "model3d", "binary"),
        ("a.obj", b"v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n", "model3d", "text"),
        ("a.bin", bytes(range(256)), "binary", "binary"),
        ("a.md", b"# x", "markdown", "markdown"),
    ],
)
def test_a_file_is_drawn_by_the_viewer_of_its_kind(home, name, data, view, kind):
    opened = file_ui.open_file(home["view"], home["put"](name, data))
    assert (opened.view, opened.kind) == (view, kind)


def test_the_name_does_not_choose_the_viewer(home):
    """Text called `.pdf` is text; a PNG called `.svg` is a picture."""
    assert file_ui.open_file(home["view"], home["put"]("x.pdf", b"just words")).view == "text"
    assert file_ui.open_file(home["view"], home["put"]("x.svg", PNG)).view == "image"


# --- An SVG is a picture to an <img>, and a download to everything else ------------------
def test_an_svg_goes_out_as_a_picture_only_to_an_img(client, home):
    path = home["put"]("a.svg", SVG)
    url = f"/files/raw?path={quote(path)}&inline=1"

    drawn = client.get(url, headers={"Sec-Fetch-Dest": "image"})
    assert drawn.headers["content-type"] == "image/svg+xml"
    policy = drawn.headers["content-security-policy"]
    assert "sandbox" in policy and "default-src 'none'" in policy and "script" not in policy
    assert drawn.headers["x-content-type-options"] == "nosniff"
    assert drawn.headers["content-disposition"].startswith("attachment")

    for dest in ("document", "iframe", "empty", "object", "embed", "script", None):
        headers = {"Sec-Fetch-Dest": dest} if dest else {}
        opened = client.get(url, headers=headers)
        assert opened.headers["content-type"] == "application/octet-stream", dest
        assert opened.headers["content-disposition"].startswith("attachment"), dest
        assert "sandbox" in opened.headers["content-security-policy"]

    # Without `inline` it is a download even to an <img>.
    plain = client.get(f"/files/raw?path={quote(path)}", headers={"Sec-Fetch-Dest": "image"})
    assert plain.headers["content-type"] == "application/octet-stream"


def test_an_svg_is_put_into_the_page_as_an_img_and_never_as_markup(client, home):
    page = _page(client, home["put"]("a.svg", SVG))
    # Its source is in the page once, for the editor, and escaped there.
    assert 'onload="window.ran=1"' not in page and "<script>window.ran" not in page
    assert "&lt;script&gt;window.ran" in page
    assert 'class="viewer__image viewer__image--svg"' in page


def test_html_and_a_pdf_are_never_given_out_inline(client, home):
    for name, data in (("a.pdf", PDF), ("a.html", b"<script>1</script>")):
        answer = client.get(f"/files/raw?path={quote(home['put'](name, data))}&inline=1",
                            headers={"Sec-Fetch-Dest": "image"})
        assert answer.headers["content-type"] == "application/octet-stream"


# --- Sound, video, a PDF, a model ---------------------------------------------------------
def test_sound_is_a_player_over_the_raw_route(client, home):
    path = home["put"]("a.wav", WAV)
    page = _page(client, path)
    assert "<audio" in page and f"/files/raw?path={quote(path)}&amp;inline=1" in page
    played = client.get(f"/files/raw?path={quote(path)}&inline=1", headers={"Range": "bytes=0-3"})
    assert played.status_code == 206 and played.headers["content-type"] == "audio/wav"
    assert played.content == b"RIFF"


def test_a_pdf_and_a_model_are_a_sealed_frame_that_holds_no_file(client, home):
    for name, data, kind in (("a.pdf", PDF, "pdf"), ("a.glb", b"glTF" + bytes(20), "model3d")):
        page = _page(client, home["put"](name, data))
        assert f'src="/files/viewer/{kind}" sandbox="allow-scripts"' in page
        assert f'data-sealed-view="{kind}"' in page
    assert 'data-lib-main="/static/vendor/pdf.min.mjs' in _page(client, f"{home['path']}/a.pdf")
    assert 'data-format="glb"' in _page(client, f"{home['path']}/a.glb")

    for kind in ("pdf", "model3d"):
        shell = client.get(f"/files/viewer/{kind}")
        assert shell.status_code == 200
        assert shell.headers["content-security-policy"] == sealed.policy()
        assert "connect-src 'none'" in shell.headers["content-security-policy"]
        assert "allow-same-origin" not in shell.headers["content-security-policy"]
        assert "lembasViewerReady" in shell.text and "glTF" not in shell.text.split("<script>")[0]
    assert client.get("/files/viewer/html").status_code == 404


def test_the_sealed_viewers_scripts_cannot_close_their_own_script_element():
    from lembas.api.tree import SEALED_VIEWERS
    from lembas.web.templating import STATIC_DIR

    for name in SEALED_VIEWERS.values():
        source = (STATIC_DIR / "js" / name).read_text(encoding="utf-8")
        assert "</script" not in source and "innerHTML" not in source
        assert "insertAdjacentHTML" not in source and "document.write" not in source


def test_a_file_too_large_for_a_sealed_viewer_is_a_download(client, home, monkeypatch):
    from lembas.api import tree

    monkeypatch.setattr(tree, "SEALED_MAX", 10)
    page = _page(client, home["put"]("a.pdf", PDF))
    assert "data-sealed-view" not in page and 'data-testid="viewer-card"' in page


# --- A table -----------------------------------------------------------------------------
def test_a_table_is_its_text_escaped_for_the_script_to_lay_out(client, home):
    page = _page(client, home["put"]("a.tsv", b"name\tnote\n<b>x</b>\t<script>1</script>\n"),
                 fragment=1)
    assert 'data-table="tab"' in page and "data-table-toggle" in page
    assert "&lt;script&gt;1&lt;/script&gt;" in page and "<script>1" not in page


def test_the_table_script_builds_with_text_and_caps_what_it_lays_out():
    from tests.conftest import js_function, script

    source = script("files.js")
    body = js_function(source, "tables")
    assert "textContent" in body and "innerHTML" not in body
    assert "TABLE_ROWS" in js_function(source, "parseTable")


# --- A notebook ---------------------------------------------------------------------------
def test_a_notebook_is_read_as_data_and_its_html_is_never_drawn():
    book = notebooks.parse(json.dumps(NOTEBOOK))
    assert [c.kind for c in book.cells] == ["markdown", "code"] and book.language == "python"
    outputs = book.cells[1].outputs
    assert [o.kind for o in outputs] == ["text", "skipped", "image", "skipped", "error"]
    assert outputs[1].what == "text/html"
    assert outputs[2].src.startswith("data:image/png;base64,iVBOR")
    assert "\x1b" not in outputs[4].text and "ValueError" in outputs[4].text


def test_a_notebook_page_escapes_everything(client, home):
    page = _page(client, home["put"]("a.ipynb", json.dumps(NOTEBOOK).encode()), fragment=1)
    assert 'data-testid="viewer-notebook"' in page
    assert "<script>window.nb" not in page and "onerror=alert" not in page.replace(
        "&lt;img src=x onerror=alert(1)&gt;", "")
    assert "&lt;script&gt;window.nb = 1&lt;/script&gt;" in page   # the Markdown cell, as text
    assert "&lt;b&gt;hi&lt;/b&gt;" in page
    assert page.count("<img ") == 1 and 'src="data:image/png;base64,' in page
    assert "text/html" in page  # the HTML output is named, not drawn


def test_a_notebook_that_is_not_one_is_shown_as_its_text(client, home):
    assert notebooks.parse("{not json").problem and notebooks.parse("[1]").problem
    page = _page(client, home["put"]("b.ipynb", b"{not json <script>1</script>"), fragment=1)
    assert "viewer-notebook" not in page and "&lt;script&gt;1" in page


def test_a_notebook_is_capped():
    big = {"cell_type": "code", "source": "x" * 100_000, "outputs": [
        {"output_type": "stream", "text": "y" * 100_000}, *[
            {"output_type": "stream", "text": "z"} for _ in range(49)]]}
    small = {"cell_type": "code", "source": "1", "outputs": []}
    book = notebooks.parse(json.dumps({"cells": [big, *[small] * 599]}))
    assert len(book.cells) == notebooks.MAX_CELLS and book.more == 100
    assert book.cells[0].cut and len(book.cells[0].source) == notebooks.MAX_SOURCE_CHARS
    assert len(book.cells[0].outputs) == notebooks.MAX_OUTPUTS
    assert len(book.cells[0].outputs[0].text) == notebooks.MAX_OUTPUT_CHARS


def test_only_raster_pictures_are_drawn_from_a_notebook():
    svg = {"cells": [{"cell_type": "code", "source": "", "outputs": [
        {"output_type": "display_data", "data": {"image/svg+xml": "<svg onload=alert(1)/>"}}]}]}
    out = notebooks.parse(json.dumps(svg)).cells[0].outputs
    assert [(o.kind, o.what) for o in out] == [("skipped", "image/svg+xml")]


# --- An archive ---------------------------------------------------------------------------
def _listing(db, home, name: str, data: bytes) -> archives.Listing:
    opened = file_ui.open_file(home["view"], home["put"](name, data))
    return archives.listing(db, opened.sha, opened.size, opened.mime, opened.name)


def test_a_zip_and_a_tar_are_listed(db, home):
    zipped = _listing(db, home, "a.zip", _zip({"docs/a.txt": b"hello", "b.bin": bytes(10)}))
    assert [(e.name, e.size) for e in zipped.entries] == [("docs/a.txt", 5), ("b.bin", 10)]
    assert zipped.total == 2 and not zipped.cut and not zipped.problem
    tarred = _listing(db, home, "a.tar.gz", _tar_gz({"x/y.txt": b"abc"}))
    assert [(e.name, e.size) for e in tarred.entries] == [("x/y.txt", 3)]


def test_a_listing_is_capped_by_entries(db, home, monkeypatch):
    monkeypatch.setattr(archives, "MAX_ENTRIES", 3)
    names = {f"f{i}.txt": b"" for i in range(10)}
    for found in (_listing(db, home, "a.zip", _zip(names)),
                  _listing(db, home, "a.tgz", _tar_gz(names))):
        assert len(found.entries) == 3 and found.cut


def test_a_listing_stops_at_what_it_may_unpack(db, home, monkeypatch):
    """A small file that unpacks to a great deal is passed over only so far."""
    monkeypatch.setattr(archives, "MAX_UNPACKED", 1024 * 1024)
    bomb = _tar_gz({"zeros": bytes(8 * 1024 * 1024), "after": b"x"})
    assert len(bomb) < 100_000
    found = _listing(db, home, "bomb.tar.gz", bomb)
    assert found.cut and [e.name for e in found.entries] == ["zeros"]


def test_a_zip_with_a_directory_too_large_is_not_read(db, home, monkeypatch):
    monkeypatch.setattr(archives, "MAX_DIRECTORY_ENTRIES", 2)
    found = _listing(db, home, "a.zip", _zip({f"f{i}": b"" for i in range(5)}))
    assert found.entries == [] and found.cut and found.total == 5


def test_what_cannot_be_listed_says_so(db, home):
    assert _listing(db, home, "bad.zip", b"PK\x03\x04" + bytes(40)).problem == "unreadable"
    assert _listing(db, home, "a.7z", b"7z\xbc\xaf\x27\x1c" + bytes(40)).problem == "unsupported"
    single = _listing(db, home, "notes.txt.gz", gzip.compress(b"just one file"))
    assert [e.name for e in single.entries] == ["notes.txt"]


def test_an_archive_page_lists_names_escaped_and_extracts_nothing(client, db, home):
    path = home["put"]("a.zip", _zip({"<img src=x onerror=alert(1)>.txt": b"secret words"}))
    page = _page(client, path)
    assert 'data-testid="viewer-archive"' in page
    assert "&lt;img src=x onerror=alert(1)&gt;.txt" in page and "<img src=x" not in page
    assert "secret words" not in page


# --- Everything else ----------------------------------------------------------------------
def test_a_binary_says_its_size_and_type_and_offers_the_download(client, home):
    path = home["put"]("a.bin", bytes(range(256)) * 8)
    page = _page(client, path)
    assert 'data-testid="viewer-card"' in page and "application/octet-stream" in page
    assert "2.0 KB" in page and f'href="/files/raw?path={quote(path)}" download' in page


def test_the_show_dialog_draws_by_kind_too(client, db, home, make_chat):
    from lembas.db.models import Node

    chat_id = make_chat()
    path = home["put"]("a.csv", b"a,b\n1,2\n")
    node = db.scalar(select(Node).where(Node.path == path))
    answer = client.get(f"/chat/{chat_id}/show?node={node.id}", headers={"HX-Request": "true"})
    assert answer.status_code == 200 and 'data-testid="viewer-table"' in answer.text


def test_a_file_is_renamed_from_its_own_page(client, home):
    path = home["put"]("old.csv", b"a,b\n")
    page = _page(client, path)
    assert 'data-testid="file-rename"' in page
    answer = client.post("/files/rename", data={"path": path, "name": "new.csv", "open": "1"},
                         headers={"HX-Request": "true"})
    assert answer.status_code == 204
    there = answer.headers["HX-Redirect"]
    assert there.startswith(f"/files/view?path={quote(home['path'] + '/new.csv')}")
    assert client.get(there).status_code == 200
    # Without `open` it is the explorer's rename, back on the folder.
    again = client.post("/files/rename", data={"path": f"{home['path']}/new.csv", "name": "x.csv"},
                        follow_redirects=False)
    assert again.status_code == 303 and "/files?path=" in again.headers["location"]


# --- Review fixes: an archive's own numbers are capped where they are used ------------------
def _with_zip64_record(data: bytes, *, classic_count: int) -> bytes:
    """`data` (a zip) with a zip64 end record and its locator put in front of
    the classic end record, which is made to claim `classic_count` entries.
    A reader that goes by the classic record sees a small archive; one that
    honours the locator sees what the zip64 record says."""
    import struct

    at = data.rfind(b"PK\x05\x06")
    count, size, offset = struct.unpack("<HII", data[at + 10:at + 20])
    record = struct.pack("<4sQHHIIQQQQ", b"PK\x06\x06", 44, 45, 45, 0, 0, count, count,
                         size, offset)
    locator = struct.pack("<4sIQI", b"PK\x06\x07", 0, at, 1)
    classic = bytearray(data[at:])
    classic[8:12] = struct.pack("<HH", classic_count, classic_count)
    return data[:at] + record + locator + bytes(classic)


def test_a_zip_whose_two_end_records_disagree_is_capped_by_the_one_that_is_read(
    db, home, monkeypatch
):
    """The classic end record says one entry; the zip64 one in front of it
    says two hundred. The cap is applied to the number the listing is made
    from -- whichever record that is -- and not to the other."""
    names = {f"f{i:03}.txt": b"" for i in range(200)}
    crafted = _with_zip64_record(_zip(names), classic_count=1)
    monkeypatch.setattr(archives, "MAX_DIRECTORY_ENTRIES", 50)
    found = _listing(db, home, "crafted.zip", crafted)
    assert found.entries == [] and found.cut and found.total == 200
    # With room for it, the zip64 record is simply read.
    monkeypatch.setattr(archives, "MAX_DIRECTORY_ENTRIES", 100_000)
    found = _listing(db, home, "crafted2.zip", crafted)
    assert found.total == 200 and len(found.entries) == 200 and not found.cut
    assert found.entries[0].name == "f000.txt" and found.entries[-1].name == "f199.txt"


def test_a_zip_is_listed_without_the_library_that_reads_a_whole_directory(db, home, monkeypatch):
    def never(*_a, **_kw):
        raise AssertionError("zipfile was asked to read the archive")

    data = _zip({"docs/": b"", "docs/a.txt": b"hello", "ž.txt": b"12"})
    monkeypatch.setattr(archives.zipfile, "ZipFile", never)
    found = _listing(db, home, "a.zip", data)
    assert [(e.name, e.size, e.is_dir) for e in found.entries] == [
        ("docs/", None, True), ("docs/a.txt", 5, False), ("ž.txt", 2, False)]


def test_only_the_first_part_of_a_zips_directory_is_read(db, home, monkeypatch):
    monkeypatch.setattr(archives, "MAX_DIRECTORY", 46 * 3 + 30)
    found = _listing(db, home, "a.zip", _zip({f"f{i}.txt": b"" for i in range(40)}))
    assert 1 <= len(found.entries) < 40 and found.cut and found.total == 40


def test_a_tar_header_that_declares_a_huge_name_is_not_read_into_memory(db, home, monkeypatch):
    """A GNU long-name header says how long the name after it is, and the
    library reads that much at once. Eight megabytes of "name" are refused."""
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w", format=tarfile.GNU_FORMAT) as archive:
        info = tarfile.TarInfo("././@LongLink")
        info.type = tarfile.GNUTYPE_LONGNAME
        info.size = 8 * 1024 * 1024
        archive.addfile(info, io.BytesIO(b"a" * info.size))
        after = tarfile.TarInfo("after.txt")
        after.size = 1
        archive.addfile(after, io.BytesIO(b"x"))
    reads: list[int] = []
    real = archives._Counted.readinto

    def counted(self, buffer):
        got = real(self, buffer)
        reads.append(got)
        return got

    monkeypatch.setattr(archives._Counted, "readinto", counted)
    found = _listing(db, home, "long.tar", out.getvalue())
    assert found.entries == [] and found.cut
    assert sum(reads) < 4 * 1024 * 1024  # stopped at the header, not after its 8 MB
    # An ordinary long name still works.
    out = io.BytesIO()
    long_name = "d/" + "n" * 300 + ".txt"
    with tarfile.open(fileobj=out, mode="w", format=tarfile.GNU_FORMAT) as archive:
        info = tarfile.TarInfo(long_name)
        info.size = 1
        archive.addfile(info, io.BytesIO(b"x"))
    assert [e.name for e in _listing(db, home, "ok.tar", out.getvalue()).entries] == [long_name]


def test_only_so_many_archives_are_read_at_once(db, home, monkeypatch):
    monkeypatch.setattr(archives, "WAIT_SECONDS", 0.05)
    held = [archives._turns.acquire(timeout=1) for _ in range(archives.MAX_AT_ONCE)]
    try:
        assert all(held)
        assert _listing(db, home, "a.zip", _zip({"a": b""})).problem == "busy"
    finally:
        for _ in held:
            archives._turns.release()
    assert not _listing(db, home, "b.zip", _zip({"a": b""})).problem
