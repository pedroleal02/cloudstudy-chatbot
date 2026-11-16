"""
Testes do modelo de dados (chaves, validação e itens). Nenhuma chamada à AWS.
Rodar:  python3 -m unittest test_modelos -v
"""
import unittest
from unittest.mock import patch

import modelos as M

USUARIO_A = "11111111-aaaa-4aaa-8aaa-111111111111"
USUARIO_B = "22222222-bbbb-4bbb-8bbb-222222222222"


def tipos_do_dynamodb(valor):
    """True se o valor só usa tipos que o boto3 grava sem erro (float é recusado)."""
    if valor is None or isinstance(valor, (str, int, bool)):
        return True
    if isinstance(valor, list):
        return all(tipos_do_dynamodb(v) for v in valor)
    if isinstance(valor, dict):
        return all(isinstance(k, str) and tipos_do_dynamodb(v) for k, v in valor.items())
    return False


class TestIdentificadores(unittest.TestCase):

    def test_formato_ulid(self):
        i = M.novo_id()
        self.assertEqual(len(i), 26)
        self.assertRegex(i, M.ID_DE_ITEM)

    def test_ids_unicos(self):
        ids = {M.novo_id() for _ in range(5000)}
        self.assertEqual(len(ids), 5000)

    def test_ordem_crescente_no_mesmo_milissegundo(self):
        with patch.object(M.time, "time", return_value=1_800_000_000.123):
            ids = [M.novo_id() for _ in range(200)]
        self.assertEqual(ids, sorted(ids))
        self.assertEqual(len(set(ids)), 200)

    def test_ordem_crescente_entre_milissegundos(self):
        with patch.object(M.time, "time", return_value=1_800_000_100.000):
            a = M.novo_id()
        with patch.object(M.time, "time", return_value=1_800_000_100.001):
            b = M.novo_id()
        self.assertLess(a, b)

    def test_relogio_que_volta_nao_quebra_a_ordem(self):
        with patch.object(M.time, "time", return_value=1_800_000_200.500):
            a = M.novo_id()
        with patch.object(M.time, "time", return_value=1_800_000_200.100):
            b = M.novo_id()
        self.assertLess(a, b)

    def test_estouro_da_parte_aleatoria_avanca_o_milissegundo(self):
        with patch.object(M.time, "time", return_value=1_800_000_300.000):
            M.novo_id()
            M._ultimo_aleatorio = (1 << 80) - 1
            a = M.novo_id()
            b = M.novo_id()
        self.assertRegex(a, M.ID_DE_ITEM)
        self.assertLess(a, b)

    def test_data_iso_ordena_como_texto(self):
        a = M.agora_iso()
        self.assertRegex(a, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")


class TestChaves(unittest.TestCase):

    def setUp(self):
        self.nb = M.novo_id()
        self.conv = M.novo_id()
        self.msg = M.novo_id()
        self.doc = M.novo_id()

    def test_formato_das_chaves(self):
        self.assertEqual(M.pk_usuario(USUARIO_A), f"USER#{USUARIO_A}")
        self.assertEqual(M.sk_notebook(self.nb), f"NB#{self.nb}")
        self.assertEqual(M.sk_documento(self.nb, self.doc), f"DOC#{self.nb}#{self.doc}")
        self.assertEqual(M.sk_conversa(self.nb, self.conv), f"CONV#{self.nb}#{self.conv}")
        self.assertEqual(
            M.sk_mensagem(self.nb, self.conv, self.msg), f"MSG#{self.nb}#{self.conv}#{self.msg}"
        )

    def test_cada_sort_key_cai_so_no_prefixo_da_sua_entidade(self):
        sks = {
            "NB": M.sk_notebook(self.nb),
            "DOC": M.sk_documento(self.nb, self.doc),
            "CONV": M.sk_conversa(self.nb, self.conv),
            "MSG": M.sk_mensagem(self.nb, self.conv, self.msg),
        }
        prefixos = {
            "NB": M.PREFIXO_NOTEBOOKS,
            "DOC": M.prefixo_documentos(self.nb),
            "CONV": M.prefixo_conversas(self.nb),
            "MSG": M.prefixo_mensagens(self.nb, self.conv),
        }
        for entidade, sk in sks.items():
            for outra, prefixo in prefixos.items():
                self.assertEqual(sk.startswith(prefixo), entidade == outra, f"{entidade} x {outra}")

    def test_prefixos_nao_alcancam_outro_notebook_nem_outra_conversa(self):
        outro_nb = M.novo_id()
        outra_conv = M.novo_id()
        self.assertFalse(M.sk_documento(outro_nb, self.doc).startswith(M.prefixo_documentos(self.nb)))
        self.assertFalse(M.sk_conversa(outro_nb, self.conv).startswith(M.prefixo_conversas(self.nb)))
        self.assertFalse(
            M.sk_mensagem(self.nb, outra_conv, self.msg).startswith(M.prefixo_mensagens(self.nb, self.conv))
        )

    def test_mensagens_do_notebook_cobrem_todas_as_conversas(self):
        outra_conv = M.novo_id()
        prefixo = M.prefixo_mensagens_notebook(self.nb)
        self.assertTrue(M.sk_mensagem(self.nb, self.conv, self.msg).startswith(prefixo))
        self.assertTrue(M.sk_mensagem(self.nb, outra_conv, self.msg).startswith(prefixo))
        self.assertFalse(M.sk_mensagem(M.novo_id(), self.conv, self.msg).startswith(prefixo))

    def test_usuarios_diferentes_tem_particoes_diferentes(self):
        self.assertNotEqual(M.pk_usuario(USUARIO_A), M.pk_usuario(USUARIO_B))

    def test_usuario_invalido_e_recusado(self):
        for ruim in (None, "", "curto", "x" * 65, "abc#defgh123", "abcdefgh 1234", 123, "../../etc/passwd"):
            with self.assertRaises(M.DadoInvalido, msg=repr(ruim)):
                M.pk_usuario(ruim)

    def test_id_invalido_e_recusado_antes_de_montar_a_chave(self):
        injecao = f"{self.nb}#MSG#{self.conv}"
        for ruim in (None, "", "abc", self.nb.lower(), self.nb + "A", self.nb[:-1] + "#", injecao, 42):
            with self.assertRaises(M.DadoInvalido, msg=repr(ruim)):
                M.sk_notebook(ruim)
            with self.assertRaises(M.DadoInvalido, msg=repr(ruim)):
                M.prefixo_documentos(ruim)
            with self.assertRaises(M.DadoInvalido, msg=repr(ruim)):
                M.sk_mensagem(self.nb, ruim, self.msg)


class TestNotebook(unittest.TestCase):

    def test_item_completo(self):
        item = M.novo_notebook(USUARIO_A, "  Inteligência Artificial  ", "Aulas de IA")
        self.assertEqual(item["pk"], f"USER#{USUARIO_A}")
        self.assertEqual(item["sk"], f"NB#{item['notebookId']}")
        self.assertEqual(item["entidade"], "NOTEBOOK")
        self.assertEqual(item["nome"], "Inteligência Artificial")
        self.assertEqual(item["descricao"], "Aulas de IA")
        self.assertEqual(item["criadoEm"], item["atualizadoEm"])
        self.assertTrue(tipos_do_dynamodb(item))

    def test_descricao_e_opcional(self):
        self.assertEqual(M.novo_notebook(USUARIO_A, "SO")["descricao"], "")

    def test_nome_obrigatorio_e_limitado(self):
        for ruim in (None, "", "   ", "\x00\x01", 5):
            with self.assertRaises(M.DadoInvalido, msg=repr(ruim)):
                M.novo_notebook(USUARIO_A, ruim)
        with self.assertRaises(M.DadoInvalido):
            M.novo_notebook(USUARIO_A, "x" * (M.MAX_NOME_NOTEBOOK + 1))
        M.novo_notebook(USUARIO_A, "x" * M.MAX_NOME_NOTEBOOK)

    def test_caracteres_de_controle_sao_removidos(self):
        self.assertEqual(M.novo_notebook(USUARIO_A, "Cripto\x00grafia\x07")["nome"], "Criptografia")

    def test_usuario_invalido_nao_gera_item(self):
        with self.assertRaises(M.DadoInvalido):
            M.novo_notebook("nao#valido", "Nome")


class TestAtualizacaoNotebook(unittest.TestCase):

    def test_so_os_campos_editaveis_e_limpos(self):
        corpo = {"nome": "  Novo nome ", "descricao": "Nova", "usuarioId": USUARIO_B, "pk": "USER#x", "criadoEm": "2000"}
        self.assertEqual(M.atualizacao_notebook(corpo), {"nome": "Novo nome", "descricao": "Nova"})

    def test_aceita_so_um_campo(self):
        self.assertEqual(M.atualizacao_notebook({"nome": "SO"}), {"nome": "SO"})
        self.assertEqual(M.atualizacao_notebook({"descricao": ""}), {"descricao": ""})
        self.assertEqual(M.atualizacao_notebook({"descricao": None}), {"descricao": ""})

    def test_sem_campo_editavel_e_recusado(self):
        for ruim in ({}, {"usuarioId": USUARIO_B}, None, [], "nome", 5):
            with self.assertRaises(M.DadoInvalido, msg=repr(ruim)):
                M.atualizacao_notebook(ruim)

    def test_nome_nao_pode_ficar_vazio_nem_longo(self):
        for ruim in ("", "   ", None, 5, "x" * (M.MAX_NOME_NOTEBOOK + 1)):
            with self.assertRaises(M.DadoInvalido, msg=repr(ruim)):
                M.atualizacao_notebook({"nome": ruim})

    def test_descricao_longa_demais(self):
        with self.assertRaises(M.DadoInvalido):
            M.atualizacao_notebook({"descricao": "x" * (M.MAX_DESCRICAO_NOTEBOOK + 1)})


class TestDocumento(unittest.TestCase):

    def setUp(self):
        self.nb = M.novo_id()

    def criar(self, nome="Apostila.PDF", tamanho=1024, chave="usuarios/x/y.pdf"):
        return M.novo_documento(USUARIO_A, self.nb, nome, tamanho, chave)

    def test_item_completo(self):
        item = self.criar()
        self.assertEqual(item["sk"], f"DOC#{self.nb}#{item['documentoId']}")
        self.assertEqual(item["entidade"], "DOCUMENTO")
        self.assertEqual(item["tipo"], "pdf")
        self.assertEqual(item["status"], "PENDENTE")
        self.assertIn(item["status"], M.STATUS_DOCUMENTO)
        self.assertTrue(tipos_do_dynamodb(item))

    def test_formatos_aceitos(self):
        for nome, tipo in (("a.md", "md"), ("a.txt", "txt"), ("A.PDF", "pdf"), ("v1.final.md", "md")):
            self.assertEqual(self.criar(nome=nome)["tipo"], tipo)

    def test_formatos_recusados(self):
        for nome in ("a.exe", "a.docx", "semextensao", "pdf", ".pdf.exe", "a."):
            with self.assertRaises(M.DadoInvalido, msg=nome):
                self.criar(nome=nome)

    def test_tamanho(self):
        for ruim in (0, -1, True, 1.5, "10", None, M.MAX_TAMANHO_DOCUMENTO + 1):
            with self.assertRaises(M.DadoInvalido, msg=repr(ruim)):
                self.criar(tamanho=ruim)
        self.criar(tamanho=M.MAX_TAMANHO_DOCUMENTO)

    def test_chave_s3_obrigatoria(self):
        with self.assertRaises(M.DadoInvalido):
            self.criar(chave="")

    def test_notebook_invalido(self):
        with self.assertRaises(M.DadoInvalido):
            M.novo_documento(USUARIO_A, "nb#1", "a.md", 10, "k")


class TestConversaEMensagem(unittest.TestCase):

    def setUp(self):
        self.nb = M.novo_id()

    def test_conversa_com_titulo_padrao(self):
        item = M.nova_conversa(USUARIO_A, self.nb)
        self.assertEqual(item["titulo"], M.TITULO_PADRAO_CONVERSA)
        self.assertEqual(item["sk"], f"CONV#{self.nb}#{item['conversaId']}")
        self.assertTrue(tipos_do_dynamodb(item))

    def test_conversa_com_titulo(self):
        self.assertEqual(M.nova_conversa(USUARIO_A, self.nb, "  Árvores AVL ")["titulo"], "Árvores AVL")

    def test_titulo_longo_demais(self):
        with self.assertRaises(M.DadoInvalido):
            M.nova_conversa(USUARIO_A, self.nb, "x" * (M.MAX_TITULO_CONVERSA + 1))

    def test_mensagem_do_usuario(self):
        conv = M.novo_id()
        item = M.nova_mensagem(USUARIO_A, self.nb, conv, "usuario", "O que é uma árvore binária?")
        self.assertEqual(item["sk"], f"MSG#{self.nb}#{conv}#{item['mensagemId']}")
        self.assertEqual(item["referencias"], [])
        self.assertTrue(tipos_do_dynamodb(item))

    def test_mensagem_do_assistente_guarda_referencias(self):
        refs = [{"id": 1, "arquivo": "arvores.md", "trecho": "Uma árvore binária..."}]
        item = M.nova_mensagem(USUARIO_A, self.nb, M.novo_id(), "assistente", "Resposta [1].", refs)
        self.assertEqual(item["referencias"], refs)
        self.assertTrue(tipos_do_dynamodb(item))

    def test_mensagem_preserva_quebras_de_linha(self):
        item = M.nova_mensagem(USUARIO_A, self.nb, M.novo_id(), "assistente", "linha 1\n\nlinha 2")
        self.assertEqual(item["conteudo"], "linha 1\n\nlinha 2")

    def test_mensagem_invalida(self):
        conv = M.novo_id()
        with self.assertRaises(M.DadoInvalido):
            M.nova_mensagem(USUARIO_A, self.nb, conv, "sistema", "oi")
        with self.assertRaises(M.DadoInvalido):
            M.nova_mensagem(USUARIO_A, self.nb, conv, "usuario", "   ")
        with self.assertRaises(M.DadoInvalido):
            M.nova_mensagem(USUARIO_A, self.nb, conv, "usuario", "x" * (M.MAX_CONTEUDO_MENSAGEM + 1))
        with self.assertRaises(M.DadoInvalido):
            M.nova_mensagem(USUARIO_A, self.nb, "conv#1", "usuario", "oi")

    def test_referencias_invalidas(self):
        conv = M.novo_id()
        for ruim in ("texto", {"id": 1}, [{"id": "1"}], [{"id": True}], ["x"], [{"id": 1}] * (M.MAX_REFERENCIAS + 1)):
            with self.assertRaises(M.DadoInvalido, msg=repr(ruim)):
                M.nova_mensagem(USUARIO_A, self.nb, conv, "assistente", "oi", ruim)

    def test_mensagens_gravadas_em_sequencia_ficam_em_ordem(self):
        conv = M.novo_id()
        itens = [
            M.nova_mensagem(USUARIO_A, self.nb, conv, "usuario" if i % 2 == 0 else "assistente", f"m{i}")
            for i in range(300)
        ]
        sks = [i["sk"] for i in itens]
        self.assertEqual(sks, sorted(sks))


class TestSaida(unittest.TestCase):

    def test_para_api_remove_atributos_internos(self):
        item = M.novo_documento(USUARIO_A, M.novo_id(), "a.md", 10, "usuarios/segredo/a.md")
        publico = M.para_api(item)
        for interno in ("pk", "sk", "entidade", "usuarioId", "chaveS3"):
            self.assertNotIn(interno, publico)
        self.assertNotIn(USUARIO_A, str(publico))
        self.assertEqual(publico["documentoId"], item["documentoId"])
        self.assertIn("pk", item)  # não altera o original

    def test_pertence_ao_usuario(self):
        item = M.novo_notebook(USUARIO_A, "IA")
        self.assertTrue(M.pertence_ao_usuario(item, USUARIO_A))
        self.assertFalse(M.pertence_ao_usuario(item, USUARIO_B))
        self.assertFalse(M.pertence_ao_usuario(None, USUARIO_A))
        self.assertFalse(M.pertence_ao_usuario({}, USUARIO_A))


if __name__ == "__main__":
    unittest.main()