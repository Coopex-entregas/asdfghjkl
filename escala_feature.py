import json,re,time,unicodedata
from collections import Counter,defaultdict
from datetime import date,datetime,time as dtime,timedelta
from zoneinfo import ZoneInfo
import pandas as pd
from flask import flash,jsonify,redirect,render_template,request,session,url_for
from sqlalchemy import func,or_

DONE=False; DB=None; MOD=None; Alias=None; Importacao=None; Item=None; Ajuste=None
CACHE={'until':0,'data':None}; STOP={'de','da','do','das','dos','e'}
NICKS=({'francisco','chico'},{'alberto','beto'})
COMMON={'carlos','jose','joao','francisco','antonio','alexandre','marcos','lucas','ricardo','gabriel','anderson','paulo','rafael','raphael','silva','santos','souza','costa','lima','alves','gomes','ferreira','pereira','rocha','nunes','dias','barbosa','carvalho','oliveira'}

def now(): return datetime.now(ZoneInfo('America/Fortaleza')).replace(tzinfo=None)
def norm(v):
 s=unicodedata.normalize('NFD',str(v or ''));s=''.join(c for c in s if unicodedata.category(c)!='Mn');return re.sub(r'\s+',' ',re.sub(r'[^a-zA-Z0-9]+',' ',s).lower()).strip()
def toks(v): return [x for x in norm(v).split() if x not in STOP and len(x)>1]
def equiv(x):
 for g in NICKS:
  if x in g:return g
 return {x}
def clear_cache(): CACHE.update(until=0,data=None)
def admin(): return bool(session.get('is_admin'))

def define_models(m):
 global DB,Alias,Importacao,Item,Ajuste
 DB=m.db;db=DB
 class A(db.Model):
  __tablename__='cooperado_alias';id=db.Column(db.Integer,primary_key=True);cooperado_id=db.Column(db.Integer,db.ForeignKey('cooperado.id'),nullable=False,index=True);alias_texto=db.Column(db.String(160),nullable=False);alias_norm=db.Column(db.String(160),nullable=False,unique=True,index=True);criado_em=db.Column(db.DateTime,default=datetime.utcnow,nullable=False);cooperado=db.relationship('Cooperado')
 class I(db.Model):
  __tablename__='escala_importacao';id=db.Column(db.Integer,primary_key=True,default=1);arquivo_nome=db.Column(db.String(255));inicio_semana=db.Column(db.Date,index=True);fim_semana=db.Column(db.Date,index=True);importado_em=db.Column(db.DateTime,default=datetime.utcnow,nullable=False);total_linhas=db.Column(db.Integer,default=0,nullable=False);total_vinculadas=db.Column(db.Integer,default=0,nullable=False);total_pendentes=db.Column(db.Integer,default=0,nullable=False);linhas_ignoradas=db.Column(db.Integer,default=0,nullable=False)
 class E(db.Model):
  __tablename__='escala_item';id=db.Column(db.Integer,primary_key=True);data=db.Column(db.Date,nullable=False,index=True);qtd=db.Column(db.String(30));turno=db.Column(db.String(30),index=True);horario_texto=db.Column(db.String(120));intervalos_json=db.Column(db.Text,default='[]',nullable=False);horario_estimado=db.Column(db.Boolean,default=False,nullable=False);contrato=db.Column(db.String(160),nullable=False,index=True);nome_planilha=db.Column(db.String(160),nullable=False);nome_norm=db.Column(db.String(160),nullable=False,index=True);cooperado_id=db.Column(db.Integer,db.ForeignKey('cooperado.id'),index=True);status_match=db.Column(db.String(30),default='nao_encontrado',nullable=False,index=True);candidatos_json=db.Column(db.Text,default='[]',nullable=False);detalhe_match=db.Column(db.String(255));importado_em=db.Column(db.DateTime,default=datetime.utcnow,nullable=False,index=True);cooperado=db.relationship('Cooperado')
  def intervalos(self):
   try:return json.loads(self.intervalos_json or '[]')
   except:return []
  def candidatos(self):
   try:return json.loads(self.candidatos_json or '[]')
   except:return []
 class J(db.Model):
  __tablename__='escala_ajuste';id=db.Column(db.Integer,primary_key=True);item_id=db.Column(db.Integer,db.ForeignKey('escala_item.id'),nullable=False,unique=True,index=True);cooperado_original_id=db.Column(db.Integer,db.ForeignKey('cooperado.id'));cooperado_novo_id=db.Column(db.Integer,db.ForeignKey('cooperado.id'),nullable=False,index=True);motivo=db.Column(db.String(255),nullable=False);alterado_em=db.Column(db.DateTime,default=datetime.utcnow,nullable=False);item=db.relationship(E);original=db.relationship('Cooperado',foreign_keys=[cooperado_original_id]);novo=db.relationship('Cooperado',foreign_keys=[cooperado_novo_id])
 Alias=A;Importacao=I;Item=E;Ajuste=J

def resolver_ctx():
 C=MOD.Cooperado
 try:cs=C.query.filter(C.ativo.is_(True)).order_by(C.nome).all()
 except:cs=C.query.order_by(C.nome).all()
 als=Alias.query.all();amap={a.alias_norm:int(a.cooperado_id) for a in als};freq=Counter();rows=[]
 for c in cs:
  tt=toks(c.nome);freq.update(set(tt));rows.append({'id':int(c.id),'nome':c.nome,'norm':norm(c.nome),'t':set(tt)})
 return rows,amap,freq

def match_name(name,ctx):
 rows,amap,freq=ctx;n=norm(name)
 if n in amap:
  c=next((x for x in rows if x['id']==amap[n]),None)
  if c:return c['id'],'alias',[c],'Associação salva.'
 ex=[x for x in rows if x['norm']==n]
 if len(ex)==1:return ex[0]['id'],'exato',ex,'Nome completo idêntico.'
 origin=toks(name);cand=[]
 for c in rows:
  score=0;why=[]
  for i,x in enumerate(origin):
   if x in c['t']:
    p=210+(210 if freq[x]<=1 else 90 if freq[x]==2 else 20)+(70 if i==len(origin)-1 else 0)-(100 if x in COMMON else 0);score+=max(40,p);why.append(x)
   elif any(y in c['t'] for y in equiv(x)-{x}):score+=420 if i==len(origin)-1 else 350;why.append(x+'=apelido')
  if score>=220:cand.append({'id':c['id'],'nome':c['nome'],'score':score,'motivo':', '.join(why)})
 cand.sort(key=lambda x:(-x['score'],norm(x['nome'])));cand=cand[:8]
 if not cand:return None,'nao_encontrado',[],'Nenhum cadastro compatível.'
 margin=cand[0]['score']-(cand[1]['score'] if len(cand)>1 else 0)
 if cand[0]['score']>=400 and (len(cand)==1 or margin>=110):return cand[0]['id'],'automatico',cand,cand[0]['motivo']
 return None,'ambiguo',cand,'Confira o cooperado correto.'

def parse_date(v):
 if isinstance(v,datetime):return v.date()
 if isinstance(v,date):return v
 m=re.search(r'(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})',str(v or ''))
 if not m:return None
 d,mo,y=map(int,m.groups());y=y+2000 if y<100 else y
 try:return date(y,mo,d)
 except:return None

def parse_hours(v,turn=''):
 s=unicodedata.normalize('NFD',str(v or '').lower());s=''.join(c for c in s if unicodedata.category(c)!='Mn').replace('–','-').replace('—','-');pairs=re.findall(r'(\d{1,2})[:h](\d{2})\s*(?:as|a|ate|-)\s*(\d{1,2})[:h](\d{2})',s);out=[]
 for a,b,c,d in pairs:
  ini=f'{int(a):02d}:{int(b):02d}';fim=f'{int(c):02d}:{int(d):02d}';out.append({'inicio':ini,'fim':fim,'dia_seguinte':fim<=ini})
 if out:return out,False
 z=norm(str(v or '')+' '+str(turn or ''))
 if 'meio' in z:return [{'inicio':'08:00','fim':'12:00','dia_seguinte':False}],True
 if 'comercial' in z:return [{'inicio':'08:00','fim':'18:00','dia_seguinte':False}],True
 if 'noite' in z or 'noturno' in z:return [{'inicio':'17:00','fim':'23:59','dia_seguinte':False}],True
 return [{'inicio':'07:00','fim':'17:00','dia_seguinte':False}],True

def columns(df):
 mp={norm(c):c for c in df.columns};names={'data':['data'],'qtd':['qtd','quantidade'],'turno':['turno'],'horarios':['horarios','horario','horas'],'contrato':['contrato','grupo'],'nome':['nome do cooperado','cooperado','nome']};out={k:next((mp[x] for x in vv if x in mp),None) for k,vv in names.items()};miss=[x for x in ('data','horarios','contrato','nome') if not out[x]]
 if miss:raise ValueError('Colunas obrigatórias ausentes: '+', '.join(miss))
 return out

def recount(meta):
 meta.total_linhas=Item.query.count();meta.total_vinculadas=Item.query.filter(Item.cooperado_id.isnot(None)).count();meta.total_pendentes=meta.total_linhas-meta.total_vinculadas

def label(item):return ' / '.join(f"{x.get('inicio')}–{x.get('fim')}"+(' (+1)' if x.get('dia_seguinte') else '') for x in item.intervalos()) or item.horario_texto or '—'
def active(item,dt):
 for x in item.intervalos():
  try:
   a,b=map(int,x['inicio'].split(':'));c,d=map(int,x['fim'].split(':'));ini=datetime.combine(item.data,dtime(a,b));fim=datetime.combine(item.data+timedelta(days=1 if x.get('dia_seguinte') else 0),dtime(c,d))
   if ini<=dt<fim:return True
  except:pass
 return False

def context_now():
 if CACHE['data'] and time.monotonic()<CACHE['until']:return CACHE['data']
 dt=now();meta=Importacao.query.filter_by(id=1).first();restricted=bool(meta and meta.inicio_semana<=dt.date()<=meta.fim_semana);items=[]
 if restricted:items=[x for x in Item.query.filter(Item.data.in_([dt.date(),dt.date()-timedelta(days=1)]),Item.cooperado_id.isnot(None)).all() if active(x,dt)]
 ids={int(x.cooperado_id) for x in items};rr=defaultdict(list)
 for x in items:rr[int(x.cooperado_id)].append(f'{x.contrato} · {label(x)}')
 data={'agora':dt,'restricao_ativa':restricted,'ids':ids,'itens':items,'resumo_por_id':{k:' | '.join(dict.fromkeys(v)) for k,v in rr.items()}};CACHE.update(until=time.monotonic()+20,data=data);return data

def routes(app):
 def page():
  if not admin():return redirect(url_for('login'))
  meta=Importacao.query.filter_by(id=1).first();today=now().date();default=meta.inicio_semana if meta and not(meta.inicio_semana<=today<=meta.fim_semana) else today;ds=request.args.get('data') or default.isoformat();contract=(request.args.get('contrato') or '').strip();cid=request.args.get('cooperado_id',type=int);status=request.args.get('status') or 'todos';q=(request.args.get('q') or '').strip();query=Item.query
  try:d=date.fromisoformat(ds);query=query.filter(Item.data==d)
  except:d=default
  if contract:query=query.filter(Item.contrato==contract)
  if cid:query=query.filter(Item.cooperado_id==cid)
  if status=='pendentes':query=query.filter(Item.cooperado_id.is_(None))
  if status=='vinculados':query=query.filter(Item.cooperado_id.isnot(None))
  if q:query=query.filter(or_(Item.nome_norm.like('%'+norm(q)+'%'),func.lower(Item.contrato).like('%'+q.lower()+'%')))
  items=query.order_by(Item.contrato,Item.horario_texto,Item.nome_planilha).all();ctx=context_now();active_ids={x.id for x in ctx['itens']};ajustes={x.item_id:x for x in Ajuste.query.filter(Ajuste.item_id.in_([i.id for i in items] or [-1])).all()}
  for x in items:x.ativo_agora=x.id in active_ids;x.intervalos_rotulo=label(x);x.candidatos_lista=x.candidatos();x.ajuste=ajustes.get(x.id)
  contracts=[x[0] for x in DB.session.query(Item.contrato).distinct().order_by(Item.contrato).all()];C=MOD.Cooperado
  try:coops=C.query.filter(C.ativo.is_(True)).order_by(C.nome).all()
  except:coops=C.query.order_by(C.nome).all()
  return render_template('escala.html',meta=meta,itens=items,contratos=contracts,cooperados=coops,data_escolhida=d,contrato_filtro=contract,cooperado_filtro=cid,status_filtro=status,busca=q,vinculados_total=meta.total_vinculadas if meta else 0,pendentes_total=meta.total_pendentes if meta else 0,to_brasilia=getattr(MOD,'to_brasilia',lambda x:x))
 def upload():
  if not admin():return redirect(url_for('login'))
  f=request.files.get('arquivo')
  if not f or not f.filename or not f.filename.lower().endswith('.xlsx'):flash('Selecione uma planilha XLSX.','error');return redirect(url_for('escala'))
  try:
   f.stream.seek(0,2);size=f.stream.tell();f.stream.seek(0)
   if size>12*1024*1024:raise ValueError('A planilha ultrapassa 12 MB.')
   df=pd.read_excel(f,sheet_name=0,dtype=object);col=columns(df);ctx=resolver_ctx();new=[];ignored=0;dates=[];stamp=datetime.utcnow()
   for _,r in df.iterrows():
    con=str(r.get(col['contrato'],'') or '').strip();name=str(r.get(col['nome'],'') or '').strip();dd=parse_date(r.get(col['data']))
    if not con or not name or not dd or norm(con)=='folga':ignored+=1;continue
    ht=str(r.get(col['horarios'],'') or '').strip();turn=str(r.get(col['turno'],'') or '').strip() if col.get('turno') else '';qtd=str(r.get(col['qtd'],'') or '').strip() if col.get('qtd') else '';ints,est=parse_hours(ht,turn);cid,st,cands,detail=match_name(name,ctx);new.append(Item(data=dd,qtd=qtd[:30],turno=turn[:30],horario_texto=ht[:120],intervalos_json=json.dumps(ints,ensure_ascii=False),horario_estimado=est,contrato=con[:160],nome_planilha=name[:160],nome_norm=norm(name)[:160],cooperado_id=cid,status_match=st,candidatos_json=json.dumps(cands,ensure_ascii=False),detalhe_match=detail[:255],importado_em=stamp));dates.append(dd)
   if not new:raise ValueError('Nenhuma linha válida foi encontrada.')
   Ajuste.query.delete(synchronize_session=False);Item.query.delete(synchronize_session=False);meta=Importacao.query.filter_by(id=1).first() or Importacao(id=1);DB.session.add(meta);DB.session.add_all(new);DB.session.flush();meta.arquivo_nome=f.filename[:255];meta.inicio_semana=min(dates);meta.fim_semana=max(dates);meta.importado_em=stamp;meta.linhas_ignoradas=ignored;recount(meta);DB.session.commit();clear_cache();flash(f'Escala substituída: {meta.total_linhas} linhas válidas e {ignored} ignoradas.','success')
  except Exception as e:DB.session.rollback();flash(f'Não foi possível importar a escala: {e}','error')
  return redirect(url_for('escala'))
 def link(i):
  if not admin():return redirect(url_for('login'))
  x=Item.query.get_or_404(i);cid=request.form.get('cooperado_id',type=int);c=MOD.Cooperado.query.get(cid) if cid else None
  if not c:flash('Selecione um cooperado válido.','error');return redirect(request.referrer or url_for('escala'))
  n=norm(x.nome_planilha);a=Alias.query.filter_by(alias_norm=n).first()
  if a:a.cooperado_id=c.id;a.alias_texto=x.nome_planilha
  else:DB.session.add(Alias(cooperado_id=c.id,alias_texto=x.nome_planilha,alias_norm=n))
  for y in Item.query.filter_by(nome_norm=x.nome_norm).all():y.cooperado_id=c.id;y.status_match='confirmado';y.detalhe_match='Associação confirmada pelo administrador.'
  meta=Importacao.query.filter_by(id=1).first();recount(meta) if meta else None;DB.session.commit();clear_cache();flash(f'{x.nome_planilha} foi associado a {c.nome}.','success');return redirect(request.referrer or url_for('escala'))
 def substitute(i):
  if not admin():return redirect(url_for('login'))
  x=Item.query.get_or_404(i);cid=request.form.get('cooperado_id',type=int);motivo=(request.form.get('motivo') or '').strip();c=MOD.Cooperado.query.get(cid) if cid else None
  if not c:flash('Selecione o cooperado que fará a substituição.','error');return redirect(request.referrer or url_for('escala'))
  if len(motivo)<3:flash('Informe o motivo da mudança.','error');return redirect(request.referrer or url_for('escala'))
  aj=Ajuste.query.filter_by(item_id=x.id).first()
  if not aj:aj=Ajuste(item_id=x.id,cooperado_original_id=x.cooperado_id,cooperado_novo_id=c.id,motivo=motivo[:255]);DB.session.add(aj)
  else:aj.cooperado_novo_id=c.id;aj.motivo=motivo[:255];aj.alterado_em=datetime.utcnow()
  x.cooperado_id=c.id;x.status_match='substituido';x.detalhe_match=('Substituição: '+motivo)[:255];DB.session.commit();clear_cache();flash(f'Escala alterada para {c.nome}.','success');return redirect(request.referrer or url_for('escala'))
 def restore(i):
  if not admin():return redirect(url_for('login'))
  x=Item.query.get_or_404(i);aj=Ajuste.query.filter_by(item_id=x.id).first()
  if not aj:flash('Esta linha não possui substituição.','warning');return redirect(request.referrer or url_for('escala'))
  x.cooperado_id=aj.cooperado_original_id;x.status_match='confirmado' if x.cooperado_id else 'nao_encontrado';x.detalhe_match='Substituição desfeita; escala importada restaurada.';DB.session.delete(aj);DB.session.commit();clear_cache();flash('Escala original restaurada.','success');return redirect(request.referrer or url_for('escala'))
 def clear():
  if not admin():return redirect(url_for('login'))
  Ajuste.query.delete(synchronize_session=False);Item.query.delete(synchronize_session=False);Importacao.query.delete(synchronize_session=False);DB.session.commit();clear_cache();flash('Escala atual removida. As associações de nomes foram mantidas.','success');return redirect(url_for('escala'))
 def api():
  if not admin():return jsonify(ok=False,error='Não autorizado'),401
  c=context_now();items=[{'cooperado_id':int(x.cooperado_id),'nome':x.cooperado.nome if x.cooperado else x.nome_planilha,'contrato':x.contrato,'horario':label(x)} for x in c['itens']];return jsonify(ok=True,restricao_ativa=c['restricao_ativa'],agora=c['agora'].isoformat(),cooperados_ids=sorted(c['ids']),itens=items)
 def scheduled_api():
  if not admin():return jsonify(ok=False,error='Não autorizado'),401
  E=MOD.Entrega;utc_now=datetime.utcnow();rows=E.query.filter(E.data_envio.isnot(None)).filter(or_(func.lower(func.coalesce(E.status,''))=='agendado',E.data_envio>utc_now)).order_by(E.data_envio.asc()).limit(500).all();conv=getattr(MOD,'to_brasilia',lambda x:x);out=[]
  for e in rows:
   ag=e.data_envio;at=e.data_atribuida;ag_local=conv(ag);at_local=conv(at) if at else None;depois=bool(at and ag and at>ag);mostra=at_local if depois else ag_local
   out.append({'id':int(e.id),'agendada_data':ag_local.strftime('%d/%m/%Y'),'agendada_hora':ag_local.strftime('%H:%M'),'atribuicao_hora':mostra.strftime('%H:%M') if mostra else '-','atribuida_depois':depois,'tem_cooperado':bool(e.cooperado_id)})
  return jsonify(ok=True,itens=out)
 app.add_url_rule('/escala','escala',page);app.add_url_rule('/escala/importar','escala_importar',upload,methods=['POST']);app.add_url_rule('/escala/vincular/<int:i>','escala_vincular',link,methods=['POST']);app.add_url_rule('/escala/substituir/<int:i>','escala_substituir',substitute,methods=['POST']);app.add_url_rule('/escala/restaurar/<int:i>','escala_restaurar',restore,methods=['POST']);app.add_url_rule('/escala/limpar','escala_limpar',clear,methods=['POST']);app.add_url_rule('/api/escala/agora','api_escala_agora',api);app.add_url_rule('/api/entregas/agendadas-exibicao','api_entregas_agendadas_exibicao',scheduled_api)

def wrap_suggestions():
 old=getattr(MOD,'calcular_sugestoes_cooperados',None)
 if not callable(old) or getattr(old,'_scale',False):return
 def wrapped(entrega,limite=5):
  c=context_now();r=old(entrega,max(int(limite or 5),50) if c['restricao_ativa'] else int(limite or 5))
  if c['restricao_ativa']:r=[x for x in r if int(x.get('id') or x.get('cooperado_id') or 0) in c['ids']]
  return r[:int(limite or 5)]
 wrapped._scale=True;MOD.calcular_sugestoes_cooperados=wrapped

def install(m):
 global DONE,MOD
 if DONE:return
 MOD=m;define_models(m)
 with m.app.app_context():DB.create_all()
 routes(m.app);wrap_suggestions();DONE=True
