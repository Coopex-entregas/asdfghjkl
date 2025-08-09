<!DOCTYPE html>
<html lang="pt-br">
<head>
  <meta charset="UTF-8" />
  <title>Painel da Supervisão - Coopex</title>
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&display=swap" rel="stylesheet">
  <style>
    :root { --azul:#003399; --azul-escuro:#222b44; --azul-grad:linear-gradient(90deg,#003399 55%,#4f8cff 110%); --amarelo:#ffe066; --cinza-bg:#f6f8fa; --cinza-card:#fff; --shadow:0 6px 24px #0033991a; --shadow-sm:0 2px 10px #00339918; --radius:16px; }
    html,body{margin:0;background:var(--cinza-bg);color:var(--azul-escuro);font-family:Inter,system-ui,Arial,sans-serif}
    header{background:var(--azul-grad);color:#fff;padding:.9rem 2vw;position:sticky;top:0;z-index:99;box-shadow:var(--shadow)}
    .header-grid{display:grid;grid-template-columns:auto 1fr auto;gap:18px;align-items:center}
    h1{font-size:1.1rem;margin:0;font-weight:800;white-space:nowrap}
    .logo-topo{height:42px;width:42px;border-radius:14px;background:#fff;border:2px solid #ddeaff;object-fit:contain;margin-right:6px;vertical-align:middle}
    .kpis{display:flex;gap:10px;flex-wrap:wrap}
    .kpi{background:#ffffff1c;border:1px solid #ffffff55;border-radius:12px;padding:8px 14px;min-width:150px}
    .logout{color:#fff;background:#c1121f;border-radius:18px;padding:8px 18px;text-decoration:none;font-weight:700}

    .painel-bg{background:linear-gradient(115deg,#f4f8ff 60%,#e3f2ff 100%);border-radius:16px;box-shadow:var(--shadow);padding:16px 2vw;margin:12px 2vw}
    .grid-top{display:grid;grid-template-columns:1.2fr 1fr .8fr;gap:14px}
    .card{background:#fff;border:1px solid #e6ebff;border-radius:14px;padding:14px;box-shadow:var(--shadow-sm)}
    .card h3{margin:0 0 8px;color:#003399}

    /* Fila */
    .espera-bar{display:flex;gap:8px;align-items:center;margin-bottom:8px}
    .espera-select{flex:1;border:1.2px solid #a8b9e4;border-radius:8px;padding:7px 10px;background:#f7faff}
    .espera-add{background:#003399;color:#fff;border:none;border-radius:8px;padding:8px 14px;font-weight:700;cursor:pointer}
    #espera-lista{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:7px;max-height:200px;overflow:auto}
    .espera-chip{display:flex;align-items:center;gap:8px;background:#f7faff;border:1px solid #a8b9e4;padding:7px 10px;border-radius:10px}
    .espera-chip-num{background:#003399;color:#fff;border-radius:7px;padding:2px 7px;font-weight:800;min-width:26px;text-align:center}
    .espera-chip-text{font-weight:700;color:#003399}
    .espera-chip-remove{margin-left:auto;background:#d11a1a;color:#fff;border:none;border-radius:8px;padding:5px 8px;font-weight:700;cursor:pointer}
    .dragging{opacity:.6}

    .actions,.filtros{margin:10px 2vw;display:flex;gap:10px;flex-wrap:wrap}
    .btn{background:var(--azul-grad);color:#fff;border:none;padding:10px 18px;border-radius:12px;text-decoration:none;font-weight:700}

    .tabela{overflow-x:auto;background:#232942;border-radius:17px;box-shadow:var(--shadow);padding:12px;margin:12px 2vw}
    table{width:100%;border-collapse:collapse;background:#232942;color:#fff}
    th,td{border:1px solid #3a3a3a;padding:9px 4px;text-align:center}
    th{background:linear-gradient(90deg,#003399 60%,#4f8cff 120%);font-weight:700}

    @media(max-width:1100px){.grid-top{grid-template-columns:1fr}}
  </style>
</head>
<body>
<header>
  <div class="header-grid">
    <h1>
      <img src="{{ url_for('static', filename='logo_kratos.png') }}" class="logo-topo"
           onerror="this.onerror=null;this.src='https://via.placeholder.com/90x38?text=LOGO';">
      Kratos System – Supervisão
    </h1>
    <div class="kpis">
      <div class="kpi"><small>Entregas do Dia</small><span>{{ estatisticas.total_dia }}</span></div>
      <div class="kpi"><small>Do Mês</small><span>{{ estatisticas.total_mes }}</span></div>
      <div class="kpi"><small>Do Ano</small><span>{{ estatisticas.total_ano }}</span></div>
    </div>
    <a class="logout" href="{{ url_for('logout') }}">Sair</a>
  </div>
</header>

<div class="painel-bg">
  <div class="grid-top">
    <!-- Fila de Espera -->
    <div class="card">
      <h3>Lista de Espera</h3>
      <form class="espera-bar" action="{{ url_for('lista_espera_add') }}" method="POST">
        <select name="cooperado_id" class="espera-select" required>
          <option value="">Selecionar cooperado…</option>
          {% for c in cooperados_para_incluir %}
            <option value="{{ c.id }}">{{ c.nome }}</option>
          {% endfor %}
        </select>
        <button type="submit" class="espera-add">Adicionar</button>
      </form>

      <ul id="espera-lista">
        {% for item in lista_espera %}
          <li class="espera-chip" draggable="true" data-id="{{ item.id }}">
            <span class="espera-chip-num">{{ loop.index }}</span>
            <span class="espera-chip-text">{{ item.nome }}</span>
            <form action="{{ url_for('lista_espera_remove', id=item.id) }}" method="POST" style="margin-left:auto">
              <button class="espera-chip-remove" type="submit" title="Remover">×</button>
            </form>
          </li>
        {% else %}
          <li>Nenhum cooperado na fila.</li>
        {% endfor %}
      </ul>

      <form id="espera-salvar-ordem" action="{{ url_for('lista_espera_reordenar') }}" method="POST" style="display:none">
        <input type="hidden" name="ordem" id="espera-ordem">
      </form>
    </div>

    <!-- Cooperados -->
    <div class="card">
      <h3>Cooperados</h3>
      <ul style="list-style:none;margin:0;padding:0;max-height:200px;overflow:auto">
        {% for c in cooperados %}
          <li style="padding:4px 0">{{ c.nome }}</li>
        {% endfor %}
      </ul>
    </div>

    <!-- Hoje -->
    <div class="card">
      <h3>Hoje</h3>
      <div>{{ to_brasilia(now()).strftime('%d/%m/%Y') }} – {{ to_brasilia(now())|diasemana }}</div>
      <div style="margin-top:6px">
        {% if feriado_hoje %}
          <b style="color:#d11a1a">⚠️ {{ feriado_hoje }}</b>
        {% else %}
          <b style="color:#2767e6">Hoje não é feriado.</b>
        {% endif %}
      </div>
    </div>
  </div>
</div>

<div class="actions">
  <a href="{{ url_for('cadastrar_entrega') }}" class="btn">Nova Entrega</a>
  <a href="{{ url_for('agendar_entrega') }}" class="btn">Agendar Entrega</a>
  <a href="{{ url_for('cadastrar_cooperado') }}" class="btn">Novo Cooperado</a>
  <a href="{{ url_for('estatisticas_cooperado') }}" class="btn">Dashboard</a>
</div>

<div class="filtros">
  <form method="GET" action="{{ url_for('admin') }}" style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
    <label>Cooperado:</label>
    <select name="cooperado_id">
      <option value="todos">Todos</option>
      {% for c in cooperados %}
        <option value="{{ c.id }}" {% if request.args.get('cooperado_id') == c.id|string %}selected{% endif %}>{{ c.nome }}</option>
      {% endfor %}
    </select>

    <label>De:</label>
    <input type="date" name="data_inicio" value="{{ data_inicio or '' }}">
    <label>Até:</label>
    <input type="date" name="data_fim" value="{{ data_fim or '' }}">

    <label>Pagamento:</label>
    <select name="status_pagamento">
      <option value="todos" {% if request.args.get('status_pagamento','todos')=='todos' %}selected{% endif %}>Todos</option>
      <option value="pago" {% if request.args.get('status_pagamento')=='pago' %}selected{% endif %}>Pago</option>
      <option value="pendente" {% if request.args.get('status_pagamento')=='pendente' %}selected{% endif %}>Pendente</option>
    </select>

    <label>Cliente:</label>
    <input type="text" name="cliente" value="{{ request.args.get('cliente','') }}" placeholder="Nome do cliente">

    <button class="btn" type="submit">Filtrar</button>
    <a class="btn"
       href="{{ url_for('exportar_xlsx') }}?data_inicio={{ data_inicio or '' }}&data_fim={{ data_fim or '' }}&cooperado_id={{ request.args.get('cooperado_id','todos') }}&status_pagamento={{ request.args.get('status_pagamento','todos') }}&cliente={{ request.args.get('cliente','') | urlencode }}">
      Exportar para Excel
    </a>
  </form>
</div>

<div class="tabela">
  <table>
    <thead>
      <tr>
        <th>ID</th><th>Cliente</th><th>Bairro</th><th>Valor</th>
        <th>Data</th><th>Hora</th><th>Cooperado</th>
        <th>Forma Pgto</th><th>Status</th><th>Pagamento</th><th>Recebido por</th>
      </tr>
    </thead>
    <tbody>
      {% for e in entregas %}
        {% set coop_td = (cooperados|selectattr('id','equalto',e.cooperado_id)|first) %}
        <tr>
          <td>{{ e.id }}</td>
          <td>{{ e.cliente }}</td>
          <td>{{ e.bairro }}</td>
          <td>R$ {{ '%.2f'|format(e.valor) | replace('.', ',') }}</td>
          <td>{{ to_brasilia(e.data_envio).strftime('%d/%m/%Y') if e.data_envio else '-' }}</td>
          <td>{{ to_brasilia(e.data_envio).strftime('%H:%M') if e.data_envio else '-' }}</td>
          <td>{{ coop_td.nome if coop_td else 'Sem Cooperado' }}</td>
          <td>{{ e.pagamento }}</td>
          <td>{{ e.status or '-' }}</td>
          <td>{{ e.status_pagamento or '-' }}</td>
          <td title="{{ e.recebido_por or '-' }}">{{ e.recebido_por or '-' }}</td>
        </tr>
      {% endfor %}
    </tbody>
  </table>
</div>

<script>
  // ===== Drag & Drop da fila =====
  const ul = document.getElementById('espera-lista');
  const formOrd = document.getElementById('espera-salvar-ordem');
  const inputOrd = document.getElementById('espera-ordem');

  function renumerar() {
    [...ul.querySelectorAll('.espera-chip')].forEach((li, i) => {
      const num = li.querySelector('.espera-chip-num');
      if (num) num.textContent = i + 1;
    });
  }
  function salvarOrdem() {
    const ids = [...ul.querySelectorAll('.espera-chip')].map(li => li.dataset.id);
    if (!ids.length) return;
    inputOrd.value = ids.join(',');
    formOrd.submit();
  }

  let dragEl = null;
  ul.addEventListener('dragstart', e => {
    dragEl = e.target.closest('.espera-chip');
    if (!dragEl) return;
    dragEl.classList.add('dragging');
    e.dataTransfer.effectAllowed = 'move';
  });
  ul.addEventListener('dragend', () => {
    if (dragEl) dragEl.classList.remove('dragging');
    dragEl = null;
    renumerar();
    salvarOrdem();
  });
  ul.addEventListener('dragover', e => {
    e.preventDefault();
    const after = getDragAfterElement(ul, e.clientY);
    const dragging = ul.querySelector('.dragging');
    if (!dragging) return;
    if (after == null) ul.appendChild(dragging);
    else ul.insertBefore(dragging, after);
  });

  function getDragAfterElement(container, y) {
    const els = [...container.querySelectorAll('.espera-chip:not(.dragging)')];
    return els.reduce((closest, child) => {
      const box = child.getBoundingClientRect();
      const offset = y - box.top - box.height / 2;
      if (offset < 0 && offset > closest.offset) return { offset, element: child };
      else return closest;
    }, { offset: Number.NEGATIVE_INFINITY }).element;
  }
</script>
</body>
</html>
