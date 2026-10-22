"""Gestao inicial de supervisores — acesso exclusivo do administrador master."""
from flask import abort, request, redirect, url_for, flash, session, render_template_string
from sqlalchemy import text
from werkzeug.security import generate_password_hash
from datetime import datetime
import secrets

def install(host):
    app, db = host.app, host.db
    with app.app_context():
        db.session.execute(text("""CREATE TABLE IF NOT EXISTS coopex_supervisores (
          id INTEGER PRIMARY KEY, nome VARCHAR(150) NOT NULL,
          usuario VARCHAR(80) NOT NULL UNIQUE, senha_hash VARCHAR(255) NOT NULL,
          ativo BOOLEAN NOT NULL DEFAULT TRUE,
          hora_normal NUMERIC(10,2) NOT NULL DEFAULT 0,
          hora_especial NUMERIC(10,2) NOT NULL DEFAULT 0,
          criado_em VARCHAR(32) NOT NULL)"""))
        db.session.commit()

    def master():
        if not session.get('is_admin'):
            return redirect(url_for('login'))
        if not session.get('is_master'):
            abort(403)

    @app.route('/gestao/supervisores', methods=['GET','POST'])
    def gestao_supervisores():
        guard = master()
        if guard is not None: return guard
        if request.method == 'POST':
            token = request.form.get('csrf','')
            if not secrets.compare_digest(token, session.get('gestao_csrf','')):
                abort(403)
            action = request.form.get('action','create')
            if action == 'create':
                nome = (request.form.get('nome') or '').strip()
                usuario = (request.form.get('usuario') or '').strip().lower()
                senha = request.form.get('senha') or ''
                if not (2 <= len(nome) <= 150 and 3 <= len(usuario) <= 80 and usuario.replace('_','').isalnum() and len(senha) >= 10):
                    flash('Informe nome, usuário alfanumérico e senha com pelo menos 10 caracteres.')
                else:
                    try:
                        db.session.execute(text("""INSERT INTO coopex_supervisores
                        (nome,usuario,senha_hash,ativo,criado_em) VALUES
                        (:nome,:usuario,:senha,TRUE,:criado)"""),dict(
                        nome=nome,usuario=usuario,senha=generate_password_hash(senha),
                        criado=datetime.utcnow().isoformat()))
                        db.session.commit()
                        flash('Supervisor cadastrado.')
                    except Exception:
                        db.session.rollback()
                        flash('Não foi possível cadastrar. Confira se o usuário já existe.')
            elif action == 'password':
                try:
                    ident = int(request.form.get('id', '0'))
                    nova = request.form.get('nova_senha') or ''
                    if not 10 <= len(nova) <= 128:
                        flash('A nova senha deve ter entre 10 e 128 caracteres.')
                    else:
                        db.session.execute(text('UPDATE coopex_supervisores SET senha_hash=:h WHERE id=:id'),
                                           {'h': generate_password_hash(nova), 'id': ident})
                        db.session.commit()
                        flash('Senha atualizada.')
                except Exception:
                    db.session.rollback()
                    flash('Falha ao atualizar senha.')
            elif action in ('activate','deactivate'):
                try:
                    ident = int(request.form.get('id','0'))
                    db.session.execute(text('UPDATE coopex_supervisores SET ativo=:ativo WHERE id=:id'),
                                       dict(ativo=action=='activate',id=ident))
                    db.session.commit()
                except Exception:
                    db.session.rollback()
                    flash('Falha ao atualizar cadastro.')
            return redirect(url_for('gestao_supervisores'))
        if not session.get('gestao_csrf'):
            session['gestao_csrf'] = secrets.token_urlsafe(32)
        rows = db.session.execute(text('SELECT id,nome,usuario,ativo,criado_em FROM coopex_supervisores ORDER BY nome')).mappings().all()
        return render_template_string(PAGE,rows=rows,csrf=session['gestao_csrf'])

PAGE = """
<!doctype html><html lang="pt-br"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Gestão de Supervisores · COOPEX</title>
<style>
:root{color-scheme:dark;font-family:Inter,system-ui,Arial,sans-serif}
*{box-sizing:border-box}body{margin:0;background:#031021;color:#edf6ff}
header{background:#06172c;border-bottom:2px solid #087aff;padding:18px 25px;display:flex;align-items:center;justify-content:space-between;gap:15px}
a{color:#65bbff;text-decoration:none}.brand{font-weight:800;font-size:20px}
main{max-width:1250px;margin:30px auto;padding:0 20px}.muted{color:#a3b9d6}
h1{font-size:27px;margin-bottom:4px}h2{font-size:20px;margin-top:0}
nav{display:flex;flex-wrap:wrap;gap:10px;margin:20px 0}
nav a{padding:11px 16px;background:#0c294a;border:1px solid #25517f;border-radius:12px}
nav a.active{background:#0658d4;color:white}.card{background:#091d35;border:1px solid #28466b;border-radius:17px;padding:24px;margin:20px 0}
.fields{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}
label{display:block;color:#aec7e5;font-size:13px;margin-bottom:7px}
input{width:100%;border:1px solid #35557f;border-radius:10px;background:#05152a;color:white;padding:12px;font-size:15px}
button{background:#0762e2;border:0;color:white;border-radius:10px;padding:11px 18px;font-weight:700;cursor:pointer}
button.secondary{background:#1c3859}.actions{display:flex;gap:8px}
table{width:100%;border-collapse:collapse}th{text-align:left;background:#103461;color:#fff}td,th{padding:13px 11px;border-bottom:1px solid #244363}
.wrap{overflow-x:auto}.notice{background:#102e52;border-left:3px solid #06a8ff;padding:14px;border-radius:8px}
@media(max-width:760px){.fields{grid-template-columns:1fr}header{flex-wrap:wrap}}
</style></head><body>
<header><div class="brand">COOPEX | Gestão Operacional</div><a href="{{ url_for('admin') }}">← Voltar às Entregas</a></header>
<main>
<h1>Gestão de Supervisores</h1><p class="muted">Cadastros individuais e controle de acesso à equipe.</p>
<nav><a class="active" href="{{ url_for('gestao_supervisores') }}">Supervisores</a><a href="{{ url_for('admin') }}">Dashboard operacional</a></nav>
<div class="notice">Primeira etapa: cadastro seguro de supervisores. Jornada, permissões, auditoria, produtividade e bonificações serão integradas nas próximas etapas; não estão ativadas nesta página.</div>
{% with messages=get_flashed_messages() %}{% for m in messages %}<p class="notice">{{ m }}</p>{% endfor %}{% endwith %}
<section class="card"><h2>Cadastrar supervisor</h2><form method="post"><input type="hidden" name="csrf" value="{{csrf}}">
<div class="fields"><div><label>Nome completo</label><input name="nome" maxlength="150" required></div>
<div><label>Usuário</label><input name="usuario" maxlength="80" pattern="[A-Za-z0-9_]{3,80}" required></div>
<div><label>Senha individual (mínimo 10 caracteres)</label><input name="senha" type="password" minlength="10" required autocomplete="new-password"></div></div>
<p><button type="submit">Cadastrar supervisor</button></p></form></section>
<section class="card"><h2>Supervisores cadastrados ({{ rows|length }})</h2><div class="wrap"><table>
<thead><tr><th>Nome</th><th>Usuário</th><th>Situação</th><th>Ações</th></tr></thead><tbody>
{% for r in rows %}<tr><td>{{ r.nome }}</td><td>{{ r.usuario }}</td><td>{{ 'Ativo' if r.ativo else 'Inativo' }}</td>
<td><form method="post"><input type="hidden" name="csrf" value="{{csrf}}"><input type="hidden" name="id" value="{{r.id}}">
<input type="hidden" name="action" value="{{ 'deactivate' if r.ativo else 'activate' }}">
<button class="secondary" type="submit">{{ 'Desativar' if r.ativo else 'Ativar' }}</button></form>
<details><summary style="cursor:pointer;color:#6bf">Alterar senha</summary>
<form method="post"><input type="hidden" name="csrf" value="{{csrf}}">
<input type="hidden" name="action" value="password"><input type="hidden" name="id" value="{{r.id}}">
<label>Nova senha individual</label><input name="nova_senha" type="password" minlength="10" maxlength="128" required autocomplete="new-password">
<button style="margin-top:8px" type="submit">Salvar senha</button></form></details></td></tr>
{% else %}<tr><td colspan="4" class="muted">Nenhum supervisor cadastrado.</td></tr>{% endfor %}
</tbody></table></div></section></main></body></html>
"""
