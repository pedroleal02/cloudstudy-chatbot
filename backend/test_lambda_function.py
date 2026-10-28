"""
Testes da Lambda com os clientes da AWS simulados (nenhuma chamada real, sem custo).
Requer: boto3 instalado (ex.: sudo apt install python3-boto3)
Rodar:  python3 -m unittest test_lambda_function -v
"""
import base64
import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

os.environ.setdefault("MODEL_ID", "modelo-de-teste")
os.environ.setdefault("KB_ID", "KBTESTE123")
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

import lambda_function as L  # noqa: E402
from botocore.exceptions import BotoCoreError, ClientError  # noqa: E402

CONTEXTO = SimpleNamespace(aws_request_id="req-12345678")


def trecho(texto, arquivo="recursao.md", score=0.8):
    return {
        "content": {"text": texto},
        "location": {"type": "S3", "s3Location": {"uri": f"s3://bucket-secreto/documentos/mat/{arquivo}"}},
        "score": score,
    }


def erro_aws(codigo):
    return ClientError({"Error": {"Code": codigo, "Message": "detalhe interno arn:aws:bedrock:..."}}, "Op")


class TestLambda(unittest.TestCase):

    def setUp(self):
        L.agente = MagicMock()
        L.bedrock = MagicMock()
        L.SCORE_MINIMO = 0.0
        L.agente.retrieve.return_value = {
            "retrievalResults": [trecho("Recursão é quando uma função chama a si mesma.")]
        }
        self.responder("Recursão é uma função que chama a si mesma [1].")

    def responder(self, texto):
        L.bedrock.converse.return_value = {"output": {"message": {"content": [{"text": texto}]}}}

    def chamar(self, corpo, bruto=False):
        evento = {"body": corpo if bruto else json.dumps(corpo)}
        r = L.lambda_handler(evento, CONTEXTO)
        return r["statusCode"], json.loads(r["body"]), r

    def mensagem_enviada_ao_modelo(self):
        return L.bedrock.converse.call_args.kwargs["messages"][0]["content"][0]["text"]

    # ---------- caminho feliz e perguntas sem resposta ----------

    def test_pergunta_valida_devolve_resposta_e_referencias(self):
        status, corpo, _ = self.chamar({"pergunta": "O que é recursão?"})
        self.assertEqual(status, 200)
        self.assertIn("Recursão", corpo["resposta"])
        self.assertEqual(corpo["referencias"][0]["arquivo"], "recursao.md")

    def test_sem_trechos_chama_o_modelo_com_contexto_vazio_e_sem_referencias(self):
        L.agente.retrieve.return_value = {"retrievalResults": []}
        self.responder("Não há evidências nos materiais para responder isso.")

        status, corpo, _ = self.chamar({"pergunta": "Como fazer bolo de cenoura?"})

        self.assertEqual(status, 200)
        L.bedrock.converse.assert_called_once()
        self.assertIn("<contexto>\n\n</contexto>", self.mensagem_enviada_ao_modelo())
        self.assertEqual(corpo["resposta"], "Não há evidências nos materiais para responder isso.")
        self.assertEqual(corpo["referencias"], [])

    def test_score_abaixo_do_minimo_descarta_o_trecho(self):
        L.SCORE_MINIMO = 0.6
        L.agente.retrieve.return_value = {"retrievalResults": [trecho("irrelevante", score=0.2)]}
        self.responder("Não encontrei isso nos materiais.")

        status, corpo, _ = self.chamar({"pergunta": "pergunta fora do assunto"})

        self.assertEqual(status, 200)
        L.bedrock.converse.assert_called_once()
        msg = self.mensagem_enviada_ao_modelo()
        self.assertNotIn("irrelevante", msg)              # o trecho filtrado não chega ao modelo
        self.assertIn("<contexto>\n\n</contexto>", msg)
        self.assertEqual(corpo["referencias"], [])

    def test_modelo_que_nao_cita_nao_gera_referencias(self):
        self.responder("Não encontrei isso nos materiais.")
        _, corpo, _ = self.chamar({"pergunta": "pergunta qualquer"})
        self.assertEqual(corpo["referencias"], [])

    # ---------- validação de entrada ----------

    def test_pergunta_com_tipo_errado(self):
        for valor in (123, ["a"], {"a": 1}, None, True):
            with self.subTest(valor=valor):
                status, corpo, _ = self.chamar({"pergunta": valor})
                self.assertEqual(status, 400)
                self.assertIn("erro", corpo)

    def test_corpo_que_nao_e_json_ou_nao_e_objeto(self):
        for bruto in ("{quebrado", "[]", '"texto"', "123", ""):
            with self.subTest(bruto=bruto):
                self.assertEqual(self.chamar(bruto, bruto=True)[0], 400)

    def test_corpo_ausente(self):
        r = L.lambda_handler({"body": None}, CONTEXTO)
        self.assertEqual(r["statusCode"], 400)
        r = L.lambda_handler(None, CONTEXTO)
        self.assertEqual(r["statusCode"], 400)