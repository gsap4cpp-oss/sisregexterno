import os, time, re
from fastapi import FastAPI, HTTPException, Header, Depends
from typing import Optional, List
from pydantic import BaseModel
from playwright.sync_api import sync_playwright

API_TOKEN = os.getenv("API_TOKEN", "troque-por-um-token-forte")

app = FastAPI(title="SISREG-DF Scraper (MPDFT Lista de Espera)")
@app.get("/")
def root():
    return {"status": "ok", "service": "sisreg-api"}

@app.get("/healthz")
def healthz():
    return {"status": "ok"}

def auth(authorization: Optional[str] = Header(None)):
    if API_TOKEN and authorization != f"Bearer {API_TOKEN}":
        raise HTTPException(status_code=401, detail="Unauthorized")

URL = "https://www.mpdft.mp.br/acompanhamento-sus-df/lista-de-espera"

def scrape_por_codigo(codigo: str) -> dict:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        page = browser.new_page()
        page.set_default_timeout(45000)
        page.goto(URL)

        # Força a opção "Código de solicitação"
        try:
            page.get_by_text("Código de solicitação", exact=False).first.click(timeout=3000)
        except:
            pass

        # Preenche o input do código (vários seletores para robustez)
        preencheu = False
        for sel in [
            'input[placeholder*="Código"]',
            'input[placeholder*="codigo"]',
            'input[placeholder*="solicita"]',
        ]:
            try:
                page.locator(sel).first.fill(codigo)
                preencheu = True
                break
            except:
                pass
        if not preencheu:
            try:
                page.get_by_label("Código de solicitação").fill(codigo)
                preencheu = True
            except:
                pass
        if not preencheu:
            page.locator('form input[type="text"]').first.fill(codigo)

        # Clica em Buscar
        try:
            page.get_by_role("button", name=re.compile("Buscar", re.I)).click()
        except:
            page.locator("button:has-text('Buscar')").first.click()

        # Espera a tabela
        page.wait_for_selector("tbody tr, .mat-table tbody tr", state="visible")

        # Lê cabeçalhos
        headers = [h.inner_text().strip().upper()
                   for h in page.locator("thead th").all()]
        if not headers:
            headers = [h.inner_text().strip().upper()
                       for h in page.locator(".table thead th").all()]

        # Pega a primeira linha
        tds = [td.inner_text().strip()
               for td in page.locator("tbody tr").first.locator("td").all()]

        browser.close()

    if not tds:
        raise HTTPException(404, f"Código {codigo} não encontrado ou sem dados.")

    def idx(nome_opts: List[str]) -> Optional[int]:
        for i, h in enumerate(headers):
            for n in nome_opts:
                if n in h:
                    return i
        return None

    i_proc = idx(["PROCEDIMENTO"])
    i_pos  = idx(["POSIÇÃO", "POSICAO"])
    i_temp = idx(["TEMPO DE ESPERA"])
    i_risk = idx(["CLASSIFICAÇÃO", "CLASSIFICACAO"])
    i_data = idx(["DATA DA SOLICITAÇÃO", "DATA DA SOLICITACAO"])

    def get(i): 
        return tds[i] if i is not None and i < len(tds) else ""

    def to_int(s):
        try:
            n = int(re.sub(r"\D", "", s or ""))
            return n
        except:
            return None

    return {
        "codigo_solicitacao": codigo,
        "procedimento": get(i_proc),
        "posicao": to_int(get(i_pos)),
        "tempo_espera_dias": to_int(get(i_temp)),
        "classificacao_risco": get(i_risk),
        "data_solicitacao": get(i_data),
        "status": ""
    }

@app.get("/consulta")
def consulta(codigo: str, _=Depends(auth)):
    return scrape_por_codigo(codigo)

class Lote(BaseModel):
    codigos: List[str]

@app.post("/consulta-lote")
def consulta_lote(body: Lote, _=Depends(auth)):
    out = []
    for c in body.codigos:
        c = str(c).strip()
        if not c:
            continue
        try:
            out.append(scrape_por_codigo(c))
        except HTTPException as e:
            out.append({"codigo_solicitacao": c, "error": e.detail})
        time.sleep(1.0)
    return {"resultados": out}
