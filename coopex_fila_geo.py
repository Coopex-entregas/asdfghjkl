"""Fila COOPEX: entrada por proximidade e saída com justificativa."""
from datetime import datetime
from math import radians, sin, cos, atan2, sqrt, isfinite
from flask import request, session, jsonify
from sqlalchemy import text

def install(app, db, Cooperado, ListaEspera, ConfigSistema, emitir_lista_espera):
    def ensure_schema():
        db.session.execute(text("""
            CREATE TABLE IF NOT EXISTS fila_saida_auditoria (
                id SERIAL PRIMARY KEY,
                cooperado_id INTEGER NOT NULL,
                cooperado_nome VARCHAR(120) NOT NULL,
                motivo VARCHAR(500) NOT NULL,
                ocorrido_em TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """))
        db.session.commit()

    def config(chave):
        item=ConfigSistema.query.filter_by(chave=chave).first()
        return (item.valor or '').strip() if item else ''

    def base():
        endereco=config('coopex_base_endereco')
        try:
            lat=float(config('coopex_base_latitude'))
            lng=float(config('coopex_base_longitude'))
        except (ValueError,TypeError):
            return None
        if not endereco or not isfinite(lat) or not isfinite(lng) or abs(lat)>90 or abs(lng)>180:
            return None
        return {'endereco':endereco,'latitude':lat,'longitude':lng,'raio_m':100}

    def authorized():
        uid=session.get('user_id')
        if not uid or session.get('is_admin'):
            return None
        coop=db.session.get(Cooperado,int(uid))
        return coop if coop and getattr(coop,'ativo',True) else None

    def meters(lat1,lon1,lat2,lon2):
        p1,p2=radians(lat1),radians(lat2)
        dlat=p2-p1; dlon=radians(lon2-lon1)
        a=sin(dlat/2)**2+cos(p1)*cos(p2)*sin(dlon/2)**2
        return 6371000*2*atan2(sqrt(a),sqrt(max(0,1-a)))

    @app.get('/api/cooperado/fila/base')
    def coopex_fila_geo_base():
        if not authorized():return jsonify(ok=False,erro='Não autorizado'),403
        item=base()
        if not item:return jsonify(ok=False,configurada=False,erro='Base ainda não configurada no Admin → Certificado'),503
        return jsonify(ok=True,configurada=True,base=item)

    @app.post('/api/cooperado/fila/entrar')
    def coopex_fila_geo_entrar():
        coop=authorized()
        if not coop:return jsonify(ok=False,erro='Não autorizado'),403
        item=base()
        if not item:return jsonify(ok=False,erro='Configure a base no Admin → Certificado'),503
        payload=request.get_json(silent=True) or {}
        try:
            lat=float(payload['latitude']);lng=float(payload['longitude'])
            precision=float(payload['precisao_m'])
            measured_at=int(payload['timestamp_ms'])
        except (KeyError,ValueError,TypeError):
            return jsonify(ok=False,erro='Localização inválida. Ative o GPS de alta precisão.'),400
        now_ms=int(datetime.utcnow().timestamp()*1000)
        if not all(isfinite(v) for v in (lat,lng,precision)) or not (-90<=lat<=90 and -180<=lng<=180):
            return jsonify(ok=False,erro='Coordenadas inválidas'),400
        if precision<0 or precision>50:
            return jsonify(ok=False,erro='Precisão insuficiente. Aguarde um sinal GPS melhor (até 50 m).'),409
        if abs(now_ms-measured_at)>30000:
            return jsonify(ok=False,erro='Localização antiga. Atualize o GPS e tente novamente.'),409
        dist=meters(item['latitude'],item['longitude'],lat,lng)
        # Considerar incerteza: não aceitar se o círculo de precisão alcançar fora do raio.
        if dist+precision>100:
            return jsonify(ok=False,erro='Você precisa estar dentro do raio de 100 metros da base.',distancia_m=round(dist),precisao_m=round(precision)),403
        if ListaEspera.query.filter_by(cooperado_id=coop.id).first():
            return jsonify(ok=True,ja_estava=True,mensagem='Você já está na fila.')
        max_pos=db.session.query(db.func.max(ListaEspera.pos)).scalar() or 0
        row=ListaEspera(cooperado_id=coop.id,nome=coop.nome,pos=max_pos+1,created_at=datetime.utcnow())
        try:
            db.session.add(row);db.session.commit()
            emitir_lista_espera()
        except Exception:
            db.session.rollback()
            app.logger.exception('Falha ao entrar na fila')
            return jsonify(ok=False,erro='Não foi possível entrar na fila.'),500
        return jsonify(ok=True,entrou=True,posicao=row.pos)

    @app.post('/api/cooperado/fila/sair')
    def coopex_fila_geo_sair():
        coop=authorized()
        if not coop:return jsonify(ok=False,erro='Não autorizado'),403
        data=request.get_json(silent=True) or {}
        motivo=str(data.get('motivo') or '').strip()
        if len(motivo)<5 or len(motivo)>500:
            return jsonify(ok=False,erro='Informe o motivo da saída (5 a 500 caracteres).'),400
        row=ListaEspera.query.filter_by(cooperado_id=coop.id).first()
        if not row:return jsonify(ok=False,erro='Você não está na fila.'),409
        try:
            ensure_schema()
            db.session.execute(text(
                'INSERT INTO fila_saida_auditoria (cooperado_id,cooperado_nome,motivo,ocorrido_em) '
                'VALUES (:cid,:nome,:motivo,CURRENT_TIMESTAMP)'
            ),{'cid':coop.id,'nome':coop.nome,'motivo':motivo})
            db.session.delete(row)
            # Reindexar após a saída para posições contínuas.
            db.session.flush()
            itens=ListaEspera.query.order_by(ListaEspera.pos.asc(),ListaEspera.created_at.asc(),ListaEspera.id.asc()).all()
            for i,it in enumerate(itens,1): it.pos=i
            db.session.commit()
            emitir_lista_espera()
            # O motivo e reservado aos administradores autenticados na sala LED.
            try:
                from app import socketio
                socketio.emit('coopex_fila_saida_alerta', {
                    'cooperado_id': coop.id,
                    'nome': coop.nome,
                    'motivo': motivo,
                    'quando': datetime.utcnow().isoformat() + 'Z'
                }, to='coopex-admin-led')
            except Exception:
                app.logger.exception('Saida salva, mas aviso LED nao emitido')
        except Exception:
            db.session.rollback()
            app.logger.exception('Falha ao sair da fila')
            return jsonify(ok=False,erro='Não foi possível registrar a saída.'),500
        return jsonify(ok=True,mensagem='Você saiu da fila. O motivo foi registrado para o administrador.')

    @app.get('/api/admin/fila/saidas')
    def coopex_fila_geo_auditoria():
        if not session.get('is_admin'):return jsonify(ok=False,erro='Não autorizado'),403
        ensure_schema()
        rows=db.session.execute(text(
            "SELECT cooperado_nome,motivo,ocorrido_em FROM fila_saida_auditoria "
            "ORDER BY ocorrido_em DESC LIMIT 150"
        )).mappings().all()
        return jsonify(ok=True,itens=[
            {'nome':r['cooperado_nome'],'motivo':r['motivo'],
             'quando':r['ocorrido_em'].isoformat() if r['ocorrido_em'] else ''}
            for r in rows
        ])
