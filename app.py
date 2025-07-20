<!DOCTYPE html>
<html lang="pt-br">
<head>
  <meta charset="UTF-8" />
  <title>Editar Entrega - Coopex</title>
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <style>
    /* CSS simplificado igual o que você enviou */
  </style>
</head>
<body>
  <div class="form-container">
    <h1>Editar Entrega</h1>
    <a class="back-link" href="{{ url_for('admin' if user_tipo == 'admin' else 'painel_cooperado') }}">← Voltar</a>

    <form method="POST" action="{{ url_for('editar_entrega', entrega_id=entrega.id) }}">
      {% if user_tipo == 'admin' %}
        <label for="descricao">Descrição:</label>
        <input type="text" id="descricao" name="descricao" value="{{ entrega.descricao }}" required />

        <label for="valor">Valor (R$):</label>
        <input type="number" step="0.01" id="valor" name="valor" value="{{ entrega.valor }}" required />

        <label for="cooperado_id">Atribuir Cooperado:</label>
        <select name="cooperado_id" id="cooperado_id">
          <option value="">-- Nenhum --</option>
          {% for c in cooperados %}
            <option value="{{ c.id }}" {% if entrega.cooperado_id == c.id %}selected{% endif %}>{{ c.nome }}</option>
          {% endfor %}
        </select>
      {% else %}
        <p><strong>Descrição:</strong> {{ entrega.descricao }}</p>
        <p><strong>Valor:</strong> R$ {{ '%.2f'|format(entrega.valor) }}</p>
      {% endif %}

      <label for="status_pagamento">Status Pagamento:</label>
      <select name="status_pagamento" id="status_pagamento">
        <option value="pendente" {% if entrega.status_pagamento == 'pendente' %}selected{% endif %}>Pendente</option>
        <option value="pago" {% if entrega.status_pagamento == 'pago' %}selected{% endif %}>Pago</option>
      </select>

      <label for="status_entrega">Status Entrega:</label>
      <select name="status_entrega" id="status_entrega">
        <option value="pendente" {% if entrega.status_entrega == 'pendente' %}selected{% endif %}>Pendente</option>
        <option value="em rota" {% if entrega.status_entrega == 'em rota' %}selected{% endif %}>Em Rota</option>
        <option value="entregue" {% if entrega.status_entrega == 'entregue' %}selected{% endif %}>Entregue</option>
      </select>

      <button type="submit">Salvar</button>
    </form>
  </div>
</body>
</html>
