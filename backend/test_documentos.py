"""
Testes dos documentos (envio, registro, confirmação, listagem e exclusão).
Sem AWS: DynamoDB e S3 são simulados (S3Falso e TabelaFalsa, em test_notebooks.py).
Rodar:  python3 -m unittest test_documentos -v
"""
import os
import unittest
from unittest.mock import patch

import modelos as M
from botocore.exceptions import BotoCoreError, ClientError
from test_notebooks import Base, USUARIO_A, USUARIO_B

UPLOAD = "POST /notebooks/{notebookId}/documentos/upload"
LISTAR = "GET /notebooks/{notebookId}/documentos"
CONFIRMAR = "POST /notebooks/{notebookId}/documentos/{documentoId}/confirmar"
EXCLUIR = "DELETE /notebooks/{notebookId}/documentos/{documentoId}"
EXCLUIR_NOTEBOOK = "DELETE /notebooks/{notebookId}"


class BaseDocs(Base):

    def chamar_doc(self, rota, nb, doc=None, usuario=USUARIO_A, corpo=None):
        evento = {"routeKey": rota, "pathParameters": {"notebookId": nb}}
        if doc is not None:
            evento["pathParameters"]["documentoId"] = doc
        if corpo is not None:
            import json
            evento["body"] = json.dumps(corpo)
        from test_notebooks import contexto_autenticado, CONTEXTO
        import lambda_function as L
        evento["requestContext"] = contexto_autenticado(usuario)
        r = L.lambda_handler(evento, CONTEXTO)
        import json as _j
        return r["statusCode"], (_j.loads(r["body"]) if r["body"] else None), r

    def pedir(self, nb, nome="apostila.pdf", tamanho=2048, usuario=USUARIO_A, **extra):
        return self.chamar(UPLOAD, usuario, {"nome": nome, "tamanhoBytes": tamanho, **extra}, nb)

    def enviar_arquivo(self, usuario=USUARIO_A, nome="apostila.pdf", tamanho=2048, nb=None):
        """Cria notebook (se preciso), pede o upload e simula o envio ao S3. Devolve (nb, doc)."""
        nb = nb or self.criar("SO", usuario)["notebookId"]
        _, corpo, _ = self.pedir(nb, nome, tamanho, usuario)
        doc = corpo["documento"]
        self.s3.enviar(self.s3.chamadas[-1]["Key"], tamanho)
        return nb, doc


class TestSolicitarUpload(BaseDocs):

    def test_registra_o_documento_como_pendente_e_devolve_o_upload(self):
        nb = self.criar("SO")["notebookId"]
        status, corpo, _ = self.pedir(nb, "  Apostila de SO.PDF ", 2048)
        self.assertEqual(status, 201)
        doc, up = corpo["documento"], corpo["upload"]
        self.assertRegex(doc["documentoId"], M.ID_DE_ITEM)
        self.assertEqual((doc["nome"], doc["tipo"], doc["tamanhoBytes"], doc["status"]), ("Apostila de SO.PDF", "pdf", 2048, "PENDENTE"))
        for interno in ("pk", "sk", "entidade", "usuarioId", "chaveS3"):
            self.assertNotIn(interno, doc)
        self.assertEqual(up["url"], "https://bucket-de-teste.s3.amazonaws.com/")
        self.assertEqual(up["campos"]["Content-Type"], "application/pdf")

        item = self.tabela.itens[(f"USER#{USUARIO_A}", f"DOC#{nb}#{doc['documentoId']}")]
        self.assertEqual(item["chaveS3"], self.s3.chamadas[0]["Key"])
        self.assertEqual(item["status"], "PENDENTE")

    def test_politica_assinada(self):
        nb = self.criar("SO")["notebookId"]
        _, corpo, _ = self.pedir(nb, "a.md", 500)
        c = self.s3.chamadas[0]
        self.assertEqual(c["Key"], f"usuarios/{USUARIO_A}/{nb}/{corpo['documento']['documentoId']}.md")
        self.assertIn({"Content-Type": "text/markdown"}, c["Conditions"])
        self.assertIn(["content-length-range", 500, 500], c["Conditions"])
        self.assertEqual(c["ExpiresIn"], 300)

    def test_tipo_de_conteudo_vem_da_extensao_e_o_nome_nao_entra_na_chave(self):
        nb = self.criar()["notebookId"]
        for nome, esperado in (("a.md", "text/markdown"), ("a.txt", "text/plain"), ("../../x#y.PDF", "application/pdf")):
            self.pedir(nb, nome, 10, contentType="text/html", chave="../x")
            self.assertEqual(self.s3.chamadas[-1]["Fields"], {"Content-Type": esperado})
            self.assertNotIn("..", self.s3.chamadas[-1]["Key"])

    def test_entrada_invalida_devolve_400_sem_s3_e_sem_registro(self):
        nb = self.criar()["notebookId"]
        casos = [("a.exe", 10), ("semextensao", 10), ("", 10), ("a.pdf", 0), ("a.pdf", True), ("a.pdf", 1.5), ("a.pdf", "10"),
                 ("a.pdf", None), ("a.pdf", M.MAX_TAMANHO_DOCUMENTO + 1), ("x" * 300 + ".pdf", 10)]
        for nome, tamanho in casos:
            with self.subTest(nome=nome[:20], tamanho=tamanho):
                self.assertEqual(self.pedir(nb, nome, tamanho)[0], 400)
        self.assertEqual(self.s3.chamadas, [])
        self.assertEqual(len(self.tabela.itens), 1)  # só o notebook

    def test_notebook_de_outro_usuario_ou_inexistente_devolve_404(self):
        nb_a = self.criar("do A", USUARIO_A)["notebookId"]
        for nid, usuario in ((nb_a, USUARIO_B), (M.novo_id(), USUARIO_A), ("abc", USUARIO_A)):
            self.assertEqual(self.pedir(nid, usuario=usuario)[0], 404)
        self.assertEqual(self.s3.chamadas, [])

    def test_a_chave_usa_o_usuario_do_token(self):
        nb = self.criar("do A", USUARIO_A)["notebookId"]
        self.pedir(nb, usuarioId=USUARIO_B, sub=USUARIO_B)
        self.assertTrue(self.s3.chamadas[-1]["Key"].startswith(f"usuarios/{USUARIO_A}/{nb}/"))
        self.assertEqual({k[0] for k in self.tabela.itens}, {f"USER#{USUARIO_A}"})

    def test_sem_autenticacao_devolve_401(self):
        nb = self.criar()["notebookId"]
        self.assertEqual(self.chamar(UPLOAD, corpo={"nome": "a.pdf", "tamanhoBytes": 10}, notebook_id=nb, autenticado=False)[0], 401)

    def test_falha_ao_assinar_nao_deixa_registro(self):
        nb = self.criar()["notebookId"]
        self.s3.falhar_com = ClientError({"Error": {"Code": "AccessDenied", "Message": "arn:aws:s3:::x"}}, "Op")
        status, _, bruto = self.pedir(nb)
        self.assertEqual(status, 500)
        self.assertNotIn("arn:aws", bruto["body"])
        self.assertEqual(len(self.tabela.itens), 1)
        self.s3.falhar_com = BotoCoreError()
        self.assertEqual(self.pedir(nb)[0], 502)

    def test_bucket_nao_configurado_vira_500_sem_vazar_detalhe(self):
        nb = self.criar()["notebookId"]
        with patch.dict(os.environ):
            os.environ.pop("DOCS_BUCKET")
            status, _, bruto = self.pedir(nb)
        self.assertEqual(status, 500)
        self.assertNotIn("DOCS_BUCKET", bruto["body"])
        self.assertEqual(len(self.tabela.itens), 1)

    def test_log_nao_expoe_nome_do_arquivo_nem_id_completo(self):
        nb = self.criar()["notebookId"]
        with self.assertLogs(level="INFO") as logs:
            self.pedir(nb, "Prova Secreta.pdf", 10)
        saida = "\n".join(logs.output)
        self.assertNotIn("Secreta", saida)
        self.assertNotIn(USUARIO_A, saida)


class TestListar(BaseDocs):

    def test_lista_vazia_e_em_ordem_de_criacao(self):
        nb = self.criar("SO")["notebookId"]
        self.assertEqual(self.chamar_doc(LISTAR, nb)[:2], (200, {"documentos": []}))
        nomes = ["a.pdf", "b.md", "c.txt"]
        for n in nomes:
            self.pedir(nb, n, 10)
        _, corpo, _ = self.chamar_doc(LISTAR, nb)
        self.assertEqual([d["nome"] for d in corpo["documentos"]], nomes)
        self.assertEqual({d["status"] for d in corpo["documentos"]}, {"PENDENTE"})
        self.assertNotIn(USUARIO_A, str(corpo))

    def test_numeros_lidos_do_dynamodb_viram_json(self):
        # o boto3 devolve Decimal; a resposta precisa sair com número comum
        nb = self.criar("SO")["notebookId"]
        self.pedir(nb, "a.pdf", 2048)
        status, corpo, bruto = self.chamar_doc(LISTAR, nb)
        self.assertEqual(status, 200)
        self.assertIn('"tamanhoBytes": 2048,', bruto["body"])
        self.assertIsInstance(corpo["documentos"][0]["tamanhoBytes"], int)

    def test_so_lista_os_documentos_do_notebook(self):
        nb1 = self.criar("um")["notebookId"]
        nb2 = self.criar("dois")["notebookId"]
        self.pedir(nb1, "do-um.pdf", 10)
        self.pedir(nb2, "do-dois.pdf", 10)
        _, corpo, _ = self.chamar_doc(LISTAR, nb2)
        self.assertEqual([d["nome"] for d in corpo["documentos"]], ["do-dois.pdf"])

    def test_notebook_de_outro_usuario_ou_inexistente_devolve_404(self):
        nb_a, _ = self.enviar_arquivo(USUARIO_A)
        self.assertEqual(self.chamar_doc(LISTAR, nb_a, usuario=USUARIO_B)[:2], (404, {"erro": "Notebook não encontrado."}))
        self.assertEqual(self.chamar_doc(LISTAR, M.novo_id())[0], 404)

    def test_percorre_todas_as_paginas(self):
        self.tabela.tamanho_pagina = 2
        nb = self.criar()["notebookId"]
        for i in range(5):
            self.pedir(nb, f"d{i}.pdf", 10)
        _, corpo, _ = self.chamar_doc(LISTAR, nb)
        self.assertEqual(len(corpo["documentos"]), 5)


class TestConfirmar(BaseDocs):

    def test_sem_arquivo_no_s3_devolve_409_e_continua_pendente(self):
        nb = self.criar()["notebookId"]
        doc = self.pedir(nb)[1]["documento"]
        status, corpo, _ = self.chamar_doc(CONFIRMAR, nb, doc["documentoId"])
        self.assertEqual(status, 409)
        self.assertIn("ainda não foi enviado", corpo["erro"])
        self.assertEqual(self.chamar_doc(LISTAR, nb)[1]["documentos"][0]["status"], "PENDENTE")

    def test_com_arquivo_no_s3_passa_para_enviado(self):
        nb, doc = self.enviar_arquivo()
        status, corpo, _ = self.chamar_doc(CONFIRMAR, nb, doc["documentoId"])
        self.assertEqual(status, 200)
        self.assertEqual(corpo["documento"]["status"], "ENVIADO")
        self.assertEqual(corpo["documento"]["nome"], "apostila.pdf")
        self.assertEqual(corpo["documento"]["tamanhoBytes"], 2048)
        self.assertNotIn("chaveS3", str(corpo))
        self.assertEqual(self.chamar_doc(LISTAR, nb)[1]["documentos"][0]["status"], "ENVIADO")

    def test_confirmar_de_novo_e_idempotente_e_nao_consulta_o_s3(self):
        nb, doc = self.enviar_arquivo()
        self.chamar_doc(CONFIRMAR, nb, doc["documentoId"])
        self.s3.falhar_com = ClientError({"Error": {"Code": "500", "Message": "x"}}, "HeadObject")
        status, corpo, _ = self.chamar_doc(CONFIRMAR, nb, doc["documentoId"])
        self.assertEqual((status, corpo["documento"]["status"]), (200, "ENVIADO"))

    def test_documento_de_outro_usuario_inexistente_ou_de_outro_notebook_devolve_404(self):
        nb_a, doc_a = self.enviar_arquivo(USUARIO_A)
        outro_nb_a = self.criar("outro", USUARIO_A)["notebookId"]
        nb_b = self.criar("do B", USUARIO_B)["notebookId"]
        casos = [(nb_a, doc_a["documentoId"], USUARIO_B), (nb_b, doc_a["documentoId"], USUARIO_B),
                 (outro_nb_a, doc_a["documentoId"], USUARIO_A), (nb_a, M.novo_id(), USUARIO_A), (nb_a, "abc", USUARIO_A)]
        for nb, doc, usuario in casos:
            with self.subTest(nb=nb[:4], doc=doc[:4], usuario=usuario[:4]):
                self.assertEqual(self.chamar_doc(CONFIRMAR, nb, doc, usuario)[0], 404)
        self.assertEqual(self.tabela.itens[(f"USER#{USUARIO_A}", f"DOC#{nb_a}#{doc_a['documentoId']}")]["status"], "PENDENTE")

    def test_erro_real_do_s3_vira_500_e_nao_confirma(self):
        nb, doc = self.enviar_arquivo()
        self.s3.falhar_com = ClientError({"Error": {"Code": "AccessDenied", "Message": "arn:aws:s3:::x"}}, "HeadObject")
        status, _, bruto = self.chamar_doc(CONFIRMAR, nb, doc["documentoId"])
        self.assertEqual(status, 500)
        self.assertNotIn("arn:aws", bruto["body"])
        self.assertEqual(self.chamar_doc(LISTAR, nb)[1]["documentos"][0]["status"], "PENDENTE")


class TestExcluirDocumento(BaseDocs):

    def test_apaga_o_arquivo_e_o_registro(self):
        nb, doc = self.enviar_arquivo()
        chave = f"usuarios/{USUARIO_A}/{nb}/{doc['documentoId']}.pdf"
        self.assertIn(chave, self.s3.objetos)
        status, corpo, resp = self.chamar_doc(EXCLUIR, nb, doc["documentoId"])
        self.assertEqual((status, corpo, resp["body"]), (204, None, ""))
        self.assertNotIn(chave, self.s3.objetos)
        self.assertEqual(self.chamar_doc(LISTAR, nb)[1], {"documentos": []})

    def test_segunda_exclusao_devolve_404(self):
        nb, doc = self.enviar_arquivo()
        self.chamar_doc(EXCLUIR, nb, doc["documentoId"])
        status, corpo, _ = self.chamar_doc(EXCLUIR, nb, doc["documentoId"])
        self.assertEqual((status, corpo), (404, {"erro": "Documento não encontrado."}))

    def test_outro_usuario_nao_apaga_nada(self):
        nb, doc = self.enviar_arquivo(USUARIO_A)
        nb_b = self.criar("do B", USUARIO_B)["notebookId"]
        antes_s3, antes_db = dict(self.s3.objetos), dict(self.tabela.itens)
        for nid in (nb, nb_b):
            self.assertEqual(self.chamar_doc(EXCLUIR, nid, doc["documentoId"], USUARIO_B)[0], 404)
        self.assertEqual(self.s3.objetos, antes_s3)
        self.assertEqual(self.tabela.itens, antes_db)

    def test_falha_no_s3_mantem_o_registro_para_nova_tentativa(self):
        nb, doc = self.enviar_arquivo()
        self.s3.falhar_com = ClientError({"Error": {"Code": "InternalError", "Message": "x"}}, "DeleteObject")
        self.assertEqual(self.chamar_doc(EXCLUIR, nb, doc["documentoId"])[0], 500)
        self.assertEqual(len(self.chamar_doc(LISTAR, nb)[1]["documentos"]), 1)
        self.assertEqual(self.chamar_doc(EXCLUIR, nb, doc["documentoId"])[0], 204)

    def test_registro_com_chave_fora_da_pasta_do_usuario_nao_apaga_nada(self):
        nb, doc = self.enviar_arquivo(USUARIO_A)
        self.tabela.itens[(f"USER#{USUARIO_A}", f"DOC#{nb}#{doc['documentoId']}")]["chaveS3"] = f"usuarios/{USUARIO_B}/x/y.pdf"
        self.s3.enviar(f"usuarios/{USUARIO_B}/x/y.pdf")
        self.assertEqual(self.chamar_doc(EXCLUIR, nb, doc["documentoId"])[0], 500)
        self.assertIn(f"usuarios/{USUARIO_B}/x/y.pdf", self.s3.objetos)


class TestExcluirNotebookComDocumentos(BaseDocs):

    def test_cascata_apaga_os_arquivos_do_s3_e_so_os_do_notebook(self):
        nb1, _ = self.enviar_arquivo(USUARIO_A, "a.pdf")
        self.enviar_arquivo(USUARIO_A, "b.md", nb=nb1)
        nb2, _ = self.enviar_arquivo(USUARIO_A, "c.pdf")
        nb_b, _ = self.enviar_arquivo(USUARIO_B, "d.pdf")
        self.assertEqual(len(self.s3.objetos), 4)

        self.assertEqual(self.chamar_doc(EXCLUIR_NOTEBOOK, nb1)[0], 204)
        restantes = list(self.s3.objetos)
        self.assertEqual(len(restantes), 2)
        self.assertTrue(all(f"/{nb1}/" not in k for k in restantes))
        self.assertEqual(len(self.chamar_doc(LISTAR, nb2)[1]["documentos"]), 1)
        self.assertEqual(len(self.chamar_doc(LISTAR, nb_b, usuario=USUARIO_B)[1]["documentos"]), 1)

    def test_falha_no_s3_mantem_notebook_e_registros(self):
        nb, _ = self.enviar_arquivo()
        self.s3.falhar_com = ClientError({"Error": {"Code": "InternalError", "Message": "x"}}, "DeleteObjects")
        self.assertEqual(self.chamar_doc(EXCLUIR_NOTEBOOK, nb)[0], 500)
        self.assertEqual(self.chamar(  "GET /notebooks/{notebookId}", notebook_id=nb)[0], 200)
        self.assertEqual(len(self.chamar_doc(LISTAR, nb)[1]["documentos"]), 1)
        self.assertEqual(self.chamar_doc(EXCLUIR_NOTEBOOK, nb)[0], 204)
        self.assertEqual(self.s3.objetos, {})

    def test_notebook_sem_documentos_nao_depende_do_s3(self):
        nb = self.criar()["notebookId"]
        with patch.dict(os.environ):
            os.environ.pop("DOCS_BUCKET")
            self.assertEqual(self.chamar_doc(EXCLUIR_NOTEBOOK, nb)[0], 204)

    def test_outro_usuario_nao_apaga_o_notebook_nem_os_arquivos(self):
        nb, _ = self.enviar_arquivo(USUARIO_A)
        self.assertEqual(self.chamar_doc(EXCLUIR_NOTEBOOK, nb, usuario=USUARIO_B)[0], 404)
        self.assertEqual(len(self.s3.objetos), 1)

    def test_apaga_em_lotes_de_ate_1000(self):
        import documentos as D
        nb = self.criar()["notebookId"]
        for i in range(5):
            self.pedir(nb, f"d{i}.pdf", 10)
        lotes = []
        original = self.s3.delete_objects
        self.s3.delete_objects = lambda Bucket, Delete: (lotes.append(len(Delete["Objects"])), original(Bucket, Delete))[1]
        with patch.object(D, "LOTE_EXCLUSAO_S3", 2):
            self.assertEqual(self.chamar_doc(EXCLUIR_NOTEBOOK, nb)[0], 204)
        self.assertEqual(lotes, [2, 2, 1])

    def test_erros_do_s3_na_resposta_do_lote_abortam_a_exclusao(self):
        nb, _ = self.enviar_arquivo()
        self.s3.delete_objects = lambda Bucket, Delete: {"Errors": [{"Key": "x", "Code": "AccessDenied"}]}
        self.assertEqual(self.chamar_doc(EXCLUIR_NOTEBOOK, nb)[0], 500)
        self.assertEqual(self.chamar(  "GET /notebooks/{notebookId}", notebook_id=nb)[0], 200)


class TestModelosDeArquivo(unittest.TestCase):

    def test_chave_s3(self):
        nb, doc = M.novo_id(), M.novo_id()
        self.assertEqual(M.chave_s3(USUARIO_A, nb, doc, "pdf"), f"usuarios/{USUARIO_A}/{nb}/{doc}.pdf")

    def test_chave_s3_recusa_entrada_perigosa(self):
        nb, doc = M.novo_id(), M.novo_id()
        for args in ((USUARIO_A, nb, doc, "exe"), (USUARIO_A, nb, doc, "pdf/../x"), ("a/../b", nb, doc, "pdf"),
                     (USUARIO_A, "../x", doc, "pdf"), (USUARIO_A, nb, f"{doc}/../../x", "pdf")):
            with self.assertRaises(M.DadoInvalido, msg=repr(args)):
                M.chave_s3(*args)

    def test_novo_documento_aceita_id_externo(self):
        nb, doc = M.novo_id(), M.novo_id()
        item = M.novo_documento(USUARIO_A, nb, "a.pdf", 10, "k", documento_id=doc)
        self.assertEqual((item["documentoId"], item["sk"]), (doc, f"DOC#{nb}#{doc}"))
        with self.assertRaises(M.DadoInvalido):
            M.novo_documento(USUARIO_A, nb, "a.pdf", 10, "k", documento_id="x#y")


if __name__ == "__main__":
    unittest.main()