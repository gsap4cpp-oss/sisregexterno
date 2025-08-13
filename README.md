# SISREG-DF Scraper API (MPDFT Lista de Espera)

API simples que consulta a página pública do MPDFT por **Código de solicitação** e retorna:
- procedimento
- posição
- tempo_espera_dias
- classificação_risco
- data_solicitação

## Variáveis
- `API_TOKEN` (obrigatória): token de acesso. A planilha envia `Authorization: Bearer <API_TOKEN>`.

## Executar local
```bash
pip install -r requirements.txt
python -m playwright install --with-deps chromium
export API_TOKEN="SISREG-DF-SEU-TOKEN"
uvicorn main:app --reload
# http://127.0.0.1:8000/consulta?codigo=616676804 (Header Authorization: Bearer SISREG-DF-SEU-TOKEN)
```

## Deploy com Docker
Crie um repositório com estes arquivos e use Render/Railway/Docker:
```bash
docker build -t sisreg-api .
docker run -e API_TOKEN="SISREG-DF-SEU-TOKEN" -p 8000:8000 sisreg-api
```