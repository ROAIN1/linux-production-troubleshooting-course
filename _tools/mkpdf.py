"""Build the Linux Production Troubleshooting course PDFs from Markdown sources.

Usage:  python mkpdf.py            (builds every _src/*.md + the combined book)
Needs:  pip install markdown       and Google Chrome (headless, for HTML -> PDF)

Markdown conventions used in _src/*.md
  ```bash / ```sh        -> dark "Terminal" box          ```json / ```yaml / ```text -> light "File / Output" box
  :::concept  ... :::    -> blue  "Concept"              :::prod   ... :::  -> purple "In Production" story
  :::tip      ... :::    -> amber "Exam Tip"             :::warn   ... :::  -> red    "Watch Out"
  :::lab Title ... :::   -> green "Hands-on Lab"         :::answer ... :::  -> collapsed-style answer box
  :::cost     ... :::    -> teal  "Cost"                 :::summary ... ::: -> grey   "Key Takeaways"
"""
import glob, html, os, re, subprocess, sys
import markdown

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC, BUILD = os.path.join(ROOT, "_src"), os.path.join(ROOT, "_build")
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

LABELS = {"concept": "💡 Concept", "prod": "🏭 In Production — what really happens", "tip": "🎯 Exam Tip",
          "warn": "⚠️ Watch Out", "lab": "🧪 Hands-on Lab", "answer": "✅ Answer & Explanation",
          "cost": "💲 Cost", "summary": "📌 Key Takeaways", "story": "📖 Analogy"}

CSS = r"""
@page { size: A4; margin: 15mm 14mm 17mm 14mm; }
* { box-sizing: border-box; }
body { font: 10.4pt/1.55 -apple-system, "Helvetica Neue", Helvetica, Arial, sans-serif; color: #16191f; margin: 0; }
h1 { font-size: 23pt; color: #1b2a41; margin: 0 0 4px; line-height: 1.15; }
.mod { font: 700 9pt -apple-system, Helvetica, sans-serif; color: #cc0000; letter-spacing: .14em; text-transform: uppercase; }
h2 { font-size: 15.5pt; color: #1b2a41; border-bottom: 2px solid #cc0000; padding-bottom: 3px; margin: 24px 0 8px; break-after: avoid; }
h3 { font-size: 12.6pt; color: #0b5a8f; margin: 16px 0 5px; break-after: avoid; }
h4 { font-size: 11pt; color: #1b2a41; margin: 12px 0 4px; break-after: avoid; }
p { margin: 6px 0; } ul, ol { margin: 4px 0 8px; padding-left: 22px; } li { margin: 2px 0; }
a { color: #0972d3; text-decoration: none; }
code { font: 8.9pt "SF Mono", Menlo, Consolas, monospace; background: #f1f3f3; padding: 1px 4px; border-radius: 3px; color: #a3330b; }
pre { font: 8.5pt/1.45 "SF Mono", Menlo, Consolas, monospace; border-radius: 6px; padding: 9px 11px; white-space: pre-wrap;
      word-break: break-word; margin: 6px 0 10px; break-inside: avoid; }
pre code { background: none; padding: 0; color: inherit; font-size: inherit; }
pre.term { background: #1b222d; color: #e6edf3; border: 1px solid #1b222d; }
pre.term::before { content: "TERMINAL"; display: block; font: 700 7pt -apple-system, sans-serif; color: #ff8a80; letter-spacing: .12em; margin-bottom: 4px; }
pre.file { background: #f7f9fb; border: 1px solid #d5dbe1; color: #16191f; }
table { border-collapse: collapse; width: 100%; margin: 8px 0 12px; font-size: 9pt; }
th, td { border: 1px solid #d5dbe1; padding: 4px 7px; vertical-align: top; text-align: left; }
th { background: #1b2a41; color: #fff; font-weight: 600; } tr:nth-child(even) td { background: #fafbfc; } tr { break-inside: avoid; }
.box { border-radius: 7px; padding: 8px 12px 6px; margin: 10px 0 12px; border: 1px solid; break-inside: avoid-page; }
.box > .lbl { font: 700 8.2pt -apple-system, Helvetica, sans-serif; letter-spacing: .05em; text-transform: uppercase; margin-bottom: 3px; }
.box > *:last-child { margin-bottom: 2px; }
.concept { background: #f0f7ff; border-color: #b6d7f7; } .concept > .lbl { color: #0b5a8f; }
.prod    { background: #f6f1ff; border-color: #cdb9f2; } .prod > .lbl { color: #5b2bb5; }
.tip     { background: #fff8e8; border-color: #f5cf7a; } .tip > .lbl { color: #a25a00; }
.warn    { background: #fff1f0; border-color: #f3b4ad; } .warn > .lbl { color: #b42318; }
.lab     { background: #f1fbf3; border-color: #9fd8ab; break-inside: auto; } .lab > .lbl { color: #1d7a33; }
.answer  { background: #f3faf4; border-color: #b7dfbf; } .answer > .lbl { color: #1d7a33; }
.cost    { background: #eefaf9; border-color: #9ad8d1; } .cost > .lbl { color: #0b6e66; }
.summary { background: #f4f5f7; border-color: #cfd4da; } .summary > .lbl { color: #1b2a41; }
.story   { background: #fffdf2; border-color: #eadf9c; } .story > .lbl { color: #7a6400; }
.labtitle { font-weight: 700; font-size: 11pt; color: #16191f; margin: 0 0 4px; }
hr { border: none; border-top: 1px solid #d5dbe1; margin: 16px 0; }
.cover { background: linear-gradient(135deg, #1b2a41, #4a1414); color: #fff; border-radius: 10px; padding: 16px 20px; margin: 0 0 16px; }
.cover .mod { color: #ff8a80; } .cover h1 { color: #fff; } .cover p { color: #d5dbe1; margin: 4px 0 0; font-size: 9.6pt; }
.pb { break-before: page; }
.q { break-inside: avoid; }
"""


def boxes(md_text):
    out, stack = [], []
    for line in md_text.split("\n"):
        m = re.match(r"^:::(\w+)\s*(.*)$", line.strip())
        if m and m.group(1) in LABELS:
            kind, title = m.group(1), m.group(2).strip()
            stack.append(kind)
            out.append(f'\n<div class="box {kind}" markdown="1">\n<div class="lbl">{LABELS[kind]}</div>\n')
            if title:
                out.append(f'<div class="labtitle">{html.escape(title)}</div>\n')
            continue
        if line.strip() == ":::" and stack:
            stack.pop(); out.append("\n</div>\n"); continue
        out.append(line)
    return "\n".join(out) + "\n</div>" * len(stack)


def tidy(md_text):
    """Fix two common Markdown gotchas outside code fences:
    - answer options 'A. ...' on consecutive lines -> each on its own line
    - a list that starts right after a text line -> needs a blank line before it"""
    lines, out, fence = md_text.split("\n"), [], False
    opt = re.compile(r"^[A-F]\. ")
    item = re.compile(r"^\s*(\d+\.|[-*]) ")
    for i, line in enumerate(lines):
        if line.strip().startswith("```"):
            fence = not fence
        if not fence:
            line = re.sub(r"^(\s*)- \[ \] ", r"\1- ☐ ", line)
            prev = out[-1] if out else ""
            if item.match(line) and prev.strip() and not item.match(prev) and not prev.startswith((" ", "\t", "|", "<", ":::")):
                out.append("")
            nxt = lines[i + 1] if i + 1 < len(lines) else ""
            if opt.match(line) and opt.match(nxt):
                line = line.rstrip() + "  "
        out.append(line)
    return "\n".join(out)


def render(md_text):
    md_text = boxes(tidy(md_text))
    body = markdown.markdown(md_text, extensions=["tables", "fenced_code", "md_in_html", "sane_lists", "attr_list"])
    body = re.sub(r'<pre><code class="language-(bash|sh|shell|console|powershell)">', r'<pre class="term"><code>', body)
    body = re.sub(r'<pre><code(?: class="language-\w+")?>', r'<pre class="file"><code>', body)
    return body


def page(title, body):
    return f"<!doctype html><html><head><meta charset='utf-8'><title>{html.escape(title)}</title><style>{CSS}</style></head><body>{body}</body></html>"


def to_pdf(html_path, pdf_path):
    subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--no-pdf-header-footer", f"--print-to-pdf={pdf_path}",
                    "file://" + html_path], check=True, capture_output=True)


def main():
    os.makedirs(BUILD, exist_ok=True)
    files = sorted(glob.glob(os.path.join(SRC, "*.md")))
    book = []
    for f in files:
        name = os.path.splitext(os.path.basename(f))[0]
        text = open(f, encoding="utf-8").read()
        title = re.search(r"^# (.+)$", text, re.M).group(1)
        body = render(text)
        h = os.path.join(BUILD, name + ".html")
        open(h, "w", encoding="utf-8").write(page(title, body))
        to_pdf(h, os.path.join(ROOT, name + ".pdf"))
        book.append(f'<section class="pb">{body}</section>' if book else f"<section>{body}</section>")
        print("built", name + ".pdf")
    h = os.path.join(BUILD, "book.html")
    open(h, "w", encoding="utf-8").write(page("Linux Production Troubleshooting – Complete Guide", "".join(book)))
    to_pdf(h, os.path.join(ROOT, "Linux-Production-Troubleshooting-Complete-Guide.pdf"))
    print("built Linux-Production-Troubleshooting-Complete-Guide.pdf")


if __name__ == "__main__":
    main()
