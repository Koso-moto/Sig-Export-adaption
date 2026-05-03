"""HTML templates used by html.create_html().

Format variables
────────────────
html:           {name}, {content}
message:        {cl}, {date}, {time}, {sender}, {quote}, {body}, {reactions}
figure:         {src}, {alt}
audio:          {src}
video:          {src}
attachment_ref: {filename}   (used in PDF instead of video/audio players)
"""

html = """
<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <title>{name}</title>
    <link rel="stylesheet" href="../style.css">
</head>
<body>
    {content}
    <script>if (!document.location.hash) document.location.hash = 'pg0'</script>
</body>
</html>
"""

message = """
<div class="{cl}">
    <span class="date">{date}</span>
    <span class="time">{time}</span>
    <span class="sender">{sender}</span>
    {quote}
    <span class="body">{body}</span>
    <span class="reaction">{reactions}</span>
</div>
"""

audio = """
<audio controls>
    <source src="{src}">
</audio>
"""

figure = """
<figure>
    <img loading="lazy" src="{src}" alt="{alt}">
</figure>
"""

video = """
<video controls>
    <source src="{src}" type="video/mp4">
</video>
"""

attachment_ref = """
<span class="attachment-ref">📎 {filename}</span>
"""