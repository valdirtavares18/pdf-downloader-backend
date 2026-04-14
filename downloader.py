"""
Versão standalone — não precisa de chave de API.
Uso: python downloader.py <url> [pdf|md]
"""
import sys
import requests
from bs4 import BeautifulSoup
import html2text
from fpdf import FPDF
from urllib.parse import urlparse


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}


def fetch(url: str) -> tuple[str, str]:
    """Retorna (titulo, markdown) da página."""
    r = requests.get(url, headers=HEADERS, timeout=15)
    r.raise_for_status()

    soup = BeautifulSoup(r.text, "lxml")

    title = soup.title.string.strip() if soup.title else "sem_titulo"

    for tag in soup(["script", "style", "nav", "footer", "header", "aside", "iframe", "noscript"]):
        tag.decompose()

    main = soup.find("article") or soup.find("main") or soup.find("body")
    html_content = str(main) if main else str(soup)

    h = html2text.HTML2Text()
    h.ignore_links = False
    h.ignore_images = True
    h.body_width = 0
    markdown = h.handle(html_content).strip()

    return title, markdown


def to_markdown(title: str, markdown: str, filename: str):
    path = filename if filename.endswith(".md") else filename + ".md"
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"# {title}\n\n{markdown}")
    print(f"Salvo: {path}")


def to_pdf(title: str, markdown: str, filename: str):
    path = filename if filename.endswith(".pdf") else filename + ".pdf"

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    # Título
    pdf.set_font("Helvetica", "B", 18)
    safe_title = title.encode("latin-1", errors="replace").decode("latin-1")
    pdf.multi_cell(0, 10, safe_title)
    pdf.ln(4)

    pdf.set_font("Helvetica", size=11)

    for line in markdown.split("\n"):
        if line.startswith("# "):
            pdf.set_font("Helvetica", "B", 15)
            text = line[2:].strip()
        elif line.startswith("## "):
            pdf.set_font("Helvetica", "B", 13)
            text = line[3:].strip()
        elif line.startswith("### "):
            pdf.set_font("Helvetica", "B", 12)
            text = line[4:].strip()
        else:
            pdf.set_font("Helvetica", size=11)
            text = line.replace("**", "").replace("__", "").replace("*", "")

        safe = text.encode("latin-1", errors="replace").decode("latin-1")
        try:
            pdf.multi_cell(0, 6, safe)
        except Exception:
            pass

    pdf.output(path)
    print(f"Salvo: {path}")


def slugify(text: str) -> str:
    import re
    import unicodedata
    # Normaliza acentos (ex: ã → a, é → e)
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", errors="ignore").decode("ascii")
    return re.sub(r"[^\w\-]", "_", text)[:60]


def main():
    if len(sys.argv) < 2:
        print("Uso: python downloader.py <url> [pdf|md]")
        sys.exit(1)

    url = sys.argv[1]
    fmt = sys.argv[2].lower() if len(sys.argv) > 2 else "md"

    print(f"Buscando: {url}")
    title, markdown = fetch(url)
    filename = slugify(title) or slugify(urlparse(url).netloc)

    if fmt == "pdf":
        to_pdf(title, markdown, filename)
    else:
        to_markdown(title, markdown, filename)


if __name__ == "__main__":
    main()
