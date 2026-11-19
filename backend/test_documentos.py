"""
Testes do envio de documentos (POST pré-assinado). Sem AWS: S3 e DynamoDB são simulados.
Rodar:  python3 -m unittest test_documentos -v
"""
import os
import re
import unittest
from unittest.mock import patch

import documentos as D
import modelos as M
import notebooks as N
from botocore.exceptions import BotoCoreError, ClientError
from test_notebooks import Base, USUARIO_A, USUARIO_B

ROTA = "POST /notebooks/{notebookId}/documentos/upload"


class S3Falso:
    def __init__(self):
        self.chamadas = []
        self.falhar_com = None

    def generate_presigned_post(self, Bucket, Key, Fields, Conditions, ExpiresIn):
        if self.falhar_com:
            raise self.falhar_com
        self.chamadas.append({"Bucket": Bucket, "Key": Key, "Fields": Fields, "Conditions": Conditions, "ExpiresIn": ExpiresIn})
        return {"url": f"https://{Bucket}.s3.amazonaws.com/", "fields": {**Fields, "key": Key, "policy": "p", "x-amz-signature": "s"}}


class BaseDocs(Base):
    def setUp(self):
        super().setUp()
        self.s3 = S3Falso()
        D.s3 = self.s3
        patcher = patch.dict(os.environ, {"DOCS_BUCKET": "bucket-de-teste"})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(setattr, D, "s3", None)

    def pedir(self, nb_id, nome="apostila.pdf", tamanho=2048, usuario=USUARIO_A, **extra):
        return self.chamar(ROTA, usuario, {"nome": nome, "tamanhoBytes": tamanho, **extra}, nb_id)


class TestSolicitarUpload(BaseDocs):

    def test_autoriza_e_devolve_url_campos_e_documento(self):
        nb = self.criar("SO")["notebookId"]
        status, corpo, _ = self.pedir(nb, "  Apostila de SO.PDF ", 2048)
        self.assertEqual(status, 200)
        doc, up = corpo["documento"], corpo["upload"]
        self.assertRegex(doc["documentoId"], M.ID_DE_ITEM)
        self.assertEqual((doc["nome"], doc["tipo"], doc["tamanhoBytes"], doc["notebookId"]), ("Apostila de SO.PDF", "pdf", 2048, nb))
        self.assertEqual(up["url"], "https://bucket-de-teste.s3.amazonaws.com/")
        self.assertEqual(up["expiraEmSegundos"], 300)
        self.assertEqual(up["campos"]["Content-Type"], "application/pdf")
        self.assertNotIn("chaveS3", str(corpo))

    def test_chave_e_politica_assinada(self):
        nb = self.criar("SO")["notebookId"]
        _, corpo, _ = self.pedir(nb, "a.md", 500)
        c = self.s3.chamadas[0]
        self.assertEqual(c["Bucket"], "bucket-de-teste")
        self.assertEqual(c["Key"], f"usuarios/{USUARIO_A}/{nb}/{corpo['documento']['documentoId']}.md")
        self.assertEqual(c["ExpiresIn"], 300)
        self.assertIn({"Content-Type": "text/markdown"}, c["Conditions"])
        self.assertIn(["content-length-range", 500, 500], c["Conditions"])  # tamanho exato

    def test_tipo_de_conteudo_vem_da_extensao_e_nao_do_cliente(self):
        nb = self.criar()["notebookId"]
        for nome, esperado in (("a.md", "text/markdown"), ("a.txt", "text/plain"), ("a.PDF", "application/pdf")):
            self.pedir(nb, nome, 10, contentType="text/html", tipo="exe", chave="../x", key="x")
            self.assertEqual(self.s3.chamadas[-1]["Fields"], {"Content-Type": esperado}, nome)
            self.assertRegex(self.s3.chamadas[-1]["Key"], rf"^usuarios/{USUARIO_A}/[0-9A-Z]{{26}}/[0-9A-Z]{{26}}\.[a-z]+$")

    def test_nome_do_arquivo_nunca_entra_na_chave(self):
        nb = self.criar()["notebookId"]
        status, _, _ = self.pedir(nb, "../../outro/x#y?z.pdf", 10)
        self.assertEqual(status, 200)
        self.assertNotIn("outro", self.s3.chamadas[-1]["Key"])
        self.assertNotIn("..", self.s3.chamadas[-1]["Key"])

    def test_cada_pedido_gera_um_documento_novo(self):
        nb = self.criar()["notebookId"]
        ids = {self.pedir(nb)[1]["documento"]["documentoId"] for _ in range(4)}
        self.assertEqual(len(ids), 4)

    def test_arquivo_invalido_devolve_400_sem_chamar_o_s3(self):
        nb = self.criar()["notebookId"]
        casos = [("a.exe", 10), ("a.docx", 10), ("semextensao", 10), ("a.", 10), ("", 10), ("   ", 10), ("a.pdf", 0), ("a.pdf", -5),
                 ("a.pdf", True), ("a.pdf", 1.5), ("a.pdf", "10"), ("a.pdf", None), ("a.pdf", M.MAX_TAMANHO_DOCUMENTO + 1), ("x" * 300 + ".pdf", 10)]
        for nome, tamanho in casos:
            with self.subTest(nome=nome[:20], tamanho=tamanho):
                self.assertEqual(self.pedir(nb, nome, tamanho)[0], 400)
        self.assertEqual(self.s3.chamadas, [])

    def test_tamanho_maximo_aceito(self):
        nb = self.criar()["notebookId"]
        self.assertEqual(self.pedir(nb, "a.pdf", M.MAX_TAMANHO_DOCUMENTO)[0], 200)

    def test_corpo_invalido(self):
        nb = self.criar()["notebookId"]
        for bruto in ("{quebrado", "[]", '"x"'):
            self.assertEqual(self.chamar(ROTA, USUARIO_A, notebook_id=nb, bruto=bruto)[0], 400)
        self.assertEqual(self.chamar(ROTA, USUARIO_A, notebook_id=nb)[0], 400)
        self.assertEqual(self.s3.chamadas, [])


class TestPropriedade(BaseDocs):

    def test_notebook_de_outro_usuario_devolve_404_sem_chamar_o_s3(self):
        nb_a = self.criar("do A", USUARIO_A)["notebookId"]
        status, corpo, _ = self.pedir(nb_a, usuario=USUARIO_B)
        self.assertEqual((status, corpo), (404, {"erro": "Notebook não encontrado."}))
        self.assertEqual(self.s3.chamadas, [])

    def test_notebook_inexistente_ou_id_malformado_devolve_404(self):
        for nid in (M.novo_id(), "abc", "../x", None):
            with self.subTest(id=nid):
                self.assertEqual(self.pedir(nid)[0], 404)
        self.assertEqual(self.s3.chamadas, [])

    def test_a_chave_usa_o_usuario_do_token_e_nunca_o_do_corpo(self):
        nb = self.criar("do A", USUARIO_A)["notebookId"]
        self.pedir(nb, usuarioId=USUARIO_B, sub=USUARIO_B, pk=f"USER#{USUARIO_B}")
        self.assertTrue(self.s3.chamadas[-1]["Key"].startswith(f"usuarios/{USUARIO_A}/{nb}/"))

    def test_sem_autenticacao_devolve_401(self):
        nb = self.criar()["notebookId"]
        status, _, _ = self.chamar(ROTA, corpo={"nome": "a.pdf", "tamanhoBytes": 10}, notebook_id=nb, autenticado=False)
        self.assertEqual(status, 401)
        self.assertEqual(self.s3.chamadas, [])


class TestErros(BaseDocs):

    def test_bucket_nao_configurado_vira_500_sem_vazar_detalhe(self):
        nb = self.criar()["notebookId"]
        with patch.dict(os.environ):
            os.environ.pop("DOCS_BUCKET")
            status, _, bruto = self.pedir(nb)
        self.assertEqual(status, 500)
        self.assertNotIn("DOCS_BUCKET", bruto["body"])

    def test_erro_da_aws_vira_500_e_erro_de_sdk_vira_502(self):
        nb = self.criar()["notebookId"]
        self.s3.falhar_com = ClientError({"Error": {"Code": "AccessDenied", "Message": "arn:aws:s3:::x"}}, "Op")
        status, _, bruto = self.pedir(nb)
        self.assertEqual(status, 500)
        self.assertNotIn("arn:aws", bruto["body"])
        self.s3.falhar_com = BotoCoreError()
        self.assertEqual(self.pedir(nb)[0], 502)

    def test_log_nao_expoe_nome_do_arquivo_nem_id_completo(self):
        nb = self.criar()["notebookId"]
        with self.assertLogs(level="INFO") as logs:
            self.pedir(nb, "Prova Secreta.pdf", 10)
        saida = "\n".join(logs.output)
        self.assertNotIn("Secreta", saida)
        self.assertNotIn(USUARIO_A, saida)


class TestModelosDeArquivo(unittest.TestCase):

    def test_chave_s3(self):
        nb, doc = M.novo_id(), M.novo_id()
        self.assertEqual(M.chave_s3(USUARIO_A, nb, doc, "pdf"), f"usuarios/{USUARIO_A}/{nb}/{doc}.pdf")

    def test_chave_s3_recusa_entrada_perigosa(self):
        nb, doc = M.novo_id(), M.novo_id()
        for args in ((USUARIO_A, nb, doc, "exe"), (USUARIO_A, nb, doc, "pdf/../x"), ("a/../b", nb, doc, "pdf"),
                     (USUARIO_A, "../x", doc, "pdf"), (USUARIO_A, nb, f"{doc}/../../x", "pdf"), (USUARIO_B[:-1] + "/", nb, doc, "md")):
            with self.assertRaises(M.DadoInvalido, msg=repr(args)):
                M.chave_s3(*args)

    def test_validar_arquivo(self):
        self.assertEqual(M.validar_arquivo("  Notas.MD ", 10), ("Notas.MD", "md"))
        self.assertEqual(M.validar_arquivo("v1.final.txt", 1), ("v1.final.txt", "txt"))


if __name__ == "__main__":
    unittest.main()