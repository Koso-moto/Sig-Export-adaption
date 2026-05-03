# Changelog — Koso-moto adaptation

This documents everything that differs from [carderne/signal-export](https://github.com/carderne/signal-export).

---

## New features

### PDF export (`sigexport pdf ~/signal-chats`)
A full PDF generation pipeline was added using headless Chrome/Chromium.

- Auto-detects Google Chrome or Chromium on macOS, Linux, and Windows
- Generates one `{ChatName}.pdf` per chat in A4 portrait format, no browser headers or footers
- Large chats (over 5,000 messages) are automatically split into 1,000-message chunks, each rendered as a temporary PDF, then merged into one final file using `pypdf`
- Before rendering, a `media_pdf/` subfolder is created containing resized JPEG copies of images (max 600 px wide, EXIF orientation corrected). Chrome uses these instead of the originals for faster and more reliable rendering
- PDF sidebar bookmarks are added per year and month for navigation
- Images can be excluded with `--no-images` (useful for very image-heavy chats)
- Single chat: `--chat Aya`; custom filename: `--output archive.pdf`

Requires: Google Chrome or Chromium. Optional: `pip install pypdf Pillow`

### Regenerate HTML (`sigexport regenerate-html ~/signal-chats`)
Re-renders the `.html` files from existing `data.json` exports without re-exporting from Signal. Useful after CSS or template changes. Supports `--chat Aya` to regenerate a single chat.

### Cover page with statistics
Every HTML export starts with a cover page containing:
- Total message count broken down by sender
- Number of images shared
- First and last message dates
- A table of contents with year-based jump links (HTML) or a note pointing to PDF sidebar bookmarks (PDF)

### Missing attachments report (`!signal_missing_attachments.txt`)
Every export writes `!signal_missing_attachments.txt` to the root of the output folder. It queries Signal's database for every attachment that has a database record but no local file — i.e. files that were never downloaded or have since been deleted. The report lists them by conversation, date, content type, and filename.

The `!` prefix ensures the file sorts to the top of the folder alphabetically.

To recover missing files before archiving: open the chat in Signal Desktop → click the contact's name → *Media, Links and Files* → download everything manually. Then re-run the export.

### Self-contained HTML for iOS / iCloud Drive
The CSS stylesheet is inlined into each `.html` file instead of being linked as a separate `../style.css`. iOS Safari blocks loading resources from parent directories when opening local files from the Files app or iCloud Drive. With inlined CSS, every `.html` renders correctly on iPhone without embedding images.

---

## Changed from upstream

### HTML renderer completely redesigned
The upstream renderer produces plain message lines. This adaptation renders messages as **chat bubbles**:
- Your messages appear on the right in blue; others appear on the left in grey — matching the look of Signal itself
- Sender names are shown above each bubble
- Day dividers appear between messages from different days, each with an HTML anchor for TOC navigation
- Quoted/reply blocks are shown as indented bordered sections inside the bubble
- Reactions (emoji + sender name) are displayed inline
- Voice notes and audio use native browser `<audio>` controls; videos use `<video>` controls. In PDF mode both are replaced with a plain filename reference since media cannot render in PDFs
- Images are displayed inline at full width inside the bubble
- Bare URLs are auto-linked even if not formatted as Markdown links

### Pagination removed
The upstream version offers a `--paginate` option (default: 100 messages per page) that splits the HTML output into pages with PREV/NEXT navigation links. This adaptation removes pagination entirely. Every chat is a single scrollable page, which works better for PDF rendering and for opening on mobile devices.

### CSS completely rewritten
The upstream stylesheet is minimal. This adaptation uses a purpose-built stylesheet with:
- Chat bubble layout (blue right / grey left)
- System font stack for clean rendering on all platforms
- Day dividers with horizontal rules
- Cover page table styling
- Full print/PDF media queries for clean A4 output

### `sigexport` command now acts as both entry point and app
Upstream: `sigexport ~/signal-chats` runs the export directly.
This adaptation adds `pdf` and `regenerate-html` as subcommands while keeping `sigexport ~/signal-chats` working as before. The CLI routes to the correct handler based on the first argument.

---

## Bug fixes (relative to upstream at time of fork)

### Null sender crash
Signal sometimes stores messages with no sender (e.g. system messages). Passing `None` to `html_escape()` or calling `.title()` on it crashed HTML generation. Fixed by filtering null senders in the cover page statistics and falling back to `"Unknown"` in the message renderer.

### Null attachment path crash
Some attachments recorded in the database have no local file (never downloaded or since deleted). The export tried to copy them, produced a confusing warning ("No file to copy at …/None"), and later crashed. Fixed by detecting null paths early, skipping the copy, and recording the attachment in the missing attachments report instead.

### Attachment `path` type mismatch
`att.path` was stored as a `PosixPath` object but `models.is_image()`, `is_audio()`, and `is_video()` expected a plain `str`. This caused attachment type detection to silently fail for every attachment, so images were never displayed inline and audio/video players were never inserted. Fixed by converting `att.path` to `str` at the point of creation.

---

## Synced from upstream after fork

### Handle missing columns in older Signal databases (`upstream aa8c195`)
Signal databases from early 2024 and older are missing columns that the message query assumed were always present (`timestamp`, `serverTimestamp`, `sourceServiceId`, `hasAttachments`, `readStatus`, `seenStatus`, `expireTimer`). The query now uses `PRAGMA table_info(messages)` to detect which columns exist and substitutes `NULL AS <colname>` for any that are absent.

### Accurate call descriptions (`upstream 48ae11a`)
Signal now stores call direction and status in a separate `callsHistory` table. Previously all calls were described as "Incoming call" or "Outgoing call". After this fix, exports show accurate descriptions such as:
- *"Incoming voice call (accepted, 2m 30s)"*
- *"Missed video call"*
- *"Outgoing call (unanswered)"*

---

## Internal code changes

These do not affect user-visible behaviour but improve maintainability:

- Extracted the repeated 5-line SQLCipher PRAGMA setup into `files._open_signal_db()`, used in `copy_attachments()`, `write_missing_attachments_report()`, and `data.fetch_data()`
- Simplified `is_image()`, `is_audio()`, `is_video()` in `models.py` to use a shared `_has_extension()` helper with `Path.suffix` instead of string splitting
- Renamed internal variables for clarity: `jsonLoaded` → `message_json`, `overlength` → `filename_too_long`, `comp()` → `dedup_key()`
- Fixed a potential `IndexError` in `merge.py` when the first line of a Markdown file does not match the expected message format
- Updated image resizing to use `PilImage.Resampling.LANCZOS` (correct Pillow 10+ API, replaces deprecated `PilImage.LANCZOS`)
