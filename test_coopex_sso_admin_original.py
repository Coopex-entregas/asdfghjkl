"""SSO abre o admin REAL e nunca aceita identidade sem assinatura."""
import os
import unittest
from flask import Flask
from itsdangerous import URLSafeTimedSerializer
from unittest.mock import patch
from coopex_sso_admin_original import instalar,AUDIENCIA,EMISSOR,SALT

KEY="test-secret-0123456789abcdefghijklmnopqrstuvwxyz-ABCDEFGHIJKLMNOP"

class TestSSO(unittest.TestCase):
    def setUp(self):
        self.app=Flask(__name__)
        self.app.secret_key="ci"
        instalar(self.app)
        self.client=self.app.test_client()
        self.env=patch.dict(os.environ,{
           "COOPEX_SUPERVISAO_SSO_ATIVO":"1",
           "COOPEX_SUPERVISAO_SSO_SECRET":KEY})
        self.env.start()
        self.addCleanup(self.env.stop)

    def token(self,**changes):
        v={"aud":AUDIENCIA,"iss":EMISSOR,"role":"master",
           "sub":42,"nonce":"N"*48}
        v.update(changes)
        return URLSafeTimedSerializer(KEY,salt=SALT).dumps(v)

    def test_abre_admin_original_e_reutiliza_html(self):
        resp=self.client.get("/sso/financeiro/supervisao-original?token="+self.token())
        self.assertEqual(resp.status_code,303)
        self.assertEqual(resp.headers["Location"],"/admin")
        with self.client.session_transaction() as ses:
            self.assertEqual(ses["coopex_sso_sub"],42)
            self.assertTrue(ses["is_master"])
            self.assertTrue(ses["is_admin"])

    def test_bloqueia_repeticao(self):
        t=self.token(nonce="M"*48)
        self.assertEqual(self.client.get(
            "/sso/financeiro/supervisao-original?token="+t).status_code,303)
        self.assertEqual(self.client.get(
            "/sso/financeiro/supervisao-original?token="+t).status_code,403)

    def test_assinatura_errada_e_perfil_inadequado(self):
        url="/sso/financeiro/supervisao-original?token="
        self.assertEqual(self.client.get(url+self.token(role="admin")).status_code,403)
        self.assertEqual(self.client.get(url+self.token(aud="outra")).status_code,403)
        self.assertEqual(self.client.get(url+self.token(sub=0)).status_code,403)
        self.assertEqual(self.client.get(url+self.token()+"x").status_code,403)

    def test_padroes_nao_aceitam_login(self):
        with patch.dict(os.environ,{"COOPEX_SUPERVISAO_SSO_ATIVO":"0"}):
            self.assertEqual(self.client.get(
                "/sso/financeiro/supervisao-original?token="+self.token()).status_code,503)

if __name__=="__main__":
    unittest.main()
