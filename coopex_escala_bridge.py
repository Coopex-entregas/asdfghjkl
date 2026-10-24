"""Ponte entre escalas COOPEX, limitada a lotes e autenticada por segredo."""
import os,hmac,re,threading,unicodedata
from datetime import date
from flask import request,jsonify
from sqlalchemy import text
ROLE=os.getenv("COOPEX_ESCALA_ROLE","")
PEER=os.getenv("COOPEX_ESCALA_PEER_URL","").rstrip("/")
TOKEN=os.getenv("COOPEX_ESCALA_SYNC_TOKEN","")
def norm(v):
 return re.sub(r"\\s+"," ","".join(c for c in unicodedata.normalize("NFD",str(v or "").lower()) if unicodedata.category(c)!="Mn")).strip()
def install(m,feature=None):
 if ROLE not in ("financeiro","escalas"):return
 app,db=m.app,m.db
 Model=m.Escala if ROLE=="financeiro" else feature.Item
 Co=m.Cooperado
 with app.app_context():
  db.session.execute(text("CREATE TABLE IF NOT EXISTS escala_sync_link(source VARCHAR(30),remote_id BIGINT,local_id BIGINT,PRIMARY KEY(source,remote_id))"));db.session.commit()
 def name(x):
  return x.cooperado.nome if x.cooperado_id and x.cooperado else (x.cooperado_nome if ROLE=="financeiro" else x.nome_planilha) or ""
 def dump(x):
  return dict(id=x.id,data=str(x.data)[:10],turno=x.turno or "",horario=(x.horario if ROLE=="financeiro" else x.horario_texto) or "",contrato=x.contrato or "",cooperado_nome=name(x))
 def publish(rows):
  if not TOKEN or not PEER or not rows:return
  data=[dump(x) for x in rows]
  def send():
   import requests
   try:
    r=requests.post(PEER+"/integracao/escala/sync",json={"source":ROLE,"records":data},headers={"Authorization":"Bearer "+TOKEN},timeout=(2,5))
    if not r.ok:app.logger.warning("Escala sync remoto HTTP %s",r.status_code)
   except Exception:app.logger.warning("Escala remota indisponivel; cadastro local mantido")
  threading.Thread(target=send,daemon=True).start()
 def co(nome):
  return next((c for c in Co.query.all() if norm(c.nome)==norm(nome)),None)
 @app.post("/integracao/escala/sync")
 def receber_escala():
  if not TOKEN or not hmac.compare_digest(request.headers.get("Authorization",""),"Bearer "+TOKEN):return jsonify(ok=False),403
  payload=request.get_json(silent=True) or {};src=payload.get("source");rows=payload.get("records")
  if src not in ("financeiro","escalas") or src==ROLE or not isinstance(rows,list) or len(rows)>1500:return jsonify(ok=False),400
  total=0
  try:
   for r in rows:
    if not isinstance(r,dict):continue
    rid=int(r.get("id") or 0)
    if rid<=0:continue
    dia=date.fromisoformat(str(r.get("data") or "")[:10]);contrato=str(r.get("contrato") or "").strip()[:80]
    nome=str(r.get("cooperado_nome") or "").strip()[:120];horario=str(r.get("horario") or "").strip()
    if not contrato or not nome:continue
    mid=db.session.execute(text("SELECT local_id FROM escala_sync_link WHERE source=:s AND remote_id=:r"),{"s":src,"r":rid}).scalar()
    item=db.session.get(Model,int(mid)) if mid else None
    if item is None:
     matches=Model.query.filter(Model.data==dia.isoformat() if ROLE=="financeiro" else Model.data==dia).limit(700).all()
     item=next((x for x in matches if norm(x.contrato)==norm(contrato) and norm(x.horario if ROLE=="financeiro" else x.horario_texto)==norm(horario) and norm(name(x))==norm(nome)),None)
    coop=co(nome)
    if item is None:
     item=Model() if ROLE=="financeiro" else Model(data=dia,contrato=contrato,nome_planilha=nome,nome_norm=norm(nome)[:160],intervalos_json="[]",candidatos_json="[]",status_match="confirmado" if coop else "nao_encontrado")
     db.session.add(item)
    item.data=dia.isoformat() if ROLE=="financeiro" else dia
    item.turno=str(r.get("turno") or "")[:30];item.contrato=contrato;item.cooperado_id=coop.id if coop else None
    if ROLE=="financeiro":
     item.horario=horario[:50];item.cooperado_nome=None if coop else nome
    else:
     item.horario_texto=horario[:120];item.nome_planilha=nome;item.nome_norm=norm(nome)[:160]
    db.session.flush()
    db.session.execute(text("INSERT INTO escala_sync_link(source,remote_id,local_id) VALUES(:s,:r,:l) ON CONFLICT(source,remote_id) DO UPDATE SET local_id=EXCLUDED.local_id"),{"s":src,"r":rid,"l":item.id});total+=1
   db.session.commit()
   if ROLE=="escalas":feature.clear_cache()
   return jsonify(ok=True,atualizados=total)
  except Exception:
   db.session.rollback();app.logger.exception("Erro ponte escala");return jsonify(ok=False),500
 m._coopex_escala_publish=publish
