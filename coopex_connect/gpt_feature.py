"""Sugestao de texto para o atendente, sem envio automatico."""
import os
import json
import urllib.request

RULES = (
    "Voce atende clientes da COOPEX em portugues. Seja breve e cordial. "
    "Pergunte local de coleta e destino quando faltarem. Se o cliente "
    "pedir apenas um entregador, pergunte coleta e destino ou bairro. "
    "Para condominios, pergunte torre e apartamento. Nao invente preco, "
    "prazo, comprovacao de Pix ou disponibilidade. Somente atendente "
    "humano confirma entregas e atribuicoes. Retorne apenas a mensagem sugerida."
)

def suggest(messages):
    key = os.getenv("OPENAI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("OPENAI_API_KEY nao configurada")
    history = [{"role":"developer","content":RULES}]
    for item in messages[-12:]:
        role = "user" if item.direction == "in" else "assistant"
        history.append({"role":role,"content":item.body[:1800]})
    payload = json.dumps({
        "model":os.getenv("COOPEX_OPENAI_MODEL","gpt-4.1-mini"),
        "input":history,
        "max_output_tokens":250
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.openai.com/v1/responses", data=payload,
        headers={"Authorization":"Bearer "+key,"Content-Type":"application/json"},
        method="POST")
    with urllib.request.urlopen(req, timeout=20) as response:
        result = json.loads(response.read(200000).decode("utf-8"))
    parts = []
    for output in result.get("output", []):
        for part in output.get("content", []):
            if part.get("type") == "output_text":
                parts.append(part.get("text", ""))
    return "\n".join(parts).strip()[:1400]
