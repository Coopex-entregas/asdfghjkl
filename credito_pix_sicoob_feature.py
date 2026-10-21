"""Compra de credito Pix Sicoob por cliente: conciliacao exata e lancamento idempotente."""
from datetime import datetime
from decimal import Decimal, InvalidOperation
from io import BytesIO
from flask import abort, request, jsonify, session, send_file
from sqlalchemy import text
from cryptography.fernet import Fernet
from requests_pkcs12 import Pkcs12Adapter
import requests, qrcode

BASE="https://api.sicoob.com.br/pix/api/v2"
AUTH="https://auth.sicoob.com.br/auth/realms/cooperado/protocol/openid-connect/token"

def install(h):
    app,db=h.app,h.db
    Sol=h.SolicitacaoCreditoCliente
    Credito,Mov,Cliente=h.Credito,h.CreditoMovimento,h.Cliente
    with app.app_context():
        db.session.execute(text("""CREATE TABLE IF NOT EXISTS sicoob_credito_pix(
          solicitacao_id INTEGER PRIMARY KEY,
          cliente_id INTEGER NOT NULL,
          txid VARCHAR(35) NOT NULL UNIQUE,
          valor VARCHAR(20) NOT NULL,
          brcode TEXT,
          status VARCHAR(20) NOT NULL DEFAULT 'CRIANDO',
          e2eid VARCHAR(60) UNIQUE,
          pago_em VARCHAR(40),
          credito_id INTEGER
        )"""))
        db.session.commit()

    def me():
        cid=session.get("cliente_id")
        if not cid or h._cliente_em_modo_vendedor():
            abort(403)
        return int(cid)

    def request_owned(sid):
        req=Sol.query.filter_by(id=sid,cliente_id=me()).first()
        if not req:abort(404)
        return req

    def bank_call(method, endpoint, body=None):
        row=db.session.execute(text("SELECT client_id,chave,certificado,senha FROM sicoob_pix_config WHERE id=1")).mappings().first()
        if not row:raise RuntimeError("Sicoob não configurado")
        cypher=Fernet(__import__("os").environ["SICOOB_CONFIG_FERNET_KEY"].encode())
        pfx=cypher.decrypt(bytes(row["certificado"]))
        pwd=cypher.decrypt(bytes(row["senha"])).decode()
        with requests.Session() as s:
            s.mount("https://",Pkcs12Adapter(pkcs12_data=pfx,pkcs12_password=pwd))
            s.headers.update({"client_id":row["client_id"],"Accept":"application/json"})
            tok=s.post(AUTH,data={"grant_type":"client_credentials","client_id":row["client_id"],"scope":"cob.write cob.read pix.read"},timeout=20)
            tok.raise_for_status()
            s.headers["Authorization"]="Bearer "+tok.json()["access_token"]
            response=s.request(method,BASE+endpoint,json=body,timeout=25)
            response.raise_for_status()
            return (response.json() if response.content else {}),row["chave"]

    def amt(v):
        try:
            n=Decimal(str(v)).quantize(Decimal(".01"))
            if not n.is_finite() or n<=0:raise ValueError()
            return n
        except (ValueError,InvalidOperation):raise ValueError("Valor inválido")

    def get_row(sid):
        return db.session.execute(text("SELECT * FROM sicoob_credito_pix WHERE solicitacao_id=:sid"),{"sid":sid}).mappings().first()

    def get_code(data):
        return data.get("pixCopiaECola") or data.get("brcode") or (data.get("loc") or {}).get("brcode")

    def reconcile(req,row):
        if row["status"]=="PAGO" and req.status=="aprovado":
            return True
        data,_=bank_call("GET","/cob/"+row["txid"])
        pix=data.get("pix") or []
        matches=[p for p in pix if p.get("endToEndId") and p.get("txid",row["txid"])==row["txid"]]
        received=sum((Decimal(str(p.get("valor","0"))) for p in matches),Decimal("0"))
        if data.get("status")!="CONCLUIDA" or not matches or received!=Decimal(row["valor"]):
            return False
        # One database transaction; row locks ensure concurrent polling cannot double-credit.
        locked=Sol.query.filter_by(id=req.id,cliente_id=row["cliente_id"]).with_for_update().first()
        if not locked:raise ValueError("Solicitação não localizada")
        saved=db.session.execute(text("SELECT credito_id,status FROM sicoob_credito_pix WHERE solicitacao_id=:sid FOR UPDATE"),{"sid":req.id}).mappings().first() if db.engine.dialect.name=="postgresql" else get_row(req.id)
        if locked.status=="aprovado" or saved["credito_id"]:
            return True
        if locked.status!="pendente":
            raise ValueError("Solicitação já finalizada; exige conferência administrativa")
        cli=Cliente.query.filter_by(id=locked.cliente_id).with_for_update().first()
        if not cli:raise ValueError("Cliente não localizado")
        n=Decimal(row["valor"])
        old=Decimal(str(cli.saldo_atual or 0)).quantize(Decimal(".01"))
        new=old+n
        credit=Credito(cliente_id=cli.id,valor=float(n),valor_bruto=float(n),
            desconto_tipo="nenhum",desconto_valor=0,valor_final=float(n),
            saldo_antes=float(old),saldo_depois=float(new),
            motivo="Pix Sicoob - solicitação #"+str(locked.id),
            criado_por="Sicoob (confirmado)")
        db.session.add(credit);db.session.flush()
        db.session.add(Mov(credito_id=credit.id,cliente_id=cli.id,tipo="credito",
            valor=float(n),referencia="Pix Sicoob "+row["txid"]))
        cli.saldo_atual=float(new)
        locked.status="aprovado";locked.credito_id=credit.id
        locked.decidido_em=datetime.utcnow();locked.decidido_por="Sicoob"
        db.session.execute(text("""UPDATE sicoob_credito_pix SET status='PAGO',e2eid=:e,pago_em=:t,credito_id=:c
            WHERE solicitacao_id=:sid"""),{"e":matches[0]["endToEndId"],
                "t":matches[0].get("horario") or datetime.utcnow().isoformat(),
                "c":credit.id,"sid":locked.id})
        db.session.commit()
        return True

    @app.post("/api/cliente/pix-credito/<int:sid>/criar")
    def pix_credito_criar(sid):
        req=request_owned(sid)
        if req.status!="pendente":return jsonify(ok=False,erro="Solicitação já processada"),409
        row=get_row(sid)
        if not row:
            txid=("COOPEXCR"+str(sid).zfill(12))[:35]
            db.session.execute(text("""INSERT INTO sicoob_credito_pix(solicitacao_id,cliente_id,txid,valor)
                VALUES(:sid,:cid,:tx,:v) ON CONFLICT(solicitacao_id) DO NOTHING"""),
                {"sid":sid,"cid":req.cliente_id,"tx":txid,"v":str(amt(req.valor))})
            db.session.commit()
            row=get_row(sid)
        if row["cliente_id"]!=req.cliente_id:abort(403)
        if row["status"]=="PAGO":return jsonify(ok=False,erro="Pix já pago"),409
        if row["brcode"]:
            return jsonify(ok=True,solicitacao_id=sid,valor=row["valor"],brcode=row["brcode"],
                qrcode_url="/api/cliente/pix-credito/"+str(sid)+"/qr",status=row["status"])
        try:
            cfg=db.session.execute(text("SELECT chave FROM sicoob_pix_config WHERE id=1")).scalar()
            if not cfg:raise ValueError("Chave Pix não configurada")
            payload={"calendario":{"expiracao":3600},"valor":{"original":row["valor"]},
                     "chave":cfg,"solicitacaoPagador":"Crédito COOPEX #"+str(sid)}
            try:
                data,_=bank_call("GET","/cob/"+row["txid"])
            except requests.HTTPError as exc:
                if exc.response is None or exc.response.status_code!=404:raise
                data,_=bank_call("PUT","/cob/"+row["txid"],payload)
            code=get_code(data)
            if not code:
                data,_=bank_call("GET","/cob/"+row["txid"])
                code=get_code(data)
            if not code or not str(code).startswith("000201"):
                return jsonify(ok=False,erro="Código Pix ainda não disponível. Reabra a mesma solicitação."),503
            db.session.execute(text("UPDATE sicoob_credito_pix SET brcode=:code,status='PENDENTE' WHERE solicitacao_id=:sid"),
                {"code":code,"sid":sid});db.session.commit()
            return jsonify(ok=True,solicitacao_id=sid,valor=row["valor"],brcode=code,
                qrcode_url="/api/cliente/pix-credito/"+str(sid)+"/qr",status="PENDENTE")
        except Exception:
            app.logger.exception("Falha na criacao Pix credito")
            db.session.rollback()
            return jsonify(ok=False,erro="Não foi possível obter o Pix. Consulte a mesma solicitação; não faça outro pedido."),502

    @app.get("/api/cliente/pix-credito/<int:sid>/qr")
    def pix_credito_qr(sid):
        request_owned(sid)
        row=get_row(sid)
        if not row or not row["brcode"]:abort(404)
        pic=qrcode.make(row["brcode"])
        buf=BytesIO();pic.save(buf,format="PNG");buf.seek(0)
        resp=send_file(buf,mimetype="image/png");resp.headers["Cache-Control"]="no-store"
        return resp

    @app.get("/api/cliente/pix-credito/<int:sid>/status")
    def pix_credito_status(sid):
        req=request_owned(sid);row=get_row(sid)
        if not row:return jsonify(ok=False,erro="Cobrança não iniciada"),404
        try:
            paid=reconcile(req,row)
        except Exception:
            app.logger.exception("Falha na consulta Pix credito")
            db.session.rollback()
            return jsonify(ok=False,erro="Falha ao confirmar com o Sicoob; o crédito não foi liberado."),502
        return jsonify(ok=True,pago=bool(paid),status="PAGO" if paid else row["status"],
            saldo=float(Cliente.query.get(req.cliente_id).saldo_atual or 0))

    @app.get("/api/cliente/pix-credito/<int:sid>")
    def pix_credito_atual(sid):
        request_owned(sid)
        row=get_row(sid)
        if not row:return jsonify(ok=False,erro="Cobrança não encontrada"),404
        return jsonify(ok=True,solicitacao_id=sid,valor=row["valor"],status=row["status"],
            brcode=row["brcode"],qrcode_url="/api/cliente/pix-credito/"+str(sid)+"/qr")

    @app.get("/api/admin/pix-creditos/novas-vendas")
    def pix_credito_vendas_admin():
        if not session.get("is_admin"):
            abort(403)
        rows=db.session.execute(text("""SELECT p.solicitacao_id, p.valor, p.pago_em,
            c.nome AS cliente_nome
            FROM sicoob_credito_pix p
            JOIN cliente c ON c.id=p.cliente_id
            WHERE p.status='PAGO' AND p.credito_id IS NOT NULL
            ORDER BY p.solicitacao_id DESC LIMIT 30""")).mappings().all()
        response=jsonify(vendas=[dict(x) for x in rows])
        response.headers["Cache-Control"]="no-store"
        return response
