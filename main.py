import os
import re
import unicodedata
import tempfile
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# Importa os scrapers existentes
sys_path_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
import sys
sys.path.insert(0, sys_path_dir)

from rise_downloader import download_rise, save_markdown, save_pdf
from downloader import fetch, to_markdown, to_pdf
from urllib.parse import urlparse

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class DownloadRequest(BaseModel):
    url: str
    format: str = "pdf"   # "pdf" ou "md"
    vision: bool = False


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", errors="ignore").decode("ascii")
    return re.sub(r"[^\w\-]", "_", text)[:60] or "download"


def is_spa(url: str) -> bool:
    spa_hosts = ["rise.articulate.com", "360.articulate.com"]
    host = urlparse(url).netloc
    return any(h in host for h in spa_hosts)


@app.post("/download")
async def download(req: DownloadRequest):
    fmt = req.format.lower()
    if fmt not in ("pdf", "md"):
        raise HTTPException(400, "Formato deve ser 'pdf' ou 'md'")

    with tempfile.TemporaryDirectory() as tmpdir:
        try:
            if is_spa(req.url):
                title, blocks = download_rise(req.url, use_vision=req.vision)
                if not blocks:
                    raise HTTPException(422, "Nenhum conteúdo encontrado na URL.")
                filename = os.path.join(tmpdir, slugify(title))
                if fmt == "pdf":
                    save_pdf(title, blocks, filename)
                    out = filename + ".pdf"
                else:
                    save_markdown(title, blocks, filename)
                    out = filename + ".md"
            else:
                title, markdown = fetch(req.url)
                filename = os.path.join(tmpdir, slugify(title))
                if fmt == "pdf":
                    to_pdf(title, markdown, filename)
                    out = filename + ".pdf"
                else:
                    to_markdown(title, markdown, filename)
                    out = filename + ".md"
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(500, f"Erro ao processar: {str(e)}")

        media = "application/pdf" if fmt == "pdf" else "text/markdown"
        basename = os.path.basename(out)
        return FileResponse(
            path=out,
            media_type=media,
            filename=basename,
            background=None,
        )


@app.get("/health")
def health():
    return {"status": "ok"}
