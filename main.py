import os, re, time
from typing import Optional, List

from fastapi import FastAPI, HTTPException, Header, Depends
from pydantic import BaseModel
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

# =======================
# Config
# =======================
API_TOKEN = (os.getenv("API_TOKEN") or "").strip()
URL = "https://www.mpdft.mp.br/acompanhamento-sus-df/lista-de-espera"

app = FastAPI(title="SISREG-DF Scraper (MPDFT)")

# =======================
# Rotas básicas / saúde
# =======================
@app.get("/")
def root():
    return {"status": "ok", "service": "sisreg-api"}

@app.get("/healthz")
def health():
    return {"status": "ok"}

@app.get("/debug/auth")
def debug_auth(authorization: Optional[str] = Header(None),
               x_api_token: Optional[str] = Header(None)):
    return {
        "has_authorization": bool(authorization),
        "auth_sample": (authorization[:12] if authorization else None),
        "x_api_token_len": (len(x_api_token) if x_api_token else 0)
    }

# =======================
# Auth (tolerante)
# =======================
def auth(authorization: Optional[str] = Header(None),
         x_api_token: Optional[str] = Header(None)):
    token = None
    if authorization:
        a = authorization.strip()
        token = a[7:].strip() if a.lower().startswith("bearer ") else a
    if not token and x_api_token:
        token = x_api_token.strip()
    if API_TOKEN and token != API_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")

# =======================
# Utils
# =======================
def _to_int(s: str) -> Optional[int]:
    try:
        v = re.sub(r"\D", "", s or "")
        return int(v) if v else None
    except:
        return None

# --- helper: habilita e preenche o campo do código ---
def _habilitar_e_preencher_codigo(page, codigo: str):
    # 1) marca o rádio "Código de solicitação"
    try:
        page.get_by_text("Código de solicitação", exact=False).first.click(timeout=3000)
    except:
        try:
            page.get_by_role("radio", name=re.compile("Código.*solicita", re.I)).check(timeout=3000)
        except:
            page.locator('input[type="radio"]').first.click(timeout=3000)

    # 2) localiza o input (vários seletores de fallback)
    candidatos = [
        'input[placeholder*="Código" i]',
        'input[placeholder*="codigo" i]',
        'input[placeholder*="solic" i]',
        'input[id*="codigo" i]',
        'input[name*="codigo" i]',
        'form input[type="text"]'
    ]
    campo = None
    for css in candidatos:
        loc = page.locator(css).first
        try:
            loc.wait_for(state="visible", timeout=4000)
            campo = loc
            break
        except:
            continue
    if not campo:
        raise HTTPException(400, "Não localizei o campo de código na página.")

    # 3) tenta até ficar habilitado/editável; se não, destrava via JS
    for _ in range(12):  # ~6s
        try:
            if campo.is_enabled() and campo.is_editable():
                campo.fill(codigo, timeout=3000)
                break
        except:
            pass
        # remover disabled/readonly/aria-disabled
        try:
            page.evaluate(
                """(sel)=>{
                    const el=document.querySelector(sel);
                    if(!el) return;
                    el.removeAttribute('disabled');
                    el.removeAttribute('aria-disabled');
                    el.removeAttribute('readonly');
                }""",
                campo.selector
            )
        except:
            pass
        page.wait_for_timeout(500)
    else:
        # força valor + eventos caso não tenha conseguido fill
        page.evaluate(
            """(sel, val)=>{
                const el=document.querySelector(sel);
                if(!el) return;
                el.removeAttribute('disabled');
                el.removeAttribute('readonly');
                el.value = val;
                el.dispatchEvent(new Event('input', {bubbles:true}));
                el.dispatchEvent(new Event('change', {bubbles:true}));
            }""",
            campo.selector, codigo
        )
    page.wait_for_timeout(300)  # pequena pausa p/ framework processar

# =======================
# Scraping
# =======================
def scrape_por_codigo(codigo: str) -> dict:
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
            page = browser.new_page()
            page.set_default_timeout(45000)
            page.set_default_navigation_timeout(45000)

            page.goto(URL, wait_until="domcontentloaded")
            page.wait_for_load_state("networkidle")

            # Preenche o código (com helper que garante habilitar o campo)
            _habilitar_e_preencher_codigo(page, codigo)

            # Clica em "Buscar"
            try:
                page.get_by_role("button", name=re.compile("Buscar", re.I)).click(timeout=3000)
            except:
                page.locator("button:has-text('Buscar')").first.click()

            # Espera resultados
            try:
                page.wait_for_selector("tbody tr", state="visible", timeout=20000)
            except PWTimeout:
                html_l = page.content().lower()
                if "0 registro" in html_l or "nenhum registro" in html_l:
                    raise HTTPException(404, f"Código {codigo} sem registros.")
                raise HTTPException(504, "Tempo excedido aguardando resultados.")

            # Coleta cabeçalhos (se existirem)
            headers = [h.inner_text().strip().upper()
                       for h in page.locator("thead th").all()]
            # Primeira linha de dados
            tds = [td.inner_text().strip()
                   for td in page.locator("tbody tr").first.locator("td").all()]

            browser.close()

        if not tds:
            raise HTTPException(404, f"Código {codigo} não encontrado ou sem dados.")

        # Mapeia colunas por nome com tolerância
        def idx(possiveis: List[str]) -> Optional[int]:
            for i, h in enumerate(headers or []):
                for n in possiveis:
                    if n in h:
                        return i
            return None

        i_proc = idx(["PROCEDIMENTO"])
        i_pos  = idx(["POSIÇÃO", "POSICAO"])
        i_temp = idx(["TEMPO DE ESPERA"])
        i_risk = idx(["CLASSIFICAÇÃO", "CLASSIFICACAO"])
        i_data = idx(["DATA DA SOLICITAÇÃO", "DATA DA SOLICITACAO"])

        def get(i): return tds[i] if i is not None and i < len(tds) else ""

        return {
            "codigo_solicitacao": codigo,
            "procedimento": get(i_proc) or (tds[0] if tds else ""),
            "posicao": _to_int(get(i_pos) or (tds[1] if len(tds) > 1 else "")),
            "tempo_espera_dias": _to_int(get(i_temp) or (tds[2] if len(tds) > 2 else "")),
            "classificacao_risco": get(i_risk) or (tds[4] if len(tds) > 4 else ""),
            "data_solicitacao": get(i_data) or (tds[5] if len(tds) > 5 else ""),
            "status": ""
        }

    except HTTPException:
        raise
    except Exception as e:
        # transforma qualquer erro inesperado em 502 com detalhe
        raise HTTPException(502, f"Falha no scraping: {type(e).__name__}: {e}")

# =======================
# Rotas da API
# =======================
@app.get("/consulta")
def consulta(codigo: str, _=Depends(auth)):
    codigo = str(codigo).strip()
    if not codigo:
        raise HTTPException(400, "Código vazio.")
    return scrape_por_codigo(codigo)

class Lote(BaseModel):
    codigos: List[str]

@app.post("/consulta-lote")
def consulta_lote(body: Lote, _=Depends(auth)):
    out = []
    for c in (body.codigos or []):
        c = str(c).strip()
        if not c:
            continue
        try:
            out.append(scrape_por_codigo(c))
        except HTTPException as e:
            out.append({"codigo_solicitacao": c, "error": e.detail})
        time.sleep(0.8)  # gentileza com o site
    return {"resultados": out}
