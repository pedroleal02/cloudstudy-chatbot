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

USUARIO_A = "11111111-aaaa-4aaa-8aaa-111111111111"
USUARIO_B = "22222222-bbbb-4bbb-8bbb-222222222222"


def contexto_autenticado(sub=USUARIO_A):
    """Parte do evento que o API Gateway HTTP API preenche depois de validar o JWT."""
    return {"authorizer": {"jwt": {"claims": {"sub": sub}, "scopes": None}}}


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

    def chamar(self, corpo, bruto=False, autenticado=True):
        evento = {"body": corpo if bruto else json.dumps(corpo)}
        if autenticado:
            evento["requestContext"] = contexto_autenticado()
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
        r = L.lambda_handler({"body": None, "requestContext": contexto_autenticado()}, CONTEXTO)
        self.assertEqual(r["statusCode"], 400)

    # ---------- autenticação ----------

    def assertNaoChamouAws(self):
        L.agente.retrieve.assert_not_called()
        L.bedrock.converse.assert_not_called()

    def test_sem_contexto_de_autenticacao_devolve_401_e_nao_chama_a_aws(self):
        status, corpo, _ = self.chamar({"pergunta": "O que é recursão?"}, autenticado=False)
        self.assertEqual(status, 401)
        self.assertEqual(corpo, {"erro": "Não autenticado."})
        self.assertNaoChamouAws()

    def test_evento_invalido_devolve_401(self):
        for evento in (None, [], "texto", {}, {"requestContext": None}, {"requestContext": {}}):
            with self.subTest(evento=evento):
                self.assertEqual(L.lambda_handler(evento, CONTEXTO)["statusCode"], 401)
        self.assertNaoChamouAws()

    def test_claims_sem_sub_ou_com_sub_invalido_devolvem_401(self):
        invalidos = [
            {"authorizer": {"jwt": {"claims": {}}}},
            {"authorizer": {"jwt": {"claims": {"sub": ""}}}},
            {"authorizer": {"jwt": {"claims": {"sub": 123}}}},
            {"authorizer": {"jwt": {"claims": {"sub": "curto"}}}},
            {"authorizer": {"jwt": {"claims": {"sub": "../../outro-usuario"}}}},
            {"authorizer": {"jwt": {"claims": {"sub": "a" * 100}}}},
            {"authorizer": {"jwt": None}},
            {"authorizer": {"lambda": {"sub": USUARIO_A}}},  # outro tipo de authorizer não vale
        ]
        for contexto in invalidos:
            with self.subTest(contexto=contexto):
                evento = {"body": json.dumps({"pergunta": "O que é recursão?"}), "requestContext": contexto}
                self.assertEqual(L.lambda_handler(evento, CONTEXTO)["statusCode"], 401)
        self.assertNaoChamouAws()

    def test_autenticacao_e_checada_antes_do_corpo(self):
        # corpo quebrado sem login deve ser 401, não 400: nada do corpo é interpretado
        self.assertEqual(self.chamar("{quebrado", bruto=True, autenticado=False)[0], 401)

    def test_identidade_vem_do_token_e_nao_do_corpo(self):
        evento = {
            "body": json.dumps({"pergunta": "O que é recursão?", "userId": USUARIO_B, "sub": USUARIO_B}),
            "headers": {"x-user-id": USUARIO_B},
            "queryStringParameters": {"userId": USUARIO_B},
            "requestContext": contexto_autenticado(USUARIO_A),
        }
        self.assertEqual(L.identificar_usuario(evento), USUARIO_A)

    def test_usuario_autenticado_recebe_200(self):
        evento = {
            "body": json.dumps({"pergunta": "O que é recursão?"}),
            "requestContext": contexto_autenticado(USUARIO_B),
        }
        self.assertEqual(L.lambda_handler(evento, CONTEXTO)["statusCode"], 200)

    def test_log_nao_expoe_o_id_completo_do_usuario(self):
        with self.assertLogs(level="INFO") as logs:
            self.chamar({"pergunta": "O que é recursão?"})
        saida = "\n".join(logs.output)
        self.assertIn(USUARIO_A[:8], saida)
        self.assertNotIn(USUARIO_A, saida)