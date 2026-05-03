# signal-export (Koso-moto adaptation)

[![PyPI version](https://badge.fury.io/py/signal-export.svg)](https://pypi.org/project/signal-export/)

This is a heavily extended adaptation of [carderne/signal-export](https://github.com/carderne/signal-export). It keeps the original Markdown and HTML export functionality and adds a full **PDF generation pipeline**, a redesigned **chat-bubble HTML renderer**, **cover pages with statistics**, **date-range filtering**, a **`regenerate-html` command**, and more.

⚠️ **NB:** Because the latest versions of Signal Desktop protect the database encryption key, decrypting involves some extra steps. Good luck.

> **Platform note:** This adaptation has only been tested on **macOS**. If you are on Windows or NixOS, [carderne's original repository](https://github.com/carderne/signal-export) may be a better starting point as it has broader platform support and documentation.

Export chats from the [Signal](https://www.signal.org/) [Desktop app](https://www.signal.org/download/) to Markdown, HTML, and PDF files with attachments. Each chat is exported as an individual `.md` / `.html` file and the attachments for each chat are stored in a separate `media/` folder. Attachments are linked from the Markdown files and displayed in the HTML (pictures, videos, voice notes).

---

## Example

An export for a group conversation looks as follows in Markdown:

```
[2019-05-29, 15:04] Me: How is everyone?
[2019-05-29, 15:10] Aya: We're great!
[2019-05-29, 15:20] Jim: I'm not.
```

The HTML output renders messages as **chat bubbles**: your messages appear on the right in blue, others appear on the left in grey, matching the look of a modern messaging app. Images are displayed inline; videos and audio use native browser controls.

Each HTML export starts with a **cover page** containing:
- Total message count (yours vs. theirs)
- Number of images shared
- First and last message dates
- A table of contents with jump links per year

---

## 🍎 Installation (macOS)

1. Make sure you have Python installed.
2. Install this package:

```bash
pip install signal-export

# or with pipx (recommended):
pipx install signal-export
```

3. Install the additional packages required for PDF export:

```bash
pip install pypdf Pillow
```

4. Install **Google Chrome** or **Chromium** — this is required for PDF generation. The tool will auto-detect it.

5. Then run the script:

```bash
sigexport ~/outputdir
```

---

## 🚀 Usage

> ⚠️ **Fully exit Signal Desktop before running**, otherwise you will likely encounter an `I/O disk` error because the database is locked by the running app.

See the full help:

```bash
sigexport --help
```

### Main export

```bash
# Export all chats
sigexport ~/outputdir

# Export only specific chats (by contact or group name)
sigexport --chats=Jim,Aya ~/outputdir

# Filter by date range (ISO-8601 format)
sigexport ~/outputdir --start 2024-01-01 --end 2024-12-31

# List available chats and exit
sigexport --list-chats

# Merge with a previous export (nothing is overwritten)
sigexport ~/outputdir --old ~/outputdir-backup

# Skip copying media attachments
sigexport --no-attachments ~/outputdir

# Export chat membership metadata only
sigexport --chat-members ~/outputdir
```

You can add `--source /path/to/dir/` if the script cannot find your Signal config automatically. On macOS the default location is `~/Library/Application Support/Signal/`. The directory must contain a `sql/db.sqlite` file.

---

## 📄 PDF Export

This adaptation adds a full **PDF generation pipeline** using headless Chrome/Chromium.

### Requirements

- **Google Chrome** or **Chromium** must be installed. The tool auto-detects it on macOS, Linux, and Windows.
- For large chats: `pip install pypdf` (used to merge chunk PDFs).
- For correctly-oriented, resized images in PDFs: `pip install Pillow`.

### Generate PDFs

```bash
# Generate a PDF for every chat in your export directory
sigexport pdf ~/outputdir

# Generate a PDF for a single chat only
sigexport pdf ~/outputdir --chat "Aya"

# Skip images (useful for very image-heavy chats)
sigexport pdf ~/outputdir --no-images

# Use a custom output filename
sigexport pdf ~/outputdir --output my-export.pdf
```

Each chat gets its own `{ChatName}.pdf` inside its folder. The output is **A4 portrait** format with no browser-injected headers or footers.

**Large chats** (over 5,000 messages) are automatically split into 1,000-message chunks. Each chunk is rendered to a temporary PDF, then all chunks are merged into a single final PDF using `pypdf`.

Before rendering, a `media_pdf/` subfolder is generated containing **resized JPEG copies** of images (max 600px wide, EXIF orientation corrected). Chrome uses these instead of the originals for faster and more reliable PDF rendering.

---

## 🔄 Regenerate HTML

If you want to refresh the HTML without re-exporting from Signal (e.g. after updating the CSS or templates):

```bash
# Regenerate HTML for all chats
sigexport regenerate-html ~/outputdir

# Regenerate HTML for a single chat only
sigexport regenerate-html ~/outputdir --chat "Aya"
```

This reads the existing `data.json` files (one message per line) and rewrites the `.html` files in place.

---

## 📁 Output structure

```
~/outputdir/
├── !signal_missing_attachments.txt    ← report of attachments not downloaded locally
├── Aya/
│   ├── Aya.html               ← chat-bubble HTML with cover page
│   ├── Aya.pdf                ← generated PDF (if you ran sigexport pdf)
│   ├── chat.md                ← plain-text Markdown transcript
│   ├── data.json              ← JSON export (one message per line)
│   ├── media/                 ← original attachments
│   └── media_pdf/             ← resized JPEG copies for PDF rendering
└── Jim/
    └── ...
```

`!signal_missing_attachments.txt` lists every attachment recorded in Signal's database that has no local file — either never downloaded or since deleted. Entries are grouped by conversation with date, file type, and filename.

To recover missing files before archiving: open the chat in Signal Desktop → click the contact's name → Media, Links and Files → manually download everything. Signal will then store the files locally and they will be included in the next export.

---

## 🎨 HTML rendering

- HTML output is a single scrollable page per chat.
- Messages render as **chat bubbles**: blue on the right for you, grey on the left for others.
- **Day dividers** appear between messages from different days, each with an HTML anchor for TOC navigation.
- **Quoted/reply blocks** are shown as indented, bordered sections inside the bubble.
- **Reactions** (emoji + sender name) are displayed inline.
- Message bodies are rendered from Markdown to HTML.
- Bare URLs are auto-linked even if not formatted as Markdown links.
- The stylesheet is inlined into each HTML file, making every `.html` fully self-contained (works in Safari on iPhone via iCloud Drive). It includes full **print/PDF media queries** for clean A4 output.


---