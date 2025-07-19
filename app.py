from flask import Flask, render_template, request, redirect, url_for
from datetime import datetime
import sqlite3
import os
import pandas as pd

app = Flask(__name__)

# Caminho do banco de dados SQLite
DB_FILE = 'database.db'

# Criação do banco se não existir
def criar_banco():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    cursor.execute('''
    CREATE TABLE IF NOT EXISTS entregas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        cliente TEXT,
        cooperado TEXT,
        bairro TEXT,
        valor REAL,
        hora_pedido TEXT,
        hora_atribuida TEXT,
        status_pagamento TEXT DEFAULT 'pendente',
        status_entrega TEXT DEFAULT 'pendente'
    )
    ''')

    cursor.execute('''
    CREATE TABLE IF NOT EXISTS cooperados (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nome TEXT,
        senha TEXT
    )
    ''')

    conn.commit()
    conn.close()

criar_banco()

@app.route('/')
def login():
    return render_template('login.html')

@app.route('/login', methods=['POST'])
def fazer_login():
    usuario = request.form['usuario']
    senha = request.form['senha']

    if usuario == 'coopex' and senha == '05062721':
        return redirect('/admin')
    else:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM cooperados WHERE nome=? AND senha=?', (usuario, senha))
        cooperado = cursor.fetchone()
        conn.close()

        if cooperado:
            return redirect(f'/cooperado/{cooperado[1]}')
        else:
            return 'Usuário ou senha inválidos'

@app.route('/admin')
def admin():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM entregas')
    entregas = cursor.fetchall()
    cursor.execute('SELECT nome FROM cooperados')
    cooperados = cursor.fetchall()
    conn.close()
    return render_template('admin.html', entregas=entregas, cooperados=cooperados)

@app.route('/cadastro_entrega', methods=['POST'])
def cadastro_entrega():
    cliente = request.form['cliente']
    cooperado = request.form['cooperado']
    bairro = request.form['bairro']
    valor = request.form['valor']
    hora_pedido = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    hora_atribuida = datetime.now().strftime('%Y-%m-%d %H:%M:%S') if cooperado else ''

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO entregas (cliente, cooperado, bairro, valor, hora_pedido, hora_atribuida)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (cliente, cooperado, bairro, valor, hora_pedido, hora_atribuida))
    conn.commit()
    conn.close()
    return redirect('/admin')

@app.route('/cooperado/<nome>')
def cooperado(nome):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM entregas WHERE cooperado=?', (nome,))
    entregas = cursor.fetchall()
    conn.close()
    return render_template('dashboard_cooperado.html', entregas=entregas, nome=nome)

@app.route('/atualizar_status', methods=['POST'])
def atualizar_status():
    entrega_id = request.form['entrega_id']
    status_pagamento = request.form.get('status_pagamento', 'pendente')
    status_entrega = request.form.get('status_entrega', 'pendente')

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE entregas
        SET status_pagamento=?, status_entrega=?
        WHERE id=?
    ''', (status_pagamento, status_entrega, entrega_id))
    conn.commit()
    conn.close()
    return redirect('/admin')

@app.route('/editar_entrega/<int:id>')
def editar_entrega(id):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM entregas WHERE id=?', (id,))
    entrega = cursor.fetchone()
    conn.close()
    return render_template('editar_entrega.html', entrega=entrega)

@app.route('/salvar_edicao/<int:id>', methods=['POST'])
def salvar_edicao(id):
    cooperado = request.form['cooperado']
    status_pagamento = request.form['status_pagamento']
    status_entrega = request.form['status_entrega']
    hora_atribuida = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE entregas
        SET cooperado=?, status_pagamento=?, status_entrega=?, hora_atribuida=?
        WHERE id=?
    ''', (cooperado, status_pagamento, status_entrega, hora_atribuida, id))
    conn.commit()
    conn.close()
    return redirect('/admin')

@app.route('/exportar_excel')
def exportar_excel():
    conn = sqlite3.connect(DB_FILE)
    df = pd.read_sql_query('SELECT * FROM entregas', conn)
    conn.close()
    df.to_excel('entregas_exportadas.xlsx', index=False)
    return 'Exportado como entregas_exportadas.xlsx'

@app.route('/cadastrar_cooperado', methods=['POST'])
def cadastrar_cooperado():
    nome = request.form['nome']
    senha = request.form['senha']

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('INSERT INTO cooperados (nome, senha) VALUES (?, ?)', (nome, senha))
    conn.commit()
    conn.close()
    return redirect('/admin')

@app.route('/excluir_entrega/<int:id>')
def excluir_entrega(id):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('DELETE FROM entregas WHERE id=?', (id,))
    conn.commit()
    conn.close()
    return redirect('/admin')

if __name__ == '__main__':
    app.run(debug=True)
