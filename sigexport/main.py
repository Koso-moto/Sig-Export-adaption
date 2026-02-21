"""Main script for sigexport."""

import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

from typer import Argument, Context, Exit, Option, Typer, colors, secho

app = Typer()

from sigexport import create, data, files, html, logging, merge, utils
from sigexport.export_channel_metadata import export_channel_metadata

OptionalPath = Optional[Path]
OptionalStr = Optional[str]


@app.command()
def main(
    ctx: Context,
    dest: Path = Argument(None),
    source: OptionalPath = Option(None, help="Path to Signal source directory"),
    old: OptionalPath = Option(None, help="Path to previous export to merge"),
    password: OptionalStr = Option(None, help="Linux-only. Password to decrypt DB key"),
    key: OptionalStr = Option(
        None, help="Linux-only. DB key, as found in the old config.json"
    ),
    paginate: int = Option(
        100, "--paginate", "-p", help="Messages per page in HTML; set to 0 for infinite"
    ),
    chats: str = Option(
        "", help="Comma-separated chat names to include: contact names or group names"
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
        help="Start date as a ISO-8601 formatted string (e.g., 2025-01-15T12:30:00+02:00)",
    ),
    end_date: Optional[str] = Option(
        None,
        "--end",
        help="End date as a ISO-8601 formatted string (e.g., 2025-03-15T12:30:00+02:00)",
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
        help="Export membership information for all chats (or for a subset of chats given by the --chats option)",
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
        # secho("Error: Missing argument 'DEST'", fg=colors.RED)
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
        key=key,
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
        export_channel_metadata(dest, contacts, owner, chats.split(",") if chats else None)
        raise Exit()

    dest = Path(dest).expanduser()
    if not dest.is_dir():
        dest.mkdir(parents=True, exist_ok=True)
    elif overwrite:
        shutil.rmtree(dest)
        dest.mkdir(parents=True, exist_ok=True)
    else:
        secho(
            f"Output folder '{dest}' already exists, didn't do anything!", fg=colors.RED
        )
        raise Exit()

    contacts = utils.fix_names(contacts)

    if attachments:
        secho("Copying and renaming attachments")
        files.copy_attachments(source_dir, dest, convos, contacts, password, key)

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
        paginate = int(1e20)

    if html_output:
        html.prep_html(dest)
    for key, messages in chat_dict.items():
        name = contacts[key].name
        # some contact names are None
        if not name:
            name = "None"

        (dest / name).mkdir(parents=True, exist_ok=True)
        md_path = dest / name / "chat.md"
        js_path = dest / name / "data.json"
        ht_path = dest / name / "index.html"

        md_f = md_path.open("a", encoding="utf-8")
        js_f = None
        if json_output:
            js_f = js_path.open("a", encoding="utf-8")
        ht_f = None
        if html_output:
            ht_f = ht_path.open("w", encoding="utf-8")

        try:
            for msg in messages:
                print(msg.to_md(), file=md_f)
                if js_f:
                    print(msg.dict_str(), file=js_f)
            if ht_f:
                ht = html.create_html(
                    name=name, messages=messages, msgs_per_page=paginate
                )
                print(ht, file=ht_f)
        finally:
            md_f.close()
            if js_f:
                js_f.close()
            if ht_f:
                ht_f.close()

    secho("Done!", fg=colors.GREEN)


def parse_input_dt(dt_string: str) -> datetime:
    """Parses the ISO-formatted datetime string entered by the user"""
    try:
        dt = datetime.fromisoformat(dt_string)

    except ValueError:
        secho(
            f"Invalid datetime, you entered '{dt_string}'. Must match ISO format, eg '2025-06-01' or '2025-08-10T12:15:00Z'",
            fg=colors.RED,
        )
        raise

    if dt.tzinfo is None:
        local_tz = datetime.now().astimezone().tzinfo
        dt = dt.replace(tzinfo=local_tz)

    return dt


@app.command(name="regenerate-html")
def regenerate_html(
    chats_dir: Path = Argument(..., help="Path to your signal-chats export directory"),
    paginate: int = Option(100, "--paginate", "-p", help="Messages per page in HTML; set to 0 for infinite"),
    verbose: bool = Option(False, "--verbose", "-v"),
) -> None:
    """Regenerate index.html files from existing data.json files without re-exporting from Signal."""
    import json
    from datetime import datetime as dt

    logging.verbose = verbose
    chats_dir = chats_dir.expanduser().resolve()
    secho(f"Scanning: {chats_dir}")

    if paginate <= 0:
        paginate = int(1e20)

    found = 0
    errors = 0
    for data_json in sorted(chats_dir.rglob("data.json")):
        chat_dir = data_json.parent
        chat_name = chat_dir.name
        index_html = chat_dir / "index.html"

        secho(f"  Regenerating: {chat_name}... ", nl=False)
        try:
            from sigexport import models as _models
            messages = []
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
                            attachments.append(_models.Attachment(
                                name=str(att.get("name", "")),
                                path=Path(str(raw_path)) if raw_path else None,
                            ))
                        elif att:
                            attachments.append(_models.Attachment(
                                name=str(att),
                                path=Path(str(att)),
                            ))

                    reactions = []
                    for r in d.get("reactions", []):
                        if isinstance(r, dict):
                            reactions.append(_models.Reaction(
                                name=r.get("name", ""),
                                emoji=r.get("emoji", ""),
                            ))

                    messages.append(_models.Message(
                        date=dt.fromisoformat(d["date"]),
                        sender=d.get("sender", ""),
                        body=d.get("body", "") or "",
                        quote=d.get("quote", "") or "",
                        sticker=d.get("sticker", "") or "",
                        reactions=reactions,
                        attachments=attachments,
                    ))

            if not messages:
                secho("skipped (no messages)", fg=colors.YELLOW)
                continue

            ht = html.create_html(name=chat_name, messages=messages, msgs_per_page=paginate)
            index_html.write_text(ht, encoding="utf-8")
            html.prep_html(chat_dir)
            secho(f"done ({len(messages)} messages)", fg=colors.GREEN)
            found += 1

        except Exception as e:
            import traceback
            secho(f"ERROR: {e}", fg=colors.RED)
            if verbose:
                traceback.print_exc()
            errors += 1

    secho(f"\nDone! Regenerated {found} chat(s), {errors} error(s).", fg=colors.GREEN)


def cli() -> None:
    """cli."""
    app()