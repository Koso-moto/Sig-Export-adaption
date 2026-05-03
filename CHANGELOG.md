# Changelog

## [Unreleased] — Koso-moto adaptation

All changes are relative to [carderne/signal-export](https://github.com/carderne/signal-export).

---

### Bug fixes

- **Null sender crash** — Signal sometimes stores messages with no sender name. This caused `html_escape(None)` to crash during HTML generation. Fixed by filtering out null senders in the cover page statistics and normalising them to `"Unknown"` in the message renderer.

- **Null attachment path crash** — Some attachments recorded in Signal's database have no local file (never downloaded or since deleted). These caused a crash when the export tried to copy them. Fixed by skipping them silently and recording them in the missing attachments report instead.

- **Subcommand routing** — `sigexport pdf` and `sigexport regenerate-html` were silently broken after a routing refactor. Typer's `@app.callback` with an optional positional argument caused `pdf` to be parsed as the output directory rather than as a subcommand name. Reverted to an explicit `cli()` router that correctly directs subcommands to `app()` and the main export to `run(main)`.

- **Missing columns in older Signal databases** — Signal databases from early 2024 and older are missing columns (`timestamp`, `serverTimestamp`, `sourceServiceId`, etc.) that the message query expected. The query now uses `PRAGMA table_info(messages)` to detect available columns and substitutes `NULL AS <colname>` for any that are absent. *(Synced from upstream `aa8c195`.)*

- **Call direction in exports** — Calls were always described as "Incoming call" or "Outgoing call" regardless of actual direction. Signal now stores richer call metadata in a `callsHistory` table. The export now reads from it and produces accurate descriptions such as *"Incoming voice call (accepted, 2m 30s)"* or *"Missed video call"*. *(Synced from upstream `48ae11a`.)*

---

### New features

- **Missing attachments report** — Every export now writes `!signal_missing_attachments.txt` to the root of the output folder. It lists every attachment recorded in Signal's database that has no local file, grouped by conversation with date, file type, and filename. The `!` prefix ensures it sorts to the top of the folder alphabetically.

  To recover missing files before archiving: open the chat in Signal Desktop → click the contact's name → *Media, Links and Files* → download everything manually. Signal will then store the files locally and they will be included in the next export.

- **Self-contained HTML for iOS** — The CSS stylesheet is now inlined into each `.html` file instead of being linked as `../style.css`. iOS Safari blocks loading resources from parent directories when opening local files from iCloud Drive. With inlined CSS, every `.html` file is fully self-contained and renders correctly on iPhone without needing to embed images.

---

### Removed

- **`--paginate` option** — Pagination was never functional. The implementation added PREV/NEXT anchor links and `<div class="page">` wrappers, but there was no CSS or JavaScript to actually hide or show individual pages — all messages were always visible in one continuous scroll regardless of the setting. The option, the dead CSS, and the dead script tag have been removed. HTML output is now always a single scrollable page per chat.

- **`sigexport main` subcommand** — The main export command no longer requires a `main` subcommand. Use `sigexport ~/signal-chats` directly, matching upstream behaviour.

- **`style.css` copied to output folder** — The output folder no longer contains a `style.css` file since the stylesheet is now inlined into each HTML file.

---

### Code quality

- Extracted repeated SQLCipher PRAGMA setup into `files._open_signal_db()`, eliminating duplication across `copy_attachments()`, `write_missing_attachments_report()`, and `data.fetch_data()`.
- Simplified `is_image()`, `is_audio()`, `is_video()` in `models.py` into a shared `_has_extension()` helper using `Path.suffix` instead of string splitting.
- Renamed cryptic variables: `jsonLoaded` → `message_json`, `overlength` → `filename_too_long`, `comp()` → `dedup_key()`.
- Fixed a potential `IndexError` in `merge.py` when the first line of a Markdown file does not match the expected message format.
- Removed dead code: commented-out function stubs, unused `_URL_RE` regex, unused `import sys` / `import datetime`.
- Image resizing now uses `PilImage.Resampling.LANCZOS` (correct Pillow 10+ API).

---

### CLI / help text

- `sigexport --help` now shows all export options plus the `pdf` and `regenerate-html` subcommands in one view, with usage examples at the top.
- `sigexport pdf --help` shows PDF-specific options and examples.
- `sigexport regenerate-html --help` shows its own options and examples.
- All example paths use `~/signal-chats` consistently, matching upstream.
