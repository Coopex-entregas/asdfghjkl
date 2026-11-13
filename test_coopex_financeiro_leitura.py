"""Teste da consulta HMAC sem iniciar app ou conexao real."""
import os,json,hmac,hashlib,time,unittest
from unittest.mock import MagicMock,patch
from flask import Flask
from datetime import datetime
from coopex_financeiro_leitura import instalar
CHAVE="segredo-teste-hmac-longuissimo-0123456789"

class TestLeitura(unittest.TestCase):
    def setUp(self):
        self.app=Flask(__name__);self.app.testing=True
        self.db=MagicMock()
        self.con=MagicMock()
        self.db.engine.connect.return_value.__enter__.return_value=self.con
        self.con.execute.side_effect=[
            MagicMock(mappings=lambda:MagicMock(all=lambda:[{
                "id":123,"cliente":"Cliente","bairro":"Tirol","valor":8.0,
                "pagamento":"Pix","cooperado_id":71,
                "cooperado_nome":"Origem","status_corrida":"aceita",
                "status":"em_andamento","status_pagamento":"pendente",
                "data_envio":datetime(2026,11,13,20,10)}])),
            MagicMock(mappings=lambda:MagicMock(all=lambda:[
                {"situacao":"aceita","quantidade":1}])),
        ]
        instalar(self.app,self.db)
        self.client=self.app.test_client()
        self.env=patch.dict(os.environ,{
            "COOPEX_SUPERVISAO_BRIDGE_LEITURA_ATIVA":"1",
            "COOPEX_SUPERVISAO_BRIDGE_SECRET":CHAVE})
        self.env.start();self.addCleanup(self.env.stop)

    def send(self,sign=True):
        raw=json.dumps({"inicio":"2026-11-13T03:00:00",
                        "fim":"2026-11-14T03:00:00"}).encode()
        ts=str(int(time.time()))
        h={"Content-Type":"application/json","X-COOPEX-Timestamp":ts}
        if sign:
            h["X-COOPEX-Signature"]=hmac.new(
                CHAVE.encode(),ts.encode()+b"\n"+raw,hashlib.sha256).hexdigest()
        with patch("coopex_financeiro_leitura.datetime") as dt:
            dt.fromisoformat=datetime.fromisoformat
            dt.utcnow.return_value=datetime(2026,11,13,20,0)
            return self.client.post("/api/interno/coopex-financeiro/consulta",
                                    data=raw,headers=h)

    def test_nao_exibe_sem_assinatura(self):
        self.assertEqual(self.send(False).status_code,403)
        self.con.execute.assert_not_called()

    def test_sem_flag_e_404(self):
        with patch.dict(os.environ,{"COOPEX_SUPERVISAO_BRIDGE_LEITURA_ATIVA":"0"}):
            self.assertEqual(self.send().status_code,404)

    def test_leitura_segura_e_total(self):
        r=self.send()
        self.assertEqual(r.status_code,200,r.get_json())
        self.assertEqual(r.json["itens"][0]["id"],123)
        self.assertIsNone(r.json["itens"][0]["cooperado_id"])
        self.assertEqual(r.json["resumo"]["total"],1)
        self.assertFalse(r.json["financeiro_alterado"])
        sqls=[x.args[0].text for x in self.con.execute.call_args_list]
        self.assertIn("SET TRANSACTION READ ONLY",sqls[0])
        self.assertEqual(len(sqls),3)
        self.assertFalse(any(x.lstrip().startswith("UPDATE") for x in sqls))

if __name__=="__main__":unittest.main()
