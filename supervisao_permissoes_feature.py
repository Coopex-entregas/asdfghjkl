"""Controle inicial de permissoes de supervisores, validado no servidor."""
from flask import request, session, abort, redirect, url_for, render_template_string, flash
from sqlalchemy import text
import secrets

MODULES = {
 'operacoes':'Operações', 'cadastros':'Cadastros', 'financeiro':'Financeiro',
 'relatorios':'Relatórios', 'gestao':'Gestão', 'configuracoes':'Configurações'
}
ACTIONS = {'visualizar':'Visualizar', 'incluir':'Incluir', 'editar':'Editar',
           'excluir':'Excluir', 'cancelar':'Cancelar', 'exportar':'Exportar'}
def install(host):
 app, db = host.app,host.db
 with app.app_context():
  db.session.execute(text("""CREATE TABLE IF NOT EXISTS coopex_supervisor_permissoes (
    supervisor_id INTEGER NOT NULL, modulo VARCHAR(40) NOT NULL, acao VARCHAR(30) NOT NULL,
    permitido BOOLEAN NOT NULL DEFAULT FALSE,
    PRIMARY KEY(supervisor_id,modulo,acao))"""))
  db.session.commit()
 def master_only():
  if not session.get('is_admin'):return redirect(url_for('login'))
  if not session.get('is_master'):abort(403)
 @app.route('/configuracoes/permissoes',methods=['GET','POST'])
 def coopex_permissoes():
  guard=master_only()
  if guard:return guard
  if not session.get('permissoes_csrf'):
   session['permissoes_csrf']=secrets.token_urlsafe(32)
  supervisors=db.session.execute(text('SELECT id,nome,usuario FROM coopex_supervisores ORDER BY nome')).mappings().all()
  sid=request.values.get('supervisor_id',type=int)
  valid={p['id'] for p in supervisors}
  if sid not in valid:sid=next(iter(valid),None)
  if request.method=='POST':
   if not secrets.compare_digest(request.form.get('csrf',''),session['permissoes_csrf']):abort(403)
   if sid is None:abort(400)
   try:
    for mod in MODULES:
     for action in ACTIONS:
      permitted=f'{mod}.{action}' in request.form.getlist('permission')
      db.session.execute(text("""INSERT INTO coopex_supervisor_permissoes(supervisor_id,modulo,acao,permitido)
       VALUES(:sid,:mod,:ac,:ok) ON CONFLICT(supervisor_id,modulo,acao)
       DO UPDATE SET permitido=:ok"""),dict(sid=sid,mod=mod,ac=action,ok=permitted))
    db.session.commit()
    flash('Permissões salvas para o supervisor.')
   except Exception:
    db.session.rollback();raise
   return redirect(url_for('coopex_permissoes',supervisor_id=sid))
  rows=db.session.execute(text('SELECT modulo,acao FROM coopex_supervisor_permissoes WHERE supervisor_id=:sid AND permitido=TRUE'),{'sid':sid or 0}).mappings().all()
  allowed={f"{x['modulo']}.{x['acao']}" for x in rows}
  return render_template_string(PAGE,supervisors=supervisors,sid=sid,modules=MODULES,actions=ACTIONS,allowed=allowed,csrf=session['permissoes_csrf'])
PAGE="""
<!doctype html><html lang="pt-br"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Permissões · COOPEX</title><style>
:root{color-scheme:dark;font-family:Inter,system-ui,Arial,sans-serif}*{box-sizing:border-box}
body{background:#031021;color:#edf6ff;margin:0}header{padding:18px 25px;background:#06172c;border-bottom:2px solid #078bff;display:flex;justify-content:space-between}
a{color:#6bc0ff;text-decoration:none}main{max-width:1180px;margin:24px auto;padding:0 16px}
h1{font-size:27px}p{color:#9eb5d5}.card{background:#091d35;border:1px solid #29486b;border-radius:16px;padding:22px;margin-bottom:18px}
select,button{padding:11px 15px;border-radius:10px;color:white}select{background:#06182e;border:1px solid #345e87;min-width:230px}
button{background:#065fda;border:1px solid #108cff;font-weight:750;cursor:pointer}
table{width:100%;border-collapse:collapse}th{background:#0c3977}th,td{padding:12px;text-align:center;border-bottom:1px solid #23456c}
th:first-child,td:first-child{text-align:left}tr:nth-child(even){background:#0e233e}input[type=checkbox]{width:18px;height:18px;accent-color:#087bfa}
.notice{padding:12px;background:#11294a;border-radius:10px}
</style></head><body><header><strong>COOPEX | Configurações · Permissões</strong><a href="{{ url_for('admin') }}">← Voltar ao painel</a></header>
<main><h1>Permissões por supervisor</h1><p>Defina quais ações cada supervisor poderá executar nos módulos.</p>
<div class="card"><form method="get"><label for="supervisor">Supervisor: </label>
<select name="supervisor_id" id="supervisor" onchange="this.form.submit()">
{% for s in supervisors %}<option value="{{s.id}}" {% if sid==s.id %}selected{% endif %}>{{s.nome}} ({{s.usuario}})</option>{% endfor %}
</select></form></div>
{% with messages=get_flashed_messages() %}{% for m in messages %}<p class="notice">{{m}}</p>{% endfor %}{% endwith %}
{% if sid %}
<form method="post" class="card"><input type="hidden" name="csrf" value="{{csrf}}">
<input type="hidden" name="supervisor_id" value="{{sid}}">
<div style="overflow:auto"><table><thead><tr><th>Módulo</th>{% for label in actions.values() %}<th>{{label}}</th>{% endfor %}</tr></thead><tbody>
{% for mod,label in modules.items() %}<tr><td><strong>{{label}}</strong></td>
{% for action in actions %}<td><input type="checkbox" name="permission" value="{{mod}}.{{action}}" {% if (mod ~ '.' ~ action) in allowed %}checked{% endif %}></td>{% endfor %}</tr>{% endfor %}
</tbody></table></div><p><button type="submit">Salvar permissões</button></p></form>
{% else %}<p class="notice">Cadastre primeiro um supervisor.</p>{% endif %}
<p>As permissões são registradas no banco. A aplicação das restrições a todas as rotas será liberada somente após mapear e testar cada endpoint operacional.</p>
</main></body></html>
"""