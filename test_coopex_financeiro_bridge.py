"""Testa a ponte segura sem iniciar a Supervisao nem acessar banco real."""
import hashlib
import hmac
import json
import os
import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from flask import Flask
from coopex_financeiro_bridge import (
    instalar, _verificar_assinatura, _validar_payload,
)

SECRET="test-only-key-bridge-0123456789abcdef"
URL="/api/interno/coopex-financeiro/atribuir"


class HMACTest(unittest.TestCase):
    def test_assinatura_invalida_ou_vencida_recusada(self):
        raw=b'{"entrega_id":1}'
        ts="2000000000"
        sig=hmac.new(SECRET.encode(),ts.encode()+b"\n"+raw,
                     hashlib.sha256).hexdigest()
        self.assertTrue(_verificar_assinatura(raw,ts,sig,SECRET.encode(),
                                             now=2000000000))
        self.assertFalse(_verificar_assinatura(raw,ts,sig,SECRET.encode(),
                                              now=2000000100))
        self.assertFalse(_verificar_assinatura(b"diferente",ts,sig,
                                              SECRET.encode(),now=2000000000))
        self.assertFalse(_verificar_assinatura(raw,"٢٠٠٠٠٠٠٠٠٠",sig,
                                              SECRET.encode(),now=2000000000))

    def test_payload_invalido_bloqueado(self):
        original={"entrega_id":123,"cooperado_id_legado":71,
          "esperado_cooperado_id_legado":None,"esperado_status_corrida":None}
        self.assertEqual(_validar_payload(json.dumps(original).encode()),original)
        for cambio in ({"entrega_id":True},{"entrega_id":0},
                       {"cooperado_id_legado":"71"},
                       {"esperado_cooperado_id_legado":-1},
                       {"campo_extra":"nao"}):
            p={**original,**cambio}
            with self.subTest(cambio=cambio):
                with self.assertRaises(ValueError):
                    _validar_payload(json.dumps(p).encode())


class PonteHTTPTest(unittest.TestCase):
    def setUp(self):
        self.app=Flask(__name__)
        self.app.testing=True
        self.entrega=SimpleNamespace(id=123,cooperado_id=None,
            status_corrida=None,status="pendente")
        self.cooperado=SimpleNamespace(id=71,ativo=True)
        entrega_q=MagicMock()
        entrega_q.filter.return_value.with_for_update.return_value.one_or_none.return_value=self.entrega
        coop_q=MagicMock()
        coop_q.filter.return_value.first.return_value=self.cooperado
        self.db=MagicMock()
        self.db.session.query.side_effect=[entrega_q,coop_q]
        self.core=MagicMock(side_effect=self.atribuir_fake)
        instalar(self.app,self.db,object(),object(),self.core)
        self.client=self.app.test_client()
        self.ambiente=patch.dict(os.environ,{
            "COOPEX_SUPERVISAO_BRIDGE_ATIVO":"1",
            "COOPEX_SUPERVISAO_BRIDGE_SECRET":SECRET})
        self.ambiente.start()
        self.addCleanup(self.ambiente.stop)
        self.dados={"entrega_id":123,"cooperado_id_legado":71,
            "esperado_cooperado_id_legado":None,"esperado_status_corrida":None}

    def atribuir_fake(self,entrega,coop_id,commit):
        self.assertTrue(commit)
        entrega.cooperado_id=coop_id
        entrega.status_corrida="aceita"

    def enviar(self,data=None,assinar=True):
        raw=json.dumps(data or self.dados,separators=(",",":")).encode()
        ts=str(int(time.time()))
        headers={"Content-Type":"application/json",
                 "X-COOPEX-Timestamp":ts}
        if assinar:
            headers["X-COOPEX-Signature"]=hmac.new(
                SECRET.encode(),ts.encode()+b"\n"+raw,
                hashlib.sha256).hexdigest()
        return self.client.post(URL,data=raw,headers=headers)

    def test_ponte_desativada(self):
        with patch.dict(os.environ,{"COOPEX_SUPERVISAO_BRIDGE_ATIVO":"0"}):
            self.assertEqual(self.enviar().status_code,404)
        self.core.assert_not_called()

    def test_sem_assinatura(self):
        self.assertEqual(self.enviar(assinar=False).status_code,403)
        self.db.session.query.assert_not_called()

    def test_atribuicao_com_avisos_pelo_core_original(self):
        r=self.enviar()
        self.assertEqual(r.status_code,200,r.get_json())
        self.assertEqual(r.json["cooperado_id_legado"],71)
        self.core.assert_called_once_with(self.entrega,71,commit=True)

    def test_recusa_operacao_com_versao_atual_desatualizada(self):
        self.entrega.cooperado_id=99
        self.assertEqual(self.enviar().status_code,409)
        self.core.assert_not_called()

    def test_entrega_finalizada_nao_pode_ser_atribuida(self):
        self.entrega.status="recebido"
        self.assertEqual(self.enviar().status_code,409)
        self.core.assert_not_called()

    def test_repeticao_nao_duplica_atribuicao(self):
        self.assertEqual(self.enviar().status_code,200)
        self.assertEqual(self.enviar().status_code,409)
        self.core.assert_called_once()


if __name__=="__main__":
    unittest.main()
