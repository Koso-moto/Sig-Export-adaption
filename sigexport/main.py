"""Main script for sigexport."""

import json
import shutil
import subprocess
import sys
import traceback
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path
from typing import Optional

from typer import Argument, Context, Exit, Option, Typer, colors, run, secho

app = Typer(
    help=(
        "sigexport — export Signal chats to markdown, HTML and PDF.\n\n"
        "To export from Signal database, run:\n\n"
        "    sigexport ~/outputdir\n\n"
        "For full export options run:\n\n"
        "    sigexport main --help"
    )
)

from sigexport import create, data, files, html, logging, merge, models, utils
from sigexport.export_channel_metadata import export_channel_metadata
from sigexport.html import IMAGE_EXTS, NO_PAGINATION

OptionalPath = Optional[Path]
OptionalStr = Optional[str]

# Hide-images CSS snippet injected for --no-images PDF generation
_HIDE_MEDIA_CSS = (
    "<style>figure, video, audio { display: none !important; }</style></head>"
)

# Threshold above which large chats are split into chunks for PDF generation
_PDF_CHUNK_THRESHOLD = 5000
_PDF_CHUNK_SIZE = 1000


# ──────────────────────────────────────────────────────────────────────
# Shared helpers
# ──────────────────────────────────────────────────────────────────────

def load_messages_from_json(data_json: Path) -> list[models.Message]:
    """Parse a data.json (one JSON object per line) into Message objects.

    Handles both dict-style and bare-string attachment entries for
    backwards compatibility with older exports.
    """
    messages: list[models.Message] = []
    with open(data_json, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)

            attachments = []
            for att in d.get("attachments", []):
                if isinstance(att, dict):
                    raw_path = att.get("path", "")
                    attachments.append(
                        models.Attachment(
                            name=str(att.get("name", "")),
                            path=Path(str(raw_path)) if raw_path else Path(""),
                        )
                    )
                elif att:
                    attachments.append(
                        models.Attachment(
                            name=str(att),
                            path=Path(str(att)),
                        )
                    )

            reactions = []
            for r in d.get("reactions", []):
                if isinstance(r, dict):
                    reactions.append(
                        models.Reaction(
                            name=r.get("name", ""),
                            emoji=r.get("emoji", ""),
                        )
                    )

            messages.append(
                models.Message(
                    date=datetime.fromisoformat(d["date"]),
                    sender=d.get("sender", ""),
                    body=d.get("body", "") or "",
                    quote=d.get("quote", "") or "",
                    sticker=d.get("sticker", "") or "",
                    reactions=reactions,
                    attachments=attachments,
                )
            )
    return messages


def _find_chrome() -> str | None:
    """Detect Chrome/Chromium binary across macOS, Linux, Windows."""
    candidates = [
        # macOS
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        # Linux
        "google-chrome",
        "google-chrome-stable",
        "chromium",
        "chromium-browser",
        # Windows
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ]
    for candidate in candidates:
        if "/" in candidate or "\\" in candidate:
            if Path(candidate).exists():
                return candidate
        else:
            found = shutil.which(candidate)
            if found:
                return found
    return None


def _chrome_to_pdf(
    chrome_bin: str, html_path: Path, pdf_path: Path
) -> tuple[bool, str]:
    """Run headless Chrome to convert one HTML file to PDF.

    Uses pre-generated media_pdf/ folder with resized images when available.
    """
    chat_dir = html_path.parent
    media_dir = chat_dir / "media"
    media_pdf_dir = chat_dir / "media_pdf"
    tmp_html = chat_dir / (html_path.stem + "_tmp.html")

    try:
        html_text = html_path.read_text(encoding="utf-8")

        if media_pdf_dir.exists():
            # Replace ./media/ references with media_pdf/ absolute paths
            html_text = html_text.replace(
                '"./media/', f'"{media_pdf_dir.as_posix()}/'
            ).replace("'./media/", f"'{media_pdf_dir.as_posix()}/")
            # Rewrite image extensions to .jpg (prep_media_pdf saves as JPEG)
            for ext in IMAGE_EXTS - {".jpg"}:
                html_text = html_text.replace(
                    ext + '"', '.jpg"'
                ).replace(ext + "'", ".jpg'")
        elif media_dir.exists():
            secho(
                "\n    media_pdf/ not found, run regenerate-html first "
                "for best results",
                fg=colors.YELLOW,
            )

        tmp_html.write_text(html_text, encoding="utf-8")
        result = subprocess.run(
            [
                chrome_bin,
                "--headless",
                "--disable-gpu",
                "--no-pdf-header-footer",
                f"--print-to-pdf={pdf_path}",
                tmp_html.as_uri(),
            ],
            capture_output=True,
            timeout=1200,
        )
        if result.returncode == 0:
            return True, str(pdf_path)
        else:
            return False, f"exit code {result.returncode}"
    except subprocess.TimeoutExpired:
        return False, "timed out"
    except Exception as e:
        return False, str(e)
    finally:
        tmp_html.unlink(missing_ok=True)


def _merge_pdfs(pdf_paths: list[Path], output_path: Path) -> None:
    """Merge multiple PDFs into one using pypdf.

    PDFs are merged in the order given (not sorted), so the caller
    controls the page order (e.g. cover page first, then chunks).
    """
    from pypdf import PdfWriter

    writer = PdfWriter()
    for pdf_path in pdf_paths:
        writer.append(str(pdf_path))
    with open(output_path, "wb") as f:
        writer.write(f)


def _add_toc_links(
    pdf_path: Path,
    messages: list[models.Message],
) -> None:
    """Post-process a merged PDF to add sidebar bookmarks by year and month.

    Scans every page for day-divider date strings to find which page each
    year/month's first message lands on, then adds nested PDF outline entries:

        2021
            January
            February
            ...
        2022
            January
            ...
    """
    import calendar

    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import Fit

    reader = PdfReader(str(pdf_path))
    num_pages = len(reader.pages)
    if num_pages < 2:
        return

    # 1) Collect first date per year and per (year, month) from messages
    year_first_date: dict[int, str] = {}
    month_first_date: dict[tuple[int, int], str] = {}
    for m in messages:
        year = m.date.year
        month = m.date.month
        date_str = m.date.date().isoformat()
        if year not in year_first_date:
            year_first_date[year] = date_str
        if (year, month) not in month_first_date:
            month_first_date[(year, month)] = date_str

    # 2) Scan pages to find which page contains each first-date string
    #    Build maps: date_str → first page it appears on
    date_to_page: dict[str, int] = {}
    all_dates = set(year_first_date.values()) | set(month_first_date.values())
    for page_idx in range(num_pages):
        page_text = reader.pages[page_idx].extract_text() or ""
        for date_str in list(all_dates):
            if date_str in page_text and date_str not in date_to_page:
                date_to_page[date_str] = page_idx
                all_dates.discard(date_str)
        if not all_dates:
            break  # found all dates

    if not date_to_page:
        return

    # 3) Build the output PDF with nested outline bookmarks
    writer = PdfWriter()
    writer.append(reader)

    for year in sorted(year_first_date):
        date_str = year_first_date[year]
        page_idx = date_to_page.get(date_str)
        if page_idx is None:
            continue

        # Add year bookmark (top level)
        year_bookmark = writer.add_outline_item(
            title=str(year),
            page_number=page_idx,
            fit=Fit.fit_horizontally(top=None),
        )

        # Add month bookmarks (nested under year)
        for month in range(1, 13):
            key = (year, month)
            if key not in month_first_date:
                continue
            m_date_str = month_first_date[key]
            m_page_idx = date_to_page.get(m_date_str)
            if m_page_idx is None:
                continue

            writer.add_outline_item(
                title=calendar.month_name[month],
                page_number=m_page_idx,
                parent=year_bookmark,
                fit=Fit.fit_horizontally(top=None),
            )

    # 4) Write the updated PDF
    with open(pdf_path, "wb") as f:
        writer.write(f)


# ──────────────────────────────────────────────────────────────────────
# CLI commands
# ──────────────────────────────────────────────────────────────────────


@app.command()
def main(
    ctx: Context,
    dest: Path = Argument(None),
    source: OptionalPath = Option(None, help="Path to Signal source directory"),
    old: OptionalPath = Option(None, help="Path to previous export to merge"),
    password: OptionalStr = Option(
        None, help="Linux-only. Password to decrypt DB key"
    ),
    db_key: OptionalStr = Option(
        None,
        "--key",
        help="Linux-only. DB key, as found in the old config.json",
    ),
    paginate: int = Option(
        100,
        "--paginate",
        "-p",
        help="Messages per page in HTML; set to 0 for infinite",
    ),
    chats: str = Option(
        "",
        help="Comma-separated chat names to include: contact names or group names",
    ),
    json_output: bool = Option(
        True, "--json/--no-json", "-j", help="Whether to create JSON output"
    ),
    html_output: bool = Option(
        True, "--html/--no-html", "-h", help="Whether to create HTML output"
    ),
    list_chats: bool = Option(
        False, "--list-chats", "-l", help="List available chats and exit"
    ),
    include_empty: bool = Option(
        False, "--include-empty", help="Whether to include empty chats"
    ),
    include_disappearing: bool = Option(
        False,
        "--include-disappearing",
        help="Whether to include disappearing messages",
    ),
    start_date: Optional[str] = Option(
        None,
        "--start",
        help="Start date as ISO-8601 (e.g., 2025-01-15T12:30:00+02:00)",
    ),
    end_date: Optional[str] = Option(
        None,
        "--end",
        help="End date as ISO-8601 (e.g., 2025-03-15T12:30:00+02:00)",
    ),
    overwrite: bool = Option(
        False,
        "--overwrite/--no-overwrite",
        help="Overwrite contents of output directory if it exists",
    ),
    verbose: bool = Option(False, "--verbose", "-v"),
    channel_members_only: bool = Option(
        False,
        "--chat-members",
        help=(
            "Export membership information for all chats "
            "(or for a subset of chats given by the --chats option)"
        ),
    ),
    attachments: bool = Option(
        True, "--attachments/--no-attachments", help="Whether to copy attachments"
    ),
    _: bool = Option(False, "--version", callback=utils.version_callback),
) -> None:
    """
    Read the Signal directory and output attachments and chat to DEST directory.

    Example to list chats:

        sigexport --list-chats

    Example to export all to a directory:

        sigexport ~/outputdir

    Example to export messages within a specific date range:

        sigexport ~/outputdir --start 2025-01-15T12:30:00+02:00 --end 2025-03-15T12:30:00+02:00
    """
    logging.verbose = verbose

    if not any((dest, list_chats)):
        secho(ctx.get_help())
        raise Exit(code=1)

    if source:
        source_dir = Path(source).expanduser().absolute()
    else:
        source_dir = utils.source_location()
    if not (source_dir / "config.json").is_file():
        secho(f"Error: config.json not found in directory {source_dir}")
        raise Exit(code=1)

    parsed_start_date = parse_input_dt(start_date) if start_date else None
    parsed_end_date = parse_input_dt(end_date) if end_date else None

    convos, contacts, owner = data.fetch_data(
        source_dir,
        password=password,
        key=db_key,
        chats=chats,
        include_empty=include_empty,
        include_disappearing=include_disappearing,
        start_date=parsed_start_date,
        end_date=parsed_end_date,
    )

    if list_chats:
        names = sorted(v.name for v in contacts.values() if v.name is not None)
        secho(" | ".join(names))
        raise Exit()

    if channel_members_only:
        export_channel_metadata(
            dest, contacts, owner, chats.split(",") if chats else None
        )
        raise Exit()

    dest = Path(dest).expanduser()
    if not dest.is_dir():
        dest.mkdir(parents=True, exist_ok=True)
    elif overwrite:
        shutil.rmtree(dest)
        dest.mkdir(parents=True, exist_ok=True)
    else:
        secho(
            f"Output folder '{dest}' already exists, didn't do anything!",
            fg=colors.RED,
        )
        raise Exit()

    contacts = utils.fix_names(contacts)

    if attachments:
        secho("Copying and renaming attachments")
        files.copy_attachments(source_dir, dest, convos, contacts, password, db_key)

    if json_output and old:
        secho(
            "Warning: currently, JSON does not support merging with the --old flag",
            fg=colors.RED,
        )

    secho("Creating output files")
    chat_dict = create.create_chats(convos, contacts)

    if old:
        secho(f"Merging old at {old} into output directory")
        secho("No existing files will be deleted or overwritten!")
        chat_dict = merge.merge_with_old(chat_dict, contacts, dest, Path(old))

    if paginate <= 0:
        paginate = NO_PAGINATION

    if html_output:
        html.prep_html(dest)

    for contact_id, messages in chat_dict.items():
        name = contacts[contact_id].name or "None"

        (dest / name).mkdir(parents=True, exist_ok=True)
        md_path = dest / name / "chat.md"
        js_path = dest / name / "data.json"
        ht_path = dest / name / f"{name}.html"

        with ExitStack() as stack:
            md_f = stack.enter_context(md_path.open("a", encoding="utf-8"))
            js_f = (
                stack.enter_context(js_path.open("a", encoding="utf-8"))
                if json_output
                else None
            )
            ht_f = (
                stack.enter_context(ht_path.open("w", encoding="utf-8"))
                if html_output
                else None
            )

            for msg in messages:
                print(msg.to_md(), file=md_f)
                if js_f:
                    print(msg.dict_str(), file=js_f)
            if ht_f:
                ht = html.create_html(
                    name=name, messages=messages, msgs_per_page=paginate
                )
                print(ht, file=ht_f)

        if html_output:
            html.prep_media_pdf(dest / name)

    secho("Done!", fg=colors.GREEN)


def parse_input_dt(dt_string: str) -> datetime:
    """Parse an ISO-formatted datetime string, adding local timezone if needed."""
    try:
        dt = datetime.fromisoformat(dt_string)
    except ValueError:
        secho(
            f"Invalid datetime, you entered '{dt_string}'. "
            "Must match ISO format, e.g. '2025-06-01' or "
            "'2025-08-10T12:15:00Z'",
            fg=colors.RED,
        )
        raise

    if dt.tzinfo is None:
        local_tz = datetime.now().astimezone().tzinfo
        dt = dt.replace(tzinfo=local_tz)

    return dt


@app.command(name="regenerate-html")
def regenerate_html(
    chats_dir: Path = Argument(
        ..., help="Path to your signal-chats export directory"
    ),
    chat: str = Option(
        "",
        "--chat",
        "-c",
        help="Name of a single chat folder to regenerate (leave empty for all)",
    ),
    paginate: int = Option(
        100,
        "--paginate",
        "-p",
        help="Messages per page in HTML; set to 0 for infinite",
    ),
    verbose: bool = Option(False, "--verbose", "-v"),
) -> None:
    """Regenerate {name}.html files from existing data.json files without re-exporting from Signal."""
    logging.verbose = verbose
    chats_dir = chats_dir.expanduser().resolve()

    if paginate <= 0:
        paginate = NO_PAGINATION

    if chat:
        chat_path = chats_dir / chat
        if not chat_path.is_dir():
            secho(
                f"Error: Chat folder '{chat}' not found in {chats_dir}",
                fg=colors.RED,
            )
            raise Exit(code=1)
        data_jsons = [chat_path / "data.json"]
        secho(f"Regenerating HTML for: {chat}")
    else:
        data_jsons = sorted(chats_dir.rglob("data.json"))
        secho(f"Scanning: {chats_dir}")

    found = 0
    errors = 0
    for data_json in data_jsons:
        chat_dir = data_json.parent
        chat_name = chat_dir.name
        index_html = chat_dir / f"{chat_name}.html"

        secho(f"  Regenerating: {chat_name}... ", nl=False)
        try:
            messages = load_messages_from_json(data_json)

            if not messages:
                secho("skipped (no messages)", fg=colors.YELLOW)
                continue

            ht = html.create_html(
                name=chat_name, messages=messages, msgs_per_page=paginate
            )
            index_html.write_text(ht, encoding="utf-8")
            html.prep_html(chat_dir)
            html.prep_media_pdf(chat_dir)
            secho(f"done ({len(messages)} messages)", fg=colors.GREEN)
            found += 1

        except Exception as e:
            secho(f"ERROR: {e}", fg=colors.RED)
            if verbose:
                traceback.print_exc()
            errors += 1

    secho(f"\nDone! Regenerated {found} chat(s), {errors} error(s).", fg=colors.GREEN)


@app.command(name="pdf")
def generate_pdf(
    chats_dir: Path = Argument(
        ..., help="Path to your signal-chats export directory"
    ),
    chat: str = Option(
        "",
        "--chat",
        "-c",
        help="Name of a single chat folder to generate PDF for (leave empty for all)",
    ),
    output_name: str = Option(
        "report.pdf",
        "--output",
        "-o",
        help="Name of the PDF file to generate in each chat folder",
    ),
    no_images: bool = Option(
        False,
        "--no-images",
        help="Skip images when generating PDF (useful for large image-heavy chats)",
    ),
    verbose: bool = Option(False, "--verbose", "-v"),
) -> None:
    """Generate a PDF report.pdf in each chat folder using headless Chrome."""
    chrome_bin = _find_chrome()
    if not chrome_bin:
        secho(
            "Error: Could not find Chrome or Chromium. Please install Google "
            "Chrome or Chromium, or make sure it is on your PATH.",
            fg=colors.RED,
        )
        raise Exit(code=1)

    secho(f"Using Chrome at: {chrome_bin}")

    chats_dir = chats_dir.expanduser().resolve()

    # Single chat or all chats
    if chat:
        chat_path = chats_dir / chat
        if not chat_path.is_dir():
            secho(
                f"Error: Chat folder '{chat}' not found in {chats_dir}",
                fg=colors.RED,
            )
            raise Exit(code=1)
        index_htmls = [chat_path / f"{chat}.html"]
        secho(f"Generating PDF for: {chat}")
    else:
        index_htmls = sorted(
            f
            for f in chats_dir.rglob("*.html")
            if f.stem == f.parent.name  # only {name}.html files
        )
        secho(f"Scanning: {chats_dir}")

    found = 0
    errors = 0
    for index_html in index_htmls:
        chat_name = index_html.parent.name
        secho(f"  Generating: {chat_name}... ", nl=False)
        chat_name, success, detail = _generate_one_pdf(
            index_html=index_html,
            chrome_bin=chrome_bin,
            output_name=output_name,
            no_images=no_images,
        )
        if success:
            secho(f"✓ → {detail}", fg=colors.GREEN)
            found += 1
        else:
            secho(f"✗ ERROR ({detail})", fg=colors.RED)
            errors += 1

    secho(f"\nDone! Generated {found} PDF(s), {errors} error(s).", fg=colors.GREEN)


def _generate_one_pdf(
    index_html: Path,
    chrome_bin: str,
    output_name: str,
    no_images: bool,
) -> tuple[str, bool, str]:
    """Generate a PDF for a single chat, splitting large chats into chunks."""
    chat_dir = index_html.parent
    chat_name = chat_dir.name
    if not output_name or output_name == "report.pdf":
        final_pdf = chat_dir / f"{chat_name}.pdf"
    else:
        final_pdf = chat_dir / output_name
    data_json = chat_dir / "data.json"

    # Load messages to check size
    messages_by_year: dict[int, list[models.Message]] = {}
    if data_json.exists():
        all_msgs = load_messages_from_json(data_json)
        for msg in all_msgs:
            messages_by_year.setdefault(msg.date.year, []).append(msg)

    total_messages = sum(len(v) for v in messages_by_year.values())

    # Small chat or no data.json: generate a PDF-optimized HTML and convert
    if total_messages <= _PDF_CHUNK_THRESHOLD or not messages_by_year:
        if not messages_by_year:
            # No data.json — fall back to existing HTML as-is
            if no_images:
                html_text = index_html.read_text(encoding="utf-8")
                html_text = html_text.replace("</head>", _HIDE_MEDIA_CSS)
                tmp_html = index_html.parent / f"{index_html.stem}_noimg_tmp.html"
                tmp_html.write_text(html_text, encoding="utf-8")
                success, detail = _chrome_to_pdf(chrome_bin, tmp_html, final_pdf)
                tmp_html.unlink(missing_ok=True)
            else:
                success, detail = _chrome_to_pdf(chrome_bin, index_html, final_pdf)
        else:
            # Regenerate HTML with for_pdf=True (video/audio → filename refs)
            all_messages = [
                m for _, msgs in sorted(messages_by_year.items()) for m in msgs
            ]
            ht = html.create_html(
                name=chat_name,
                messages=all_messages,
                msgs_per_page=NO_PAGINATION,
                for_pdf=True,
            )
            if no_images:
                ht = ht.replace("</head>", _HIDE_MEDIA_CSS)
            tmp_html = chat_dir / f"{chat_name}_pdf_tmp.html"
            tmp_html.write_text(ht, encoding="utf-8")
            success, detail = _chrome_to_pdf(chrome_bin, tmp_html, final_pdf)
            tmp_html.unlink(missing_ok=True)

        # Add sidebar bookmarks (year + month)
        if success and messages_by_year:
            all_messages_sorted = [
                m for _, msgs in sorted(messages_by_year.items()) for m in msgs
            ]
            try:
                _add_toc_links(final_pdf, all_messages_sorted)
            except Exception:
                pass  # non-fatal

        return chat_name, success, detail

    # Large chat: generate a cover page PDF, then chunk PDFs, then merge all
    all_messages = [
        m for _, msgs in sorted(messages_by_year.items()) for m in msgs
    ]
    chunks = [
        all_messages[i : i + _PDF_CHUNK_SIZE]
        for i in range(0, len(all_messages), _PDF_CHUNK_SIZE)
    ]

    chunk_pdfs: list[Path] = []
    temp_htmls: list[Path] = []
    try:
        # 1) Generate a single cover page PDF from all messages
        cover_html_path = chat_dir / f"{chat_name}_cover_tmp.html"
        cover_pdf_path = chat_dir / f"{chat_name}_cover_tmp.pdf"
        temp_htmls.append(cover_html_path)
        chunk_pdfs.append(cover_pdf_path)

        cover_ht = html.create_cover_html(name=chat_name, messages=all_messages)
        cover_html_path.write_text(cover_ht, encoding="utf-8")
        success, detail = _chrome_to_pdf(chrome_bin, cover_html_path, cover_pdf_path)
        if not success:
            return chat_name, False, f"cover page: {detail}"

        # 2) Generate one PDF per chunk (without cover pages)
        for idx, chunk in enumerate(chunks):
            chunk_html_path = chat_dir / f"{chat_name}_chunk{idx:03d}_tmp.html"
            chunk_pdf_path = chat_dir / f"{chat_name}_chunk{idx:03d}_tmp.pdf"
            temp_htmls.append(chunk_html_path)
            chunk_pdfs.append(chunk_pdf_path)

            start = chunk[0].date.strftime("%Y-%m")
            end = chunk[-1].date.strftime("%Y-%m")
            label = start if start == end else f"{start} to {end}"

            ht = html.create_html(
                name=f"{chat_name} ({label})",
                messages=chunk,
                msgs_per_page=NO_PAGINATION,
                include_cover=False,
                for_pdf=True,
            )
            if no_images:
                ht = ht.replace("</head>", _HIDE_MEDIA_CSS)
            chunk_html_path.write_text(ht, encoding="utf-8")

            success, detail = _chrome_to_pdf(
                chrome_bin, chunk_html_path, chunk_pdf_path
            )
            if not success:
                return (
                    chat_name,
                    False,
                    f"chunk {idx + 1}/{len(chunks)} ({label}): {detail}",
                )

        # 3) Merge cover + all chunks into final PDF
        _merge_pdfs(chunk_pdfs, final_pdf)

        # 4) Add sidebar bookmarks (year + month) to the merged PDF
        try:
            _add_toc_links(final_pdf, all_messages)
        except Exception:
            pass  # non-fatal: PDF is still valid, just without bookmarks

        return chat_name, True, str(final_pdf)

    finally:
        for tmp in temp_htmls + chunk_pdfs:
            if tmp.exists():
                tmp.unlink()


def cli() -> None:
    """Entry point: route legacy calls to main, otherwise use the Typer app."""
    known_subcommands = {
        "regenerate-html",
        "pdf",
        "--help",
        "--install-completion",
        "--show-completion",
    }
    args = sys.argv[1:]
    if args and args[0] not in known_subcommands:
        run(main)
    else:
        app()