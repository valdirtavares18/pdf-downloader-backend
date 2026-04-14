"""
Baixa conteúdo de cursos Articulate Rise interceptando as chamadas de API.
Uso: python rise_downloader.py <url> [pdf|md]
"""
import sys
import re
import json
import time
import unicodedata
import base64
import os
import requests as req
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

load_dotenv()
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.enums import TA_LEFT


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", errors="ignore").decode("ascii")
    return re.sub(r"[^\w\-]", "_", text)[:60]


HTML_ENTITIES = {
    "&ldquo;": '"', "&rdquo;": '"', "&lsquo;": "'", "&rsquo;": "'",
    "&amp;": "&", "&lt;": "<", "&gt;": ">", "&nbsp;": " ",
    "&aacute;": "á", "&eacute;": "é", "&iacute;": "í", "&oacute;": "ó",
    "&uacute;": "ú", "&atilde;": "ã", "&otilde;": "õ", "&ccedil;": "ç",
    "&Aacute;": "Á", "&Eacute;": "É", "&Iacute;": "Í", "&Oacute;": "Ó",
    "&Uacute;": "Ú", "&Atilde;": "Ã", "&Otilde;": "Õ", "&Ccedil;": "Ç",
    "&agrave;": "à", "&egrave;": "è", "&acirc;": "â", "&ecirc;": "ê",
    "&ocirc;": "ô", "&ucirc;": "û", "&auml;": "ä", "&ouml;": "ö",
    "&uuml;": "ü", "&ntilde;": "ñ", "&hellip;": "...", "&mdash;": "—",
    "&ndash;": "–", "&bull;": "•",
}

UUID_RE = re.compile(r"^[a-z0-9]{20,}$")
IMAGE_EXT_RE = re.compile(r"\.(png|jpg|jpeg|gif|svg|webp)$", re.I)
IMAGE_PATH_RE = re.compile(r"rise/courses/")


def decode_html(text: str) -> str:
    import html
    text = html.unescape(text)
    for entity, char in HTML_ENTITIES.items():
        text = text.replace(entity, char)
    text = re.sub(r"&[a-z]+;", "", text)
    return text.strip()


NOISE_PHRASES = {
    "clique nas imagens para aumentar",
    "clique para ampliar",
    "clique na imagem",
    "click to enlarge",
    "image", "video", "text", "heading", "button", "audio",
    "fonte:", "source:",
}


def is_noise(text: str) -> bool:
    """Retorna True se o texto é lixo (ID, caminho de imagem, frase de UI, etc.)."""
    t = text.strip()
    if not t or len(t) < 3:
        return True
    if UUID_RE.match(t):
        return True
    if IMAGE_EXT_RE.search(t) and " " not in t:
        return True
    if IMAGE_PATH_RE.search(t):
        return True
    if t.lower() in NOISE_PHRASES:
        return True
    # Frases de instrução de UI
    if re.match(r"^(sem\s+t[ií]tulo|untitled|image|imagem)\d*(\.(png|jpg|jpeg|gif))?$", t, re.I):
        return True
    if re.match(r"^clique\s+(nas?\s+imagens?|para\s+)", t, re.I):
        return True
    if t.startswith("http") and " " not in t:
        return True
    return False


def describe_image(image_url: str) -> str | None:
    """Usa GPT-4o Vision para descrever uma imagem. Retorna None se falhar."""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None
    try:
        resp = req.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": "gpt-4o-mini",
                "max_tokens": 200,
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": (
                            "Descreva brevemente esta imagem em português, "
                            "focando no conteúdo educacional relevante. "
                            "Máximo 2 frases."
                        )},
                        {"type": "image_url", "image_url": {"url": image_url, "detail": "low"}},
                    ],
                }],
            },
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception:
        return None


def extract_image_urls_from_json(data, base_url: str = "") -> list[str]:
    """Extrai URLs de imagens de um objeto JSON."""
    urls = []
    if isinstance(data, str):
        if IMAGE_EXT_RE.search(data):
            if data.startswith("http"):
                urls.append(data)
            elif data.startswith("rise/courses/") and base_url:
                urls.append(f"https://rise.articulate.com/{data}")
    elif isinstance(data, dict):
        for v in data.values():
            urls.extend(extract_image_urls_from_json(v, base_url))
    elif isinstance(data, list):
        for item in data:
            urls.extend(extract_image_urls_from_json(item, base_url))
    return urls


def extract_text_from_value(value) -> list[str]:
    """Recursivamente extrai strings de texto de um valor JSON."""
    texts = []
    if isinstance(value, str):
        clean = re.sub(r"<[^>]+>", " ", value)
        clean = decode_html(clean)
        clean = re.sub(r"\s+", " ", clean).strip()
        if clean and not is_noise(clean):
            texts.append(clean)
    elif isinstance(value, dict):
        for v in value.values():
            texts.extend(extract_text_from_value(v))
    elif isinstance(value, list):
        for item in value:
            texts.extend(extract_text_from_value(item))
    return texts


def parse_course_json(data: dict) -> list[dict]:
    """Extrai título e blocos de conteúdo do JSON do curso Rise."""
    import html as html_mod
    blocks = []
    seen = set()

    def clean(text: str) -> str:
        text = re.sub(r"<[^>]+>", " ", text)
        text = html_mod.unescape(text)
        return re.sub(r"\s+", " ", text).strip()

    def add(tag, text):
        text = clean(text) if isinstance(text, str) else ""
        if text and not is_noise(text) and text not in seen:
            seen.add(text)
            blocks.append({"type": tag, "text": text})

    # Título principal
    title = (
        data.get("title") or
        data.get("course", {}).get("title") or
        data.get("name", "")
    )
    if title:
        add("h1", title)

    # Descrição
    desc = data.get("description") or data.get("course", {}).get("description", "")
    if desc:
        add("p", desc)

    # Navega pelas lições
    lessons = (
        data.get("lessons") or
        data.get("course", {}).get("lessons") or
        data.get("items") or
        []
    )

    for lesson in lessons:
        lesson_title = lesson.get("title") or lesson.get("name", "")
        if lesson_title:
            add("h2", lesson_title)

        content_blocks = (
            lesson.get("blocks") or
            lesson.get("items") or
            lesson.get("content", {}).get("blocks") or
            []
        )
        for block in content_blocks:
            bdata = block.get("data") or block.get("content") or block

            for key in ["title", "heading"]:
                val = bdata.get(key, "") if isinstance(bdata, dict) else ""
                if val:
                    add("h3", val)

            for key in ["body", "text", "html", "value"]:
                val = bdata.get(key, "") if isinstance(bdata, dict) else ""
                if isinstance(val, str) and val:
                    add("p", val)
                elif isinstance(val, list):
                    for item in val:
                        for t in extract_text_from_value(item):
                            add("p", t)

            for key in ["items", "choices", "bullets"]:
                items = bdata.get(key, []) if isinstance(bdata, dict) else []
                if isinstance(items, list):
                    for item in items:
                        for t in extract_text_from_value(item):
                            add("li", t)

    # Fallback: extrai tudo recursivamente
    if len(blocks) <= 2:
        for t in extract_text_from_value(data):
            add("p", t)

    return blocks


def download_rise(url: str, use_vision: bool = False) -> tuple[str, list[dict]]:
    captured_data = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()

        # Intercepta respostas de API
        def on_response(response):
            rurl = response.url
            if any(k in rurl for k in [
                "api/v", "course", "lesson", "content", "rise.articulate.com"
            ]):
                try:
                    if "json" in response.headers.get("content-type", ""):
                        data = response.json()
                        captured_data.append(data)
                except Exception:
                    pass

        page.on("response", on_response)

        print("Carregando curso...")
        page.goto(url, wait_until="networkidle", timeout=60000)
        time.sleep(4)

        # Tenta clicar em cada item de menu para forçar carregamento
        print("Navegando pelas licoes...")
        menu_items = page.query_selector_all(
            "[class*='lesson'], [class*='menu-item'], [class*='sidebar'] li, "
            "[class*='nav-item'], [role='menuitem'], [role='listitem']"
        )
        for item in menu_items[:30]:
            try:
                if item.is_visible():
                    item.click()
                    time.sleep(2)
                    page.mouse.wheel(0, 5000)
                    time.sleep(1)
            except Exception:
                pass

        # Tenta botão "próximo" várias vezes
        for _ in range(60):
            next_btn = page.query_selector(
                "button[aria-label*='Next' i], button[aria-label*='next' i], "
                "[class*='next-btn'], [class*='btn--next'], "
                "button[class*='continue'], [data-testid*='next']"
            )
            if not next_btn:
                break
            try:
                next_btn.scroll_into_view_if_needed()
                next_btn.click()
                time.sleep(2)
                page.mouse.wheel(0, 3000)
                time.sleep(1)
            except Exception:
                break

        title = page.title() or "curso"
        browser.close()

    # Processa os JSONs capturados
    all_blocks: list[dict] = []
    seen_texts: set[str] = set()

    def merge(blocks):
        for b in blocks:
            if b["text"] not in seen_texts:
                seen_texts.add(b["text"])
                all_blocks.append(b)

    for data in captured_data:
        merge(parse_course_json(data))

    # Se ainda tiver pouco conteúdo, faz varredura bruta em todos os JSONs
    if len(all_blocks) < 5:
        for data in captured_data:
            for t in extract_text_from_value(data):
                if t not in seen_texts and len(t) > 5:
                    seen_texts.add(t)
                    all_blocks.append({"type": "p", "text": t})

    # Descrição de imagens via GPT-4o Vision
    if use_vision:
        image_urls = []
        seen_img = set()
        for data in captured_data:
            for img_url in extract_image_urls_from_json(data):
                if img_url not in seen_img:
                    seen_img.add(img_url)
                    image_urls.append(img_url)

        if image_urls:
            print(f"Descrevendo {len(image_urls)} imagem(ns) com GPT-4o Vision...")
            img_blocks = []
            for i, img_url in enumerate(image_urls, 1):
                print(f"  [{i}/{len(image_urls)}] {img_url[:60]}...")
                desc = describe_image(img_url)
                if desc:
                    img_blocks.append({"type": "p", "text": f"[Imagem {i}: {desc}]"})

            # Insere os blocos de imagem após o primeiro h2 que corresponder
            # (simplificado: adiciona todos ao final por enquanto)
            all_blocks.extend(img_blocks)

    return title, all_blocks


def blocks_to_markdown(title: str, blocks: list[dict]) -> str:
    lines = []
    for b in blocks:
        tag, text = b["type"], b["text"]
        if tag == "h1":
            lines.append(f"\n# {text}\n")
        elif tag == "h2":
            lines.append(f"\n## {text}\n")
        elif tag == "h3":
            lines.append(f"\n### {text}\n")
        elif tag in {"h4", "h5"}:
            lines.append(f"\n#### {text}\n")
        elif tag == "li":
            lines.append(f"- {text}")
        elif tag == "blockquote":
            lines.append(f"> {text}")
        else:
            lines.append(text)
    return "\n".join(lines)


def save_markdown(title: str, blocks: list[dict], filename: str):
    path = filename if filename.endswith(".md") else filename + ".md"
    content = blocks_to_markdown(title, blocks)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"Salvo: {path} ({len(blocks)} blocos)")


def save_pdf(title: str, blocks: list[dict], filename: str):
    path = filename if filename.endswith(".pdf") else filename + ".pdf"

    doc = SimpleDocTemplate(
        path, pagesize=A4,
        leftMargin=20*mm, rightMargin=20*mm,
        topMargin=20*mm, bottomMargin=20*mm,
    )

    base = getSampleStyleSheet()
    styles = {
        "h1": ParagraphStyle("h1", parent=base["Heading1"], fontSize=18, spaceAfter=8),
        "h2": ParagraphStyle("h2", parent=base["Heading2"], fontSize=14, spaceAfter=6),
        "h3": ParagraphStyle("h3", parent=base["Heading3"], fontSize=12, spaceAfter=4),
        "h4": ParagraphStyle("h4", parent=base["Heading4"], fontSize=11, spaceAfter=3),
        "h5": ParagraphStyle("h5", parent=base["Heading4"], fontSize=11, spaceAfter=3),
        "li": ParagraphStyle("li", parent=base["Normal"], fontSize=11,
                             leftIndent=12, bulletIndent=0, spaceAfter=2),
        "p":  ParagraphStyle("p",  parent=base["Normal"], fontSize=11, spaceAfter=4),
    }

    story = []
    for b in blocks:
        tag, text = b["type"], b["text"]
        # Escapa caracteres especiais do XML do ReportLab
        safe_text = (text
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;"))
        style = styles.get(tag, styles["p"])
        prefix = "• " if tag == "li" else ""
        try:
            story.append(Paragraph(prefix + safe_text, style))
        except Exception:
            story.append(Paragraph(prefix + safe_text.encode("ascii", "replace").decode(), style))

    doc.build(story)
    print(f"Salvo: {path} ({len(blocks)} blocos)")


def main():
    if len(sys.argv) < 2:
        print("Uso: python rise_downloader.py <url> [pdf|md] [--vision]")
        sys.exit(1)

    url = sys.argv[1]
    args = sys.argv[2:]
    fmt = next((a for a in args if a in ("pdf", "md")), "md")
    use_vision = "--vision" in args

    title, blocks = download_rise(url, use_vision=use_vision)

    if not blocks:
        print("Nenhum conteudo encontrado.")
        sys.exit(1)

    print(f"Titulo: {title} | Blocos: {len(blocks)}")
    filename = slugify(title) or "curso_rise"

    if fmt == "pdf":
        save_pdf(title, blocks, filename)
    else:
        save_markdown(title, blocks, filename)


if __name__ == "__main__":
    main()
