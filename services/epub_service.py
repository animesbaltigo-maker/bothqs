from __future__ import annotations

import asyncio
import hashlib
import html
import re
import zipfile
from io import BytesIO
from pathlib import Path

from config import DISTRIBUTION_TAG, EPUB_CACHE_DIR, EPUB_NAME_PATTERN
from services.media_pipeline import get_document_image_files

EPUB_CACHE_PATH = Path(EPUB_CACHE_DIR)
EPUB_CACHE_PATH.mkdir(parents=True, exist_ok=True)


def _safe_filename(value: str) -> str:
    value = re.sub(r'[<>:"/\\|?*]+', "", str(value or "").strip())
    value = re.sub(r"\s+", " ", value).strip()
    return value or "HQ"


def _book_name(title_name: str, chapter_number: str) -> str:
    base_name = EPUB_NAME_PATTERN.format(
        title=_safe_filename(title_name),
        chapter=_safe_filename(chapter_number),
    )
    if DISTRIBUTION_TAG.lower() not in base_name.lower():
        stem = base_name[:-5] if base_name.lower().endswith(".epub") else base_name
        base_name = f"{stem} - {DISTRIBUTION_TAG}.epub"
    return base_name


def _epub_path(chapter_id: str) -> Path:
    safe = hashlib.sha1(chapter_id.encode("utf-8")).hexdigest()
    return EPUB_CACHE_PATH / f"{safe}.epub"


def _chapter_title(title_name: str, chapter_number: str) -> str:
    return f"{title_name} - Capitulo {chapter_number}"


def _container_xml() -> str:
    return (
        '<?xml version="1.0"?>\n'
        '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
        '  <rootfiles>\n'
        '    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>\n'
        '  </rootfiles>\n'
        '</container>\n'
    )


def _stylesheet() -> str:
    return """
body {
  margin: 0;
  padding: 0;
  font-family: Arial, sans-serif;
  background: #ffffff;
  color: #111111;
}
.title {
  padding: 1.5rem 1rem 1rem 1rem;
  text-align: center;
}
.title h1 {
  margin: 0 0 0.6rem 0;
  font-size: 1.4rem;
}
.title p {
  margin: 0;
  color: #666666;
}
.page {
  margin: 0;
  padding: 0;
  text-align: center;
}
.page img {
  display: block;
  width: 100%;
  height: auto;
}
""".strip()


def _title_page(title_name: str, chapter_number: str) -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<!DOCTYPE html>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" lang="pt-BR">\n'
        "<head>\n"
        f"  <title>{html.escape(_chapter_title(title_name, chapter_number))}</title>\n"
        '  <meta charset="utf-8"/>\n'
        '  <link rel="stylesheet" type="text/css" href="styles.css"/>\n'
        "</head>\n"
        "<body>\n"
        '  <section class="title">\n'
        f"    <h1>{html.escape(title_name)}</h1>\n"
        f"    <p>Capitulo {html.escape(str(chapter_number))}</p>\n"
        f"    <p>{html.escape(DISTRIBUTION_TAG)}</p>\n"
        "  </section>\n"
        "</body>\n"
        "</html>\n"
    )


def _image_page(title: str, image_name: str) -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<!DOCTYPE html>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" lang="pt-BR">\n'
        "<head>\n"
        f"  <title>{html.escape(title)}</title>\n"
        '  <meta charset="utf-8"/>\n'
        '  <link rel="stylesheet" type="text/css" href="styles.css"/>\n'
        "</head>\n"
        "<body>\n"
        '  <figure class="page">\n'
        f'    <img src="images/{html.escape(image_name)}" alt="{html.escape(title)}"/>\n'
        "  </figure>\n"
        "</body>\n"
        "</html>\n"
    )


def _content_opf(
    title_name: str,
    chapter_number: str,
    identifier: str,
    image_entries: list[tuple[str, str]],
) -> str:
    manifest = [
        '    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>',
        '    <item id="styles" href="styles.css" media-type="text/css"/>',
        '    <item id="title" href="title.xhtml" media-type="application/xhtml+xml"/>',
    ]
    spine = ['    <itemref idref="title"/>']

    for index, (image_name, media_type) in enumerate(image_entries, start=1):
        image_id = f"img{index}"
        page_id = f"page{index}"
        manifest.append(f'    <item id="{image_id}" href="images/{image_name}" media-type="{media_type}"/>')
        manifest.append(f'    <item id="{page_id}" href="{page_id}.xhtml" media-type="application/xhtml+xml"/>')
        spine.append(f'    <itemref idref="{page_id}"/>')

    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="bookid">\n'
        '  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
        f'    <dc:title>{html.escape(_chapter_title(title_name, chapter_number))}</dc:title>\n'
        f'    <dc:creator>{html.escape(DISTRIBUTION_TAG)}</dc:creator>\n'
        '    <dc:language>pt-BR</dc:language>\n'
        f'    <dc:identifier id="bookid">{html.escape(identifier)}</dc:identifier>\n'
        '  </metadata>\n'
        '  <manifest>\n'
        f"{chr(10).join(manifest)}\n"
        '  </manifest>\n'
        '  <spine toc="ncx">\n'
        f"{chr(10).join(spine)}\n"
        '  </spine>\n'
        '</package>\n'
    )


def _toc_ncx(title_name: str, chapter_number: str, identifier: str, page_count: int) -> str:
    nav_points = [
        '    <navPoint id="title" playOrder="1">',
        '      <navLabel><text>Inicio</text></navLabel>',
        '      <content src="title.xhtml"/>',
        '    </navPoint>',
    ]

    for index in range(1, page_count + 1):
        nav_points.extend(
            [
                f'    <navPoint id="page{index}" playOrder="{index + 1}">',
                f'      <navLabel><text>Pagina {index}</text></navLabel>',
                f'      <content src="page{index}.xhtml"/>',
                '    </navPoint>',
            ]
        )

    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">\n'
        '  <head>\n'
        f'    <meta name="dtb:uid" content="{html.escape(identifier)}"/>\n'
        '  </head>\n'
        f'  <docTitle><text>{html.escape(_chapter_title(title_name, chapter_number))}</text></docTitle>\n'
        '  <navMap>\n'
        f"{chr(10).join(nav_points)}\n"
        '  </navMap>\n'
        '</ncx>\n'
    )


def _build_epub_bytes(
    title_name: str,
    chapter_number: str,
    chapter_id: str,
    image_files: list[tuple[str, bytes, str]],
) -> bytes:
    identifier = hashlib.sha1(f"{chapter_id}:{chapter_number}".encode("utf-8")).hexdigest()
    image_entries = [(name, media_type) for name, _, media_type in image_files]

    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as epub:
        epub.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        epub.writestr("META-INF/container.xml", _container_xml())
        epub.writestr("OEBPS/styles.css", _stylesheet())
        epub.writestr("OEBPS/title.xhtml", _title_page(title_name, chapter_number))
        epub.writestr("OEBPS/content.opf", _content_opf(title_name, chapter_number, identifier, image_entries))
        epub.writestr("OEBPS/toc.ncx", _toc_ncx(title_name, chapter_number, identifier, len(image_entries)))

        for index, (image_name, image_bytes, _) in enumerate(image_files, start=1):
            epub.writestr(f"OEBPS/images/{image_name}", image_bytes)
            epub.writestr(
                f"OEBPS/page{index}.xhtml",
                _image_page(f"{title_name} - Pagina {index}", image_name),
            )

    return buffer.getvalue()


async def get_or_build_epub(
    chapter_id: str,
    chapter_number: str,
    title_name: str,
    images: list[str],
    progress_cb=None,
) -> tuple[str, str]:
    epub_path = _epub_path(chapter_id)
    epub_name = _book_name(title_name, chapter_number)

    if epub_path.exists():
        return str(epub_path), epub_name

    if progress_cb:
        await progress_cb(1, 3)

    image_files = await get_document_image_files(images, include_banner=True)
    if not image_files:
        raise RuntimeError("Nenhuma imagem encontrada para gerar o EPUB.")

    if progress_cb:
        await progress_cb(2, 3)

    epub_bytes = await asyncio.to_thread(
        _build_epub_bytes,
        title_name,
        chapter_number,
        chapter_id,
        image_files,
    )

    if progress_cb:
        await progress_cb(3, 3)

    await asyncio.to_thread(epub_path.write_bytes, epub_bytes)
    return str(epub_path), epub_name
