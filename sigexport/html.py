import re
import shutil
import sys
from html import escape as html_escape
from pathlib import Path

import markdown
from bs4 import BeautifulSoup
from typer import secho

from sigexport import models, templates
from sigexport.logging import log

# Shared constants
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".avif"}

# Pre-compiled regexes (avoid recompiling per message / per call)
_URL_RE = re.compile(r"(https?://\S+)")
_INDENT_RE = re.compile(r"^(\s*)", re.MULTILINE)

# Sentinel for disabling pagination
NO_PAGINATION = sys.maxsize


def prep_html(dest: Path) -> None:
    """Copy style.css to the root export directory.

    dest can be either the root export dir (main export) or a chat subfolder
    (regenerate-html).  We always copy to the root export dir so all chats
    share one style.css via ../style.css.
    """
    root = Path(__file__).resolve().parents[0]
    css_source = root / "style.css"
    # If dest contains data.json it's a chat folder, so go up one level
    # Otherwise dest is already the root export directory
    if (dest / "data.json").exists():
        css_dest = dest.parent / "style.css"
    else:
        css_dest = dest / "style.css"
    if css_source.is_file():
        shutil.copy2(css_source, css_dest)
    else:
        secho(
            f"Stylesheet ({css_source}) not found. "
            f"You might want to install one manually at {css_dest}."
        )


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
                    img = img.resize(new_size, PilImage.LANCZOS)
                img.convert("RGB").save(dest, "JPEG", quality=85)
        except Exception:
            shutil.copy2(img_path, dest)


def create_cover_page(name: str, messages: list[models.Message]) -> str:
    """Generate a statistics cover page with chat summary and year-based TOC."""
    safe_name = html_escape(name)

    total_msgs = len(messages)
    my_msgs = sum(1 for m in messages if m.sender == "Me")
    their_msgs = total_msgs - my_msgs

    num_images = sum(
        1
        for m in messages
        for a in m.attachments
        if a.path and any(str(a.path).lower().endswith(ext) for ext in IMAGE_EXTS)
    )

    first_msg = messages[0].date if messages else None
    last_msg = messages[-1].date if messages else None

    # Collect last date per year for TOC links to day-divider anchors
    year_last_date: dict[int, str] = {}
    for m in messages:
        year = m.date.year
        year_last_date[year] = m.date.date().isoformat()

    toc_rows = ""
    for year in sorted(year_last_date):
        date_id = "div-" + year_last_date[year]
        toc_rows += (
            f"<tr><td>{year}</td>"
            f"<td><a href='#{date_id}'>"
            f"Jump to last message of {year} ({year_last_date[year]})"
            f"</a></td></tr>\n"
        )

    first_str = first_msg.strftime("%Y-%m-%d %H:%M") if first_msg else "N/A"
    last_str = last_msg.strftime("%Y-%m-%d %H:%M") if last_msg else "N/A"

    cover = f"""
<div class="cover-page">
    <h1 class="cover-name">{safe_name}</h1>
    <table class="cover-stats">
        <tr><th>Stat</th><th>Value</th></tr>
        <tr><td>Total messages</td><td>{total_msgs}</td></tr>
        <tr><td>Messages from me</td><td>{my_msgs}</td></tr>
        <tr><td>Messages from {safe_name}</td><td>{their_msgs}</td></tr>
        <tr><td>Images shared</td><td>{num_images}</td></tr>
        <tr><td>First message</td><td>{first_str}</td></tr>
        <tr><td>Last message</td><td>{last_str}</td></tr>
    </table>
    <h2 class="cover-toc-title">Table of Contents</h2>
    <table class="cover-toc">
        <tr><th>Year</th><th>Link</th></tr>
        {toc_rows}
    </table>
</div>
<div style="page-break-after: always"></div>
"""
    return cover


def create_html(
    name: str, messages: list[models.Message], msgs_per_page: int = 100
) -> str:
    """Create paginated HTML from a list of messages."""
    log(f"\tDoing html for {name}")

    ht_content = create_cover_page(name, messages)
    last_page = max(0, (len(messages) - 1) // msgs_per_page) if messages else 0

    # Reuse a single Markdown instance (reset between messages)
    md = markdown.Markdown()

    page_num = 0
    last_date = None
    for i, msg in enumerate(messages):
        if i % msgs_per_page == 0:
            nav = "\n"
            if i > 0:
                nav += "</div>"
            nav += f'<div class="page" id="pg{page_num}">'
            nav += "<nav>"
            nav += '<div class="prev">'
            if page_num != 0:
                nav += f'<a href="#pg{page_num - 1}">PREV</a>'
            else:
                nav += "PREV"
            nav += '</div><div class="next">'
            if page_num != last_page:
                nav += f'<a href="#pg{page_num + 1}">NEXT</a>'
            else:
                nav += "NEXT"
            nav += "</div></nav>\n"
            ht_content += nav
            page_num += 1

        sender = msg.sender
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
            if models.is_image(path):
                temp = templates.figure.format(src=src, alt=att.name)
            elif models.is_audio(path):
                temp = templates.audio.format(src=src)
            elif models.is_video(path):
                temp = templates.video.format(src=src)
            else:
                temp = None
            if temp:
                soup.append(BeautifulSoup(temp, "html.parser"))

        cl = "msg me" if sender == "Me" else "msg"
        ht_content += templates.message.format(
            cl=cl,
            date=date,
            time=time,
            sender=sender,
            quote=quote,
            body=soup,
            reactions=reactions,
        )

    # Close the last page div
    if messages:
        ht_content += "</div>"

    ht_text = templates.html.format(
        name=html_escape(name),
        content=ht_content,
    )
    ht_text = BeautifulSoup(ht_text, "html.parser").prettify()
    ht_text = _INDENT_RE.sub(r"\1\1\1\1", ht_text)
    return ht_text