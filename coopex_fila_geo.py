"""Fila COOPEX: entrada por proximidade e saída com justificativa."""
from datetime import datetime
from math import radians, sin, cos, atan2, sqrt, isfinite
from flask import request, session, jsonify
from sqlalchemy import text

def install(app, db, Cooperado, ListaEspera, ConfigSistema, emitir_lista_espera):
    _audit_schema_checked = False

    def ensure_schema():
        # DDL por operação pode concorrer com outros requests e atrasar a fila.
        # Uma vez conferida no processo, a tabela não precisa ser recriada a cada saída.
        nonlocal _audit_schema_checked
        if _audit_schema_checked:
            return
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
        _audit_schema_checked = True

    def config(chave):
        item=ConfigSistema.query.filter_by(chave=chave).first()
        return (item.valor or '').strip() if item else ''

    # A configuração muda raramente. Consultar três chaves em uma operação.
    # Apenas a consulta visual usa cache; a entrada valida a base atual do banco.
    _base_cache = {'value': None, 'expires': 0.0}

    def base(allow_cache=False):
        import time
        now = time.monotonic()
        if allow_cache and now < _base_cache['expires']:
            return _base_cache['value']
        nomes = ('coopex_base_endereco', 'coopex_base_latitude', 'coopex_base_longitude')
        rows = ConfigSistema.query.filter(ConfigSistema.chave.in_(nomes)).all()
        values = {item.chave: (item.valor or '').strip() for item in rows}
        endereco = values.get('coopex_base_endereco', '')
        try:
            lat = float(values.get('coopex_base_latitude', ''))
            lng = float(values.get('coopex_base_longitude', ''))
        except (ValueError, TypeError):
            item = None
        else:
            item = ({'endereco': endereco, 'latitude': lat, 'longitude': lng, 'raio_m': 100}
                    if endereco and isfinite(lat) and isfinite(lng) and abs(lat)<=90 and abs(lng)<=180
                    else None)
        _base_cache['value'] = item
        _base_cache['expires'] = now + 30
        return item

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
        item=base(allow_cache=True)
        if not item:return jsonify(ok=False,configurada=False,erro='Base ainda não configurada no Admin → Certificado'),503
        return jsonify(ok=True,configurada=True,base={'endereco':item['endereco'],'latitude':item['latitude'],'longitude':item['longitude']})

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
        if dist+precision>item['raio_m']:
            return jsonify(ok=False,erro='Para entrar na fila, você precisa estar na cooperativa. Aproxime-se da base e tente novamente.'),403
        # Serializar entradas e saídas: a posição máxima deve refletir as alterações
        # já confirmadas por outra operação concorrente, inclusive em outros workers.
        db.session.execute(text("SELECT pg_advisory_xact_lock(62410801)"))
        existente=ListaEspera.query.filter_by(cooperado_id=coop.id).first()
        if existente:
            # Responder imediatamente; não emitir atualização global para entrada duplicada.
            return jsonify(ok=True,ja_estava=True,posicao=existente.pos,
                           mensagem='Você já está na fila.')
        max_pos=db.session.query(db.func.max(ListaEspera.pos)).scalar() or 0
        row=ListaEspera(cooperado_id=coop.id,nome=coop.nome,pos=max_pos+1,created_at=datetime.utcnow())
        try:
            db.session.add(row);db.session.commit()
            emitir_lista_espera()
            # Notificar apenas ENTRADA efetiva; reentradas já existentes não geram bip.
            try:
                from app import socketio
                socketio.emit('coopex_fila_entrada_alerta', {
                    'cooperado_id': coop.id,
                    'nome': coop.nome,
                    'quando': datetime.utcnow().isoformat() + 'Z'
                }, to='coopex-admin-led')
            except Exception:
                app.logger.exception('Entrada salva, mas aviso LED nao emitido')
        except Exception:
            db.session.rollback()
            app.logger.exception('Falha ao entrar na fila')
            return jsonify(ok=False,erro='Não foi possível entrar na fila.'),500
        # commit() já confirmou a gravação; não executar outro SELECT para
        # consultar o próprio registro. A posição vem do registro persistido.
        response=jsonify(ok=True,entrou=True,posicao=max_pos+1)
        response.headers['Cache-Control']='no-store, private'
        return response

    @app.post('/api/cooperado/fila/sair')
    def coopex_fila_geo_sair():
        coop=authorized()
        if not coop:return jsonify(ok=False,erro='Não autorizado'),403
        data=request.get_json(silent=True) or {}
        motivo=str(data.get('motivo') or '').strip()
        if len(motivo)<5 or len(motivo)>500:
            return jsonify(ok=False,erro='Informe o motivo da saída (5 a 500 caracteres).'),400
        try:
            ensure_schema()
            # Mesmo bloqueio da entrada para manter ordem e posições sem colisões.
            db.session.execute(text("SELECT pg_advisory_xact_lock(62410801)"))
            row=ListaEspera.query.filter_by(cooperado_id=coop.id).first()
            if not row:
                db.session.rollback()
                return jsonify(ok=False,erro='Você não está na fila.'),409
            posicao_saida=row.pos
            db.session.execute(text(
                'INSERT INTO fila_saida_auditoria (cooperado_id,cooperado_nome,motivo,ocorrido_em) '
                'VALUES (:cid,:nome,:motivo,CURRENT_TIMESTAMP)'
            ),{'cid':coop.id,'nome':coop.nome,'motivo':motivo})
            db.session.delete(row)
            # Reposicionar apenas quem estava depois da posição removida.
            # Um único UPDATE SQL substitui a leitura e o loop de toda a fila.
            db.session.flush()
            ListaEspera.query.filter(ListaEspera.pos > posicao_saida).update(
                {ListaEspera.pos: ListaEspera.pos - 1},
                synchronize_session=False,
            )
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

    # Recado persistente de passagem de turno. Independente da fila, saídas e Pix.
    def ensure_recado_schema():
        db.session.execute(text("""
            CREATE TABLE IF NOT EXISTS supervisao_recado_turno (
                chave VARCHAR(32) PRIMARY KEY,
                mensagem TEXT NOT NULL,
                autor VARCHAR(150) NOT NULL,
                criado_em TIMESTAMP NOT NULL,
                lido_por VARCHAR(150),
                lido_em TIMESTAMP
            )
        """))
        db.session.commit()

    def admin_recado():
        return bool(session.get('is_admin'))

    def evento_recado():
        try:
            from app import socketio
            socketio.emit('coopex_supervisao_recado_atualizado', {'atualizado': True},
                          to='coopex-admin-led')
        except Exception:
            app.logger.exception('Recado salvo; aviso de atualização não emitido')

    @app.get('/api/admin/supervisao/recado')
    def supervisao_recado_ler():
        if not admin_recado(): return jsonify(ok=False,erro='Não autorizado'),403
        ensure_recado_schema()
        row=db.session.execute(text(
            "SELECT mensagem,autor,criado_em,lido_por,lido_em "
            "FROM supervisao_recado_turno WHERE chave='turno' "
        )).mappings().first()
        return jsonify(ok=True,recado=({
            'mensagem':row['mensagem'],'autor':row['autor'],
            'criado_em':row['criado_em'].isoformat()+'Z' if row['criado_em'] else '',
            'lido_por':row['lido_por'],
            'lido_em':row['lido_em'].isoformat()+'Z' if row['lido_em'] else ''
        } if row else None))

    @app.post('/api/admin/supervisao/recado')
    def supervisao_recado_salvar():
        if not admin_recado(): return jsonify(ok=False,erro='Não autorizado'),403
        data=request.get_json(silent=True) or {}
        mensagem=str(data.get('mensagem') or '').strip()
        if not mensagem or len(mensagem)>2000:
            return jsonify(ok=False,erro='Digite um recado de até 2.000 caracteres.'),400
        ensure_recado_schema()
        autor=str(session.get('user_nome') or 'Supervisão')[:150]
        agora=datetime.utcnow()
        try:
            updated=db.session.execute(text(
                "UPDATE supervisao_recado_turno SET mensagem=:msg,autor=:autor,criado_em=:agora,"
                "lido_por=NULL,lido_em=NULL WHERE chave='turno'"
            ),{'msg':mensagem,'autor':autor,'agora':agora})
            if not updated.rowcount:
                db.session.execute(text(
                    "INSERT INTO supervisao_recado_turno "
                    "(chave,mensagem,autor,criado_em) VALUES ('turno',:msg,:autor,:agora)"
                ),{'msg':mensagem,'autor':autor,'agora':agora})
            db.session.commit()
        except Exception:
            db.session.rollback()
            app.logger.exception('Falha ao gravar recado de supervisão')
            return jsonify(ok=False,erro='Não foi possível salvar o recado.'),500
        evento_recado()
        return jsonify(ok=True)

    @app.post('/api/admin/supervisao/recado/confirmar')
    def supervisao_recado_confirmar():
        if not admin_recado(): return jsonify(ok=False,erro='Não autorizado'),403
        ensure_recado_schema()
        leitor=str(session.get('user_nome') or 'Supervisão')[:150]
        try:
            db.session.execute(text(
                "UPDATE supervisao_recado_turno SET lido_por=:leitor,lido_em=:agora "
                "WHERE chave='turno' AND lido_em IS NULL"
            ),{'leitor':leitor,'agora':datetime.utcnow()})
            db.session.commit()
        except Exception:
            db.session.rollback()
            return jsonify(ok=False,erro='Não foi possível confirmar leitura.'),500
        evento_recado()
        return jsonify(ok=True)

    @app.delete('/api/admin/supervisao/recado')
    def supervisao_recado_excluir():
        if not admin_recado(): return jsonify(ok=False,erro='Não autorizado'),403
        ensure_recado_schema()
        try:
            db.session.execute(text("DELETE FROM supervisao_recado_turno WHERE chave='turno'"))
            db.session.commit()
        except Exception:
            db.session.rollback()
            return jsonify(ok=False,erro='Não foi possível excluir o recado.'),500
        evento_recado()
        return jsonify(ok=True)
