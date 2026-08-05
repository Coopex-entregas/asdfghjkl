(function () {
  'use strict';

  const STYLE_ID = 'coopex-price-print-style';
  const SHEET_ID = 'coopexPricePrintSheet';
  const API_URL = '/api/precos';

  function norm(value) {
    return String(value || '')
      .normalize('NFD')
      .replace(/[\u0300-\u036f]/g, '')
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, ' ')
      .replace(/\s+/g, ' ')
      .trim();
  }

  function esc(value) {
    return String(value || '').replace(/[&<>"']/g, function (char) {
      return {
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#039;'
      }[char];
    });
  }

  function money(value) {
    return Number(value || 0).toLocaleString('pt-BR', {
      style: 'currency',
      currency: 'BRL',
      minimumFractionDigits: 2,
      maximumFractionDigits: 2
    });
  }

  function injectStyle() {
    if (document.getElementById(STYLE_ID)) return;

    const style = document.createElement('style');
    style.id = STYLE_ID;
    style.textContent = `
      #${SHEET_ID}{display:none}

      .coopex-export-preview{
        margin-top:14px;
        border:1px solid var(--line, #dbe6ff);
        border-radius:16px;
        background:#fff;
        overflow:hidden;
        box-shadow:0 8px 24px rgba(0,39,135,.08);
      }
      body.dark .coopex-export-preview{background:#0f1b34;border-color:#24375f}
      .coopex-export-preview-head{
        padding:14px 16px 10px;
        text-align:center;
        border-bottom:1px solid #dbe6ff;
        background:#fff;
      }
      body.dark .coopex-export-preview-head{background:#0f1b34;border-color:#24375f}
      .coopex-letterhead-logo{
        display:block;
        width:92px;
        height:auto;
        margin:0 auto 4px;
        object-fit:contain;
      }
      .coopex-letterhead-name{
        margin:0;
        color:#111;
        font:800 11px/1.25 Arial, sans-serif;
        text-transform:uppercase;
      }
      body.dark .coopex-letterhead-name,
      body.dark .coopex-letterhead-line{color:#eef4ff}
      .coopex-letterhead-line{
        margin:3px 0 0;
        color:#111;
        font:700 10px/1.22 Arial, sans-serif;
      }
      .coopex-price-title{
        margin:11px 0 0;
        color:#003399;
        font:800 15px/1.2 Arial, sans-serif;
      }
      body.dark .coopex-price-title{color:#b9ccff}
      .coopex-export-preview-body{padding:10px 12px 14px}
      .coopex-export-preview table{
        width:100%;
        min-width:0;
        table-layout:fixed;
        border-collapse:collapse;
        font:700 10px/1.15 Arial, sans-serif;
      }
      .coopex-export-preview th{
        position:static;
        padding:6px 7px;
        background:#003399;
        color:#fff;
        border:1px solid #163f9a;
        font-size:9px;
        text-align:left;
      }
      .coopex-export-preview td{
        padding:5px 7px;
        border:1px solid #d7dfed;
        color:#111;
        background:#fff;
        overflow-wrap:anywhere;
      }
      body.dark .coopex-export-preview td{background:#0f1b34;color:#eef4ff;border-color:#30456d}
      .coopex-export-preview td.price,
      .coopex-export-preview th.price{text-align:right;white-space:nowrap}
      .coopex-export-empty{padding:18px;text-align:center;font-weight:800;color:#66789f}

      @media print{
        @page{size:A4 portrait;margin:6mm}
        html,body{width:100%!important;min-height:0!important;background:#fff!important}
        body.coopex-print-price-sheet > *{display:none!important}
        body.coopex-print-price-sheet > #${SHEET_ID}{display:block!important}
        #${SHEET_ID}{
          width:100%;
          margin:0;
          padding:0;
          background:#fff;
          color:#000;
          font-family:Arial, Helvetica, sans-serif;
          break-inside:avoid;
          page-break-inside:avoid;
        }
        #${SHEET_ID} .print-letterhead{
          text-align:center;
          padding:0 0 3mm;
          border-bottom:.35mm solid #1c1c1c;
        }
        #${SHEET_ID} .print-letterhead img{
          display:block;
          width:32mm;
          height:auto;
          margin:0 auto 1mm;
          object-fit:contain;
        }
        #${SHEET_ID} .print-letterhead .org{
          margin:0;
          font-size:8.2pt;
          line-height:1.15;
          font-weight:800;
          text-transform:uppercase;
        }
        #${SHEET_ID} .print-letterhead .meta{
          margin:.8mm 0 0;
          font-size:7.5pt;
          line-height:1.12;
          font-weight:700;
        }
        #${SHEET_ID} .print-title{
          margin:3.4mm 0 3mm;
          text-align:center;
          color:#003399;
          font-size:11pt;
          line-height:1.15;
          font-weight:800;
        }
        #${SHEET_ID} table{
          width:100%;
          min-width:0!important;
          table-layout:fixed;
          border-collapse:collapse;
          border-spacing:0;
          font-size:6.8pt;
          line-height:1.05;
          page-break-inside:avoid;
        }
        #${SHEET_ID} th{
          position:static!important;
          padding:1.25mm 1.35mm;
          background:#003399!important;
          color:#fff!important;
          border:.25mm solid #173e96;
          font-size:6.6pt;
          font-weight:800;
          text-align:left;
          -webkit-print-color-adjust:exact;
          print-color-adjust:exact;
        }
        #${SHEET_ID} td{
          height:4.6mm;
          padding:.85mm 1.35mm;
          border:.22mm solid #bfc9d9;
          background:#fff!important;
          color:#000!important;
          font-size:6.8pt;
          font-weight:700;
          vertical-align:middle;
          overflow:hidden;
          text-overflow:ellipsis;
          white-space:nowrap;
        }
        #${SHEET_ID} th.price,
        #${SHEET_ID} td.price{
          width:16mm;
          text-align:right;
          white-space:nowrap;
        }
        #${SHEET_ID} th.destination,
        #${SHEET_ID} td.destination{width:auto}
        #${SHEET_ID} .print-footer{
          margin-top:2mm;
          text-align:right;
          font-size:6.5pt;
          color:#333;
        }
      }
    `;
    document.head.appendChild(style);
  }

  async function loadRoutes() {
    const response = await fetch(API_URL, {
      credentials: 'same-origin',
      cache: 'no-store',
      headers: { Accept: 'application/json' }
    });
    const data = await response.json().catch(function () { return {}; });
    if (!response.ok || data.ok === false) {
      throw new Error(data.error || 'Não foi possível carregar os preços cadastrados.');
    }
    return Array.isArray(data.items) ? data.items : [];
  }

  function routesForOrigin(routes, origin) {
    const originKey = norm(origin);
    const direct = new Map();
    const reverse = new Map();

    routes.forEach(function (route) {
      const value = Number(route.valor || 0);
      if (!(value > 0)) return;

      const fromKey = norm(route.origem);
      const toKey = norm(route.destino);
      if (!fromKey || !toKey) return;

      if (fromKey === originKey) {
        direct.set(toKey, {
          destino: String(route.destino || '').trim(),
          valor: value
        });
      } else if (toKey === originKey && !reverse.has(fromKey)) {
        reverse.set(fromKey, {
          destino: String(route.origem || '').trim(),
          valor: value
        });
      }
    });

    reverse.forEach(function (item, key) {
      if (!direct.has(key)) direct.set(key, item);
    });

    return Array.from(direct.values())
      .filter(function (item) { return item.destino && item.valor > 0; })
      .sort(function (a, b) {
        return a.destino.localeCompare(b.destino, 'pt-BR', {
          sensitivity: 'base',
          numeric: true
        });
      });
  }

  function columnize(items, columns) {
    const perColumn = Math.max(1, Math.ceil(items.length / columns));
    const rows = [];

    for (let rowIndex = 0; rowIndex < perColumn; rowIndex += 1) {
      const row = [];
      for (let columnIndex = 0; columnIndex < columns; columnIndex += 1) {
        row.push(items[rowIndex + columnIndex * perColumn] || null);
      }
      rows.push(row);
    }
    return rows;
  }

  function tableMarkup(items, compact) {
    const columns = items.length > 108 ? 4 : 3;
    const rows = columnize(items, columns);

    const headers = Array.from({ length: columns }, function () {
      return '<th class="destination">Bairro destino</th><th class="price">Valor</th>';
    }).join('');

    const body = rows.map(function (row) {
      return '<tr>' + row.map(function (item) {
        if (!item) return '<td class="destination"></td><td class="price"></td>';
        return '<td class="destination">' + esc(item.destino) + '</td>' +
          '<td class="price">' + esc(money(item.valor)) + '</td>';
      }).join('') + '</tr>';
    }).join('');

    if (!items.length) {
      return '<div class="coopex-export-empty">Nenhuma rota com valor acima de R$ 0,00 para esta origem.</div>';
    }

    return '<table class="' + (compact ? 'compact' : '') + '">' +
      '<thead><tr>' + headers + '</tr></thead>' +
      '<tbody>' + body + '</tbody>' +
      '</table>';
  }

  function letterheadMarkup(origin, printMode) {
    const logo = '/static/logo_coopex.png';
    const cls = printMode ? 'print-letterhead' : 'coopex-export-preview-head';
    const orgClass = printMode ? 'org' : 'coopex-letterhead-name';
    const metaClass = printMode ? 'meta' : 'coopex-letterhead-line';
    const titleClass = printMode ? 'print-title' : 'coopex-price-title';

    return '<div class="' + cls + '">' +
      '<img class="coopex-letterhead-logo" src="' + logo + '" alt="COOPEX">' +
      '<p class="' + orgClass + '">COOPERATIVA DE TRABALHADORES DE ENTREGAS DO RIO GRANDE DO NORTE - COOPEX</p>' +
      '<p class="' + metaClass + '">CNPJ: 05.289.938/0001-97</p>' +
      '<p class="' + metaClass + '">Rua: José Freire de Souza, 22 - Lagoa Nova - Natal/RN, CEP: 59075-140</p>' +
      '<p class="' + metaClass + '">Fone/WhatsApp: (84) 3234-9025 / 3231-5623 / 98111-0706</p>' +
      '</div>' +
      '<h1 class="' + titleClass + '">Tabela de valores saindo de ' + esc(origin) + '</h1>';
  }

  function buildPreview(origin, items) {
    const preview = document.getElementById('exportPreview');
    if (!preview) return;

    preview.innerHTML = '<section class="coopex-export-preview">' +
      letterheadMarkup(origin, false) +
      '<div class="coopex-export-preview-body">' + tableMarkup(items, false) + '</div>' +
      '</section>';
  }

  function buildPrintSheet(origin, items) {
    let sheet = document.getElementById(SHEET_ID);
    if (!sheet) {
      sheet = document.createElement('section');
      sheet.id = SHEET_ID;
      document.body.appendChild(sheet);
    }

    sheet.innerHTML = letterheadMarkup(origin, true) +
      tableMarkup(items, true) +
      '<div class="print-footer">Valores cadastrados no sistema COOPEX.</div>';
  }

  async function render() {
    const select = document.getElementById('exportOrigem');
    if (!select) return;

    const origin = String(select.value || '').trim();
    if (!origin) return;

    const preview = document.getElementById('exportPreview');
    if (preview) preview.innerHTML = '<div class="coopex-export-empty">Carregando tabela...</div>';

    try {
      const routes = await loadRoutes();
      const items = routesForOrigin(routes, origin);
      buildPreview(origin, items);
      buildPrintSheet(origin, items);
    } catch (error) {
      if (preview) {
        preview.innerHTML = '<div class="coopex-export-empty">' + esc(error.message || 'Erro ao gerar a tabela.') + '</div>';
      }
    }
  }

  async function printTable() {
    await render();
    const select = document.getElementById('exportOrigem');
    const origin = String(select && select.value || '').trim();
    const sheet = document.getElementById(SHEET_ID);
    if (!origin || !sheet || !sheet.querySelector('table')) return;

    document.body.classList.add('coopex-print-price-sheet');
    setTimeout(function () {
      window.print();
    }, 120);
  }

  function wire() {
    injectStyle();

    const select = document.getElementById('exportOrigem');
    const button = document.getElementById('btnExportPdf');
    const exportTab = document.querySelector('.tab[data-tab="exportar"]');
    const note = document.querySelector('#tabExportar .note');

    if (!select || !button) return false;

    button.textContent = 'Gerar tabela / PDF';
    button.onclick = function (event) {
      event.preventDefault();
      printTable();
    };

    select.onchange = render;

    if (exportTab) {
      exportTab.addEventListener('click', function () {
        setTimeout(render, 80);
      });
    }

    if (note) {
      note.innerHTML = 'A tabela será gerada em <strong>uma única folha A4</strong>, com o cabeçalho da COOPEX, somente bairros com valor acima de R$ 0,00 e destinos em ordem alfabética.';
    }

    window.addEventListener('afterprint', function () {
      document.body.classList.remove('coopex-print-price-sheet');
    });

    setTimeout(render, 250);
    return true;
  }

  function start() {
    let attempts = 0;
    const timer = setInterval(function () {
      attempts += 1;
      if (wire() || attempts >= 40) clearInterval(timer);
    }, 100);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start, { once: true });
  } else {
    start();
  }
})();
