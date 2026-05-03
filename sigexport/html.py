import re
import shutil
from html import escape as html_escape
from pathlib import Path

import markdown
from bs4 import BeautifulSoup
from typer import secho

from sigexport import models, templates
from sigexport.logging import log

# Shared constants
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".avif"}

_INDENT_RE = re.compile(r"^(\s*)", re.MULTILINE)




def prep_media_pdf(chat_dir: Path, max_width: int = 600) -> None:
    """Pre-generate a media_pdf/ folder with resized images for fast PDF export.

    Videos and audio are skipped as they cannot appear in PDFs.
    """
    media_dir = chat_dir / "media"
    if not media_dir.exists():
        return

    try:
        from PIL import Image as PilImage
        from PIL import ImageOps
    except ImportError:
        secho(
            "Pillow not installed, skipping media_pdf generation. "
            "Install with: pip install Pillow"
        )
        return

    media_pdf_dir = chat_dir / "media_pdf"
    media_pdf_dir.mkdir(exist_ok=True)

    for img_path in media_dir.iterdir():
        if img_path.suffix.lower() not in IMAGE_EXTS:
            continue
        dest = media_pdf_dir / (img_path.stem + ".jpg")
        if dest.exists():
            continue  # skip if already generated
        try:
            with PilImage.open(img_path) as img:
                # Apply EXIF orientation before resizing so rotation is baked in
                try:
                    img = ImageOps.exif_transpose(img)
                except Exception:
                    pass
                if img.width > max_width:
                    ratio = max_width / img.width
                    new_size = (max_width, int(img.height * ratio))
                    img = img.resize(new_size, PilImage.Resampling.LANCZOS)
                img.convert("RGB").save(dest, "JPEG", quality=85)
        except Exception:
            shutil.copy2(img_path, dest)


def create_cover_page(
    name: str, messages: list[models.Message], for_pdf: bool = False
) -> str:
    """Generate a statistics cover page with chat summary and year-based TOC."""
    from collections import Counter

    safe_name = html_escape(name)

    total_msgs = len(messages)

    # Count messages per sender
    sender_counts = Counter(m.sender for m in messages)

    num_images = sum(
        1
        for m in messages
        for a in m.attachments
        if a.path and any(str(a.path).lower().endswith(ext) for ext in IMAGE_EXTS)
    )

    first_msg = messages[0].date if messages else None
    last_msg = messages[-1].date if messages else None

    # Collect first date per year for TOC links to day-divider anchors
    year_first_date: dict[int, str] = {}
    for m in messages:
        year = m.date.year
        if year not in year_first_date:
            year_first_date[year] = m.date.date().isoformat()

    toc_rows = ""
    for year in sorted(year_first_date):
        date_id = "div-" + year_first_date[year]
        toc_rows += (
            f"<tr><td>{year}</td>"
            f"<td><a href='#{date_id}'>"
            f"First message: {year_first_date[year]}"
            f"</a></td></tr>\n"
        )

    first_str = first_msg.strftime("%Y-%m-%d %H:%M") if first_msg else "N/A"
    last_str = last_msg.strftime("%Y-%m-%d %H:%M") if last_msg else "N/A"

    # Build sender rows sorted by message count (descending)
    sender_rows = ""
    for sender, count in sender_counts.most_common():
        safe_sender = html_escape(sender)
        sender_rows += f"<tr><td>{safe_sender}</td><td>{count}</td></tr>\n"

    # In PDF mode, the TOC hyperlinks don't work across merged chunks,
    # so we replace them with a note pointing to the sidebar bookmarks.
    if for_pdf:
        toc_section = (
            '<h2 class="cover-toc-title">Table of Contents</h2>\n'
            "<p><em>Use the sidebar bookmarks in your PDF viewer to navigate "
            "by year and month.</em></p>"
        )
    else:
        toc_section = f"""
    <h2 class="cover-toc-title">Table of Contents</h2>
    <table class="cover-toc">
        <tr><th>Year</th><th>Link</th></tr>
        {toc_rows}
    </table>"""

    cover = f"""
<div class="cover-page">
    <h1 class="cover-name">{safe_name}</h1>
    <table class="cover-stats">
        <tr><th>Stat</th><th>Value</th></tr>
        <tr><td>Total messages</td><td>{total_msgs}</td></tr>
        <tr><td>Images shared</td><td>{num_images}</td></tr>
        <tr><td>First message</td><td>{first_str}</td></tr>
        <tr><td>Last message</td><td>{last_str}</td></tr>
    </table>
    <h2 class="cover-toc-title">Messages by Sender</h2>
    <table class="cover-stats">
        <tr><th>Sender</th><th>Messages</th></tr>
        {sender_rows}
    </table>
    {toc_section}
</div>
<div style="page-break-after: always"></div>
"""
    return cover


def create_cover_html(name: str, messages: list[models.Message]) -> str:
    """Create a standalone HTML document containing only the cover page.

    Used by the PDF pipeline to generate a single cover for the entire chat,
    separate from the per-chunk message HTML files.
    """
    cover = create_cover_page(name, messages, for_pdf=True)
    ht_text = templates.html.format(
        name=html_escape(name),
        content=cover,
    )
    return ht_text


def create_html(
    name: str,
    messages: list[models.Message],
    include_cover: bool = True,
    for_pdf: bool = False,
) -> str:
    """Create HTML from a list of messages.

    Set include_cover=False to omit the statistics cover page (used when
    generating per-chunk PDFs that will be merged with a shared cover).

    Set for_pdf=True to replace video/audio players with filename references,
    since media players cannot render in PDFs.
    """
    log(f"\tDoing html for {name}")

    ht_content = create_cover_page(name, messages, for_pdf=for_pdf) if include_cover else ""

    # Reuse a single Markdown instance (reset between messages)
    md = markdown.Markdown()

    last_date = None
    for msg in messages:
        sender = msg.sender
        # Normalize to title case for display (Signal often stores ALL CAPS)
        sender_display = sender.title() if sender != "Me" else sender
        date = msg.date.date().isoformat()
        time = msg.date.time().replace(microsecond=0).isoformat()

        # Insert a day-divider whenever the date changes
        if date != last_date:
            ht_content += (
                f'<div class="day-divider" id="div-{date}">{date}</div>\n'
            )
            last_date = date

        reactions = " ".join(f"{r.name}: {r.emoji}" for r in msg.reactions)
        quote = ""
        if msg.quote:
            quote = f'<div class="quote">{html_escape(msg.quote)}</div>'

        body = msg.body
        try:
            body = md.convert(body)
            md.reset()
        except RecursionError:
            log(f"Maximum recursion on message {body}, not converted")

        # Wrap bare URLs that aren't already inside <a> tags.
        # Markdown may have already linked some URLs, so use a negative
        # lookbehind to skip URLs that are already href values.
        body = re.sub(
            r'(?<!href=["\'])(?<!href=)(https?://\S+)',
            r"<a href='\1' target='_blank'>\1</a>",
            body,
        )

        soup = BeautifulSoup(body, "html.parser")
        # Attachments
        for att in msg.attachments:
            path = str(att.path) if att.path else ""
            src = f"./{path}"
            filename = Path(path).name if path else att.name
            if models.is_image(path):
                attachment_html = templates.figure.format(src=src, alt=att.name)
            elif models.is_audio(path):
                if for_pdf:
                    attachment_html = templates.attachment_ref.format(filename=filename)
                else:
                    attachment_html = templates.audio.format(src=src)
            elif models.is_video(path):
                if for_pdf:
                    attachment_html = templates.attachment_ref.format(filename=filename)
                else:
                    attachment_html = templates.video.format(src=src)
            else:
                attachment_html = None
            if attachment_html:
                soup.append(BeautifulSoup(attachment_html, "html.parser"))

        cl = "msg me" if sender == "Me" else "msg"
        ht_content += templates.message.format(
            cl=cl,
            date=date,
            time=time,
            sender=sender_display,
            quote=quote,
            body=soup,
            reactions=reactions,
        )

    css = (Path(__file__).resolve().parent / "style.css").read_text(encoding="utf-8")
    ht_text = templates.html.format(
        name=html_escape(name),
        content=ht_content,
        css=css,
    )
    ht_text = BeautifulSoup(ht_text, "html.parser").prettify()
    ht_text = _INDENT_RE.sub(r"\1\1\1\1", ht_text)
    return ht_text