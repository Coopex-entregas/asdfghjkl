"""Sem banco e sem Cloudflare real: ticket sempre emitido da origem."""
import hashlib
import hmac
import json
import os
import time
import unittest
from unittest.mock import patch
from flask import Flask
from coopex_gps_ticket_bridge import registrar

SECRET = "sso-ci-test-secret-longer-than-48-characters-0123456789"
PAYLOAD = b'{"scope":"admin_map"}'


class TicketTest(unittest.TestCase):
    def setUp(self):
        app=Flask(__name__)
        registrar(app)
        self.client=app.test_client()
        self.ctx=patch.dict(os.environ,{
            "COOPEX_SUPERVISAO_SSO_SECRET":SECRET,
            "COOPEX_GPS_MAP_SIGN_KEY":"original-cloudflare-map-key-test",
            "COOPEX_GPS_API_URL":"https://original-gps.example.workers.dev"
        })
        self.ctx.start()
        self.addCleanup(self.ctx.stop)

    def post(self,secret=SECRET,ts=None,body=PAYLOAD):
        ts=str(int(time.time())) if ts is None else str(ts)
        sig=hmac.new(secret.encode(),b"coopex-gps-ticket-v1\n"+ts.encode()+b"\n"+body,hashlib.sha256).hexdigest()
        return self.client.post("/api/interno/coopex-financeiro/gps-ticket",data=body,headers={
            "X-COOPEX-Timestamp":ts,
            "X-COOPEX-Signature":sig,
            "Content-Type":"application/json"
        })

    def test_ticket_emite_mesmo_endpoint_cloudflare_original(self):
        r=self.post()
        self.assertEqual(r.status_code,200)
        self.assertEqual(r.json["url"],"https://original-gps.example.workers.dev/v1/positions")
        self.assertTrue(r.json["ticket"])
        self.assertEqual(r.headers["Cache-Control"],"no-store")

    def test_nao_autorizado(self):
        self.assertEqual(self.post(secret="another_secret").status_code,403)
        self.assertEqual(self.post(ts=int(time.time())-180).status_code,403)
        self.assertEqual(self.client.post("/api/interno/coopex-financeiro/gps-ticket",data=PAYLOAD).status_code,403)

    def test_escopo_invalido(self):
        self.assertEqual(self.post(body=b'{"scope":"cooperado_gps"}').status_code,403)

    def test_sem_gps_nao_inventa_ticket(self):
        with patch.dict(os.environ,{"COOPEX_GPS_MAP_SIGN_KEY":""}):
            self.assertEqual(self.post().status_code,503)

if __name__ == "__main__":
    unittest.main()
