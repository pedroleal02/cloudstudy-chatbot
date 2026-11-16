"""
Testes do modelo de dados. Nenhuma chamada à AWS (sem custo) e nenhuma dependência:
usa só a biblioteca padrão do Python.
Rodar:  python3 -m unittest test_data_model -v
"""
import json
import unittest
from datetime import datetime, timezone

import data_model as D

ANA = "11111111-1111-4111-8111-111111111111"
BIA = "22222222-2222-4222-8222-222222222222"
AGORA = datetime(2026, 11, 16, 12, 0, 0, 123000, tzinfo=timezone.utc)


class TabelaFalsa:
    """Interpreta list_params() sobre uma lista de itens, como o DynamoDB faria."""

    def __init__(self):
        self.itens = {}

    def put(self, item):
        self.itens[(item["PK"], item["SK"])] = item

    def query(self, params):
        v = params["ExpressionAttributeValues"]
        achados = [i for (p, s), i in self.itens.items() if p == v[":pk"] and s.startswith(v[":sk"])]
        achados.sort(key=lambda i: i["SK"], reverse=not params["ScanIndexForward"])
        return achados[: params.get("Limit", len(achados))]


def sem_float(valor):
    if isinstance(valor, float):
        return False
    if isinstance(valor, dict):
        return all(sem_float(v) for v in valor.values())
    if isinstance(valor, list):
        return all(sem_float(v) for v in valor)
    return True


class TestIds(unittest.TestCase):
    def test_formato_e_unicidade(self):
        ids = {D.new_id() for _ in range(2000)}
        self.assertEqual(len(ids), 2000)
        self.assertTrue(all(len(i) == 26 for i in ids))
        self.assertTrue(all(D._ID_RE.match(i) for i in ids))

    def test_ordem_entre_milissegundos(self):
        a, b = D.new_id(now_ms=5_000_000_000_000), D.new_id(now_ms=5_000_000_000_001)
        self.assertLess(a, b)

    def test_ordem_dentro_do_mesmo_milissegundo(self):
        base = 6_000_000_000_000
        ids = [D.new_id(now_ms=base) for _ in range(1000)]
        self.assertEqual(ids, sorted(ids))
        self.assertEqual(len(set(ids)), 1000)

    def test_relogio_que_volta_nao_quebra_a_ordem(self):
        a = D.new_id(now_ms=7_000_000_000_010)
        b = D.new_id(now_ms=7_000_000_000_000)  # relógio voltou
        self.assertLess(a, b)

    def test_codificacao(self):
        self.assertEqual(D._encode(0, 10), "0" * 10)
        self.assertEqual(D._encode(31, 1), "Z")
        self.assertEqual(D._encode(32, 2), "10")

    def test_ids_invalidos_sao_recusados(self):
        for ruim in ["", None, 123, "a" * 26, "0" * 25, "0" * 27, "0" * 25 + "#", "0" * 24 + "I!", "0" * 25 + "U", "0" * 26 + "\n"]:
            with self.assertRaises(D.ValidationError, msg=repr(ruim)):
                D.notebook_sk(ruim)


class TestChaves(unittest.TestCase):
    def setUp(self):
        self.nb, self.nb2 = D.new_id(), D.new_id()
        self.c, self.c2 = D.new_id(), D.new_id()
        self.d, self.m = D.new_id(), D.new_id()

    def test_formato_das_chaves(self):
        self.assertEqual(D.pk(ANA), f"USER#{ANA}")
        self.assertEqual(D.notebook_sk(self.nb), f"NB#{self.nb}")
        self.assertEqual(D.document_sk(self.nb, self.d), f"DOC#{self.nb}#{self.d}")
        self.assertEqual(D.conversation_sk(self.nb, self.c), f"CONV#{self.nb}#{self.c}")
        self.assertEqual(D.message_sk(self.nb, self.c, self.m), f"MSG#{self.nb}#{self.c}#{self.m}")

    def test_userid_invalido_ou_com_injecao(self):
        maiusculo = "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA"
        for ruim in ["", None, "abc", maiusculo, ANA + "#NB#x", "USER#" + ANA, ANA[:-1], f"{ANA} ", ANA + "\n"]:
            with self.assertRaises(D.ValidationError, msg=repr(ruim)):
                D.pk(ruim)

    def test_prefixo_de_notebooks_nao_pega_filhos(self):
        # propriedade central do desenho: listar notebooks nunca devolve docs/conversas/mensagens
        filhos = [D.document_sk(self.nb, self.d), D.conversation_sk(self.nb, self.c), D.message_sk(self.nb, self.c, self.m)]
        for sk in filhos:
            self.assertFalse(sk.startswith(D.NOTEBOOK_PREFIX), sk)

    def test_prefixos_nao_vazam_entre_notebooks_nem_conversas(self):
        self.assertFalse(D.document_sk(self.nb2, self.d).startswith(D.documents_prefix(self.nb)))
        self.assertFalse(D.conversation_sk(self.nb2, self.c).startswith(D.conversations_prefix(self.nb)))
        self.assertFalse(D.message_sk(self.nb, self.c2, self.m).startswith(D.messages_prefix(self.nb, self.c)))
        self.assertFalse(D.message_sk(self.nb2, self.c, self.m).startswith(D.messages_prefix(self.nb, self.c)))

    def test_prefixos_de_exclusao_em_cascata_cobrem_os_filhos(self):
        prefixos = D.notebook_children_prefixes(self.nb)
        for sk in (D.document_sk(self.nb, self.d), D.conversation_sk(self.nb, self.c), D.message_sk(self.nb, self.c, self.m)):
            self.assertEqual(sum(sk.startswith(p) for p in prefixos), 1, sk)
        self.assertFalse(any(D.notebook_sk(self.nb).startswith(p) for p in prefixos))

    def test_item_key(self):
        self.assertEqual(D.item_key(ANA, D.notebook_sk(self.nb)), {"PK": f"USER#{ANA}", "SK": f"NB#{self.nb}"})


class TestListParams(unittest.TestCase):
    def test_parametros_basicos(self):
        p = D.list_params(ANA, D.NOTEBOOK_PREFIX)
        self.assertEqual(p["KeyConditionExpression"], "PK = :pk AND begins_with(SK, :sk)")
        self.assertEqual(p["ExpressionAttributeValues"], {":pk": f"USER#{ANA}", ":sk": "NB#"})
        self.assertTrue(p["ScanIndexForward"])
        self.assertNotIn("Limit", p)
        self.assertNotIn("ConsistentRead", p)

    def test_opcoes(self):
        p = D.list_params(ANA, "MSG#x#", newest_first=True, limit=10, consistent=True)
        self.assertFalse(p["ScanIndexForward"])
        self.assertEqual(p["Limit"], 10)
        self.assertTrue(p["ConsistentRead"])

    def test_entradas_invalidas(self):
        for ruim in ("", None, 5):
            with self.assertRaises(D.ValidationError):
                D.list_params(ANA, ruim)
        for limite in (0, -1, True, "5", 2.5):
            with self.assertRaises(D.ValidationError, msg=repr(limite)):
                D.list_params(ANA, "NB#", limit=limite)
        with self.assertRaises(D.ValidationError):
            D.list_params("nao-e-uuid", "NB#")


class TestItens(unittest.TestCase):
    def test_notebook(self):
        n = D.new_notebook(ANA, "  Sistemas Operacionais  ", "  turma 2026 ", now=AGORA)
        self.assertEqual(n["name"], "Sistemas Operacionais")
        self.assertEqual(n["description"], "turma 2026")
        self.assertEqual(n["PK"], f"USER#{ANA}")
        self.assertEqual(n["SK"], f"NB#{n['notebookId']}")
        self.assertEqual(n["ownerId"], ANA)
        self.assertEqual(n["entityType"], "NOTEBOOK")
        self.assertEqual(n["createdAt"], "2026-11-16T12:00:00.123Z")
        self.assertEqual(n["createdAt"], n["updatedAt"])

    def test_notebook_validacao(self):
        for nome in ("", "   ", None, 5, "x" * (D.NAME_MAX + 1)):
            with self.assertRaises(D.ValidationError, msg=repr(nome)):
                D.new_notebook(ANA, nome)
        with self.assertRaises(D.ValidationError):
            D.new_notebook(ANA, "ok", "d" * (D.DESCRIPTION_MAX + 1))
        D.new_notebook(ANA, "x" * D.NAME_MAX)  # no limite, vale

    def test_documento(self):
        nb = D.new_id()
        d = D.new_document(ANA, nb, "apostila.pdf", "application/pdf", 1024, "users/x/apostila.pdf", now=AGORA)
        self.assertEqual(d["entityType"], "DOCUMENT")
        self.assertEqual(d["status"], "PENDING_UPLOAD")
        self.assertEqual(d["SK"], f"DOC#{nb}#{d['documentId']}")
        self.assertEqual(d["notebookId"], nb)

    def test_documento_validacao(self):
        nb = D.new_id()
        base = (ANA, nb, "a.pdf", "application/pdf", 10, "k")
        for tamanho in (-1, True, 1.5, "10", None):
            with self.assertRaises(D.ValidationError, msg=repr(tamanho)):
                D.new_document(ANA, nb, "a.pdf", "application/pdf", tamanho, "k")
        with self.assertRaises(D.ValidationError):
            D.new_document(*base, status="QUALQUER")
        for status in D.DOCUMENT_STATUSES:
            D.new_document(*base, status=status)
        with self.assertRaises(D.ValidationError):
            D.new_document(ANA, "id-ruim", "a.pdf", "application/pdf", 1, "k")

    def test_conversa(self):
        nb = D.new_id()
        c = D.new_conversation(ANA, nb)
        self.assertEqual(c["title"], "Nova conversa")
        self.assertEqual(c["SK"], f"CONV#{nb}#{c['conversationId']}")
        with self.assertRaises(D.ValidationError):
            D.new_conversation(ANA, nb, "t" * (D.TITLE_MAX + 1))

    def test_mensagem(self):
        nb, c = D.new_id(), D.new_id()
        m = D.new_message(ANA, nb, c, "assistant", "Resposta", sources=["a.pdf", "b.pdf"])
        self.assertEqual(m["SK"], f"MSG#{nb}#{c}#{m['messageId']}")
        self.assertEqual(m["sources"], ["a.pdf", "b.pdf"])
        self.assertNotIn("sources", D.new_message(ANA, nb, c, "user", "Pergunta"))

    def test_mensagem_validacao(self):
        nb, c = D.new_id(), D.new_id()
        for role in ("system", "", None, "USER"):
            with self.assertRaises(D.ValidationError, msg=repr(role)):
                D.new_message(ANA, nb, c, role, "x")
        for conteudo in ("", "   ", None, "x" * (D.MESSAGE_MAX + 1)):
            with self.assertRaises(D.ValidationError, msg=repr(conteudo)[:20]):
                D.new_message(ANA, nb, c, "user", conteudo)
        for fontes in ("a.pdf", [1], ["x"] * (D.SOURCES_MAX + 1), {"a": 1}):
            with self.assertRaises(D.ValidationError, msg=repr(fontes)):
                D.new_message(ANA, nb, c, "user", "x", sources=fontes)

    def test_mensagem_maxima_cabe_no_limite_do_item(self):
        nb, c = D.new_id(), D.new_id()
        m = D.new_message(ANA, nb, c, "assistant", "ã" * D.MESSAGE_MAX)
        tamanho = sum(len(k.encode()) + len(str(v).encode()) for k, v in m.items())
        self.assertLess(tamanho, 400 * 1024)  # limite de um item no DynamoDB

    def test_nenhum_item_tem_float(self):
        nb, c = D.new_id(), D.new_id()
        itens = [
            D.new_notebook(ANA, "n"),
            D.new_document(ANA, nb, "a", "t", 1, "k"),
            D.new_conversation(ANA, nb),
            D.new_message(ANA, nb, c, "user", "x", sources=["a"]),
        ]
        self.assertTrue(all(sem_float(i) for i in itens))

    def test_ids_informados_sao_validados(self):
        with self.assertRaises(D.ValidationError):
            D.new_notebook(ANA, "n", notebook_id="x#y")


class TestPadroesDeAcesso(unittest.TestCase):
    """Reproduz as consultas que a API fará, sobre uma tabela falsa."""

    def setUp(self):
        self.t = TabelaFalsa()
        self.nb1 = D.new_notebook(ANA, "IA")
        self.nb2 = D.new_notebook(ANA, "SO")
        self.nb_bia = D.new_notebook(BIA, "Cripto")
        self.id1, self.id2 = self.nb1["notebookId"], self.nb2["notebookId"]
        self.doc = D.new_document(ANA, self.id1, "a.pdf", "application/pdf", 1, "k")
        self.doc2 = D.new_document(ANA, self.id2, "b.pdf", "application/pdf", 1, "k")
        self.c1 = D.new_conversation(ANA, self.id1, "Árvores")
        self.c2 = D.new_conversation(ANA, self.id1, "Grafos")
        self.cid1, self.cid2 = self.c1["conversationId"], self.c2["conversationId"]
        self.msgs = [
            D.new_message(ANA, self.id1, self.cid1, "user" if i % 2 == 0 else "assistant", f"m{i}")
            for i in range(6)
        ]
        self.msg_outra_conv = D.new_message(ANA, self.id1, self.cid2, "user", "outra")
        self.msg_bia = D.new_message(BIA, self.nb_bia["notebookId"], D.new_id(), "user", "segredo da Bia")
        for it in [self.nb1, self.nb2, self.nb_bia, self.doc, self.doc2, self.c1, self.c2, self.msg_outra_conv, self.msg_bia, *self.msgs]:
            self.t.put(it)

    def test_listar_notebooks_so_devolve_notebooks_do_usuario(self):
        r = self.t.query(D.list_params(ANA, D.NOTEBOOK_PREFIX))
        self.assertEqual([i["entityType"] for i in r], ["NOTEBOOK", "NOTEBOOK"])
        self.assertEqual([i["name"] for i in r], ["IA", "SO"])  # ordem de criação

    def test_listar_documentos_de_um_notebook(self):
        r = self.t.query(D.list_params(ANA, D.documents_prefix(self.id1)))
        self.assertEqual([i["documentId"] for i in r], [self.doc["documentId"]])

    def test_listar_conversas_mais_novas_primeiro(self):
        r = self.t.query(D.list_params(ANA, D.conversations_prefix(self.id1), newest_first=True))
        self.assertEqual([i["title"] for i in r], ["Grafos", "Árvores"])

    def test_mensagens_em_ordem_e_ultimas_n(self):
        r = self.t.query(D.list_params(ANA, D.messages_prefix(self.id1, self.cid1)))
        self.assertEqual([i["content"] for i in r], [f"m{i}" for i in range(6)])
        ultimas = self.t.query(D.list_params(ANA, D.messages_prefix(self.id1, self.cid1), newest_first=True, limit=3))
        self.assertEqual([i["content"] for i in reversed(ultimas)], ["m3", "m4", "m5"])

    def test_isolamento_entre_usuarios(self):
        # Bia pedindo o notebook/conversa da Ana: a chave inclui o PK da Bia, então não acha nada
        self.assertEqual(self.t.query(D.list_params(BIA, D.messages_prefix(self.id1, self.cid1))), [])
        self.assertEqual(self.t.query(D.list_params(BIA, D.documents_prefix(self.id1))), [])
        self.assertEqual([i["name"] for i in self.t.query(D.list_params(BIA, D.NOTEBOOK_PREFIX))], ["Cripto"])
        # e a Ana nunca vê o que é da Bia
        todos = [i for p in (D.NOTEBOOK_PREFIX, "DOC#", "CONV#", "MSG#") for i in self.t.query(D.list_params(ANA, p))]
        self.assertTrue(all(i["ownerId"] == ANA for i in todos))
        self.assertNotIn("segredo da Bia", [i.get("content") for i in todos])

    def test_cascata_cobre_tudo_do_notebook_e_nada_de_fora(self):
        for prefixo in D.notebook_children_prefixes(self.id1):
            for it in self.t.query(D.list_params(ANA, prefixo)):
                del self.t.itens[(it["PK"], it["SK"])]
        del self.t.itens[(f"USER#{ANA}", D.notebook_sk(self.id1))]
        restante = {i["SK"] for i in self.t.itens.values() if i["ownerId"] == ANA}
        self.assertEqual(restante, {self.nb2["SK"], self.doc2["SK"]})  # só o notebook 2 sobrou
        self.assertIn((f"USER#{BIA}", self.msg_bia["SK"]), self.t.itens)  # nada da Bia foi tocado

    def test_excluir_uma_conversa_nao_afeta_outra(self):
        prefixo = D.messages_prefix(self.id1, self.cid2)
        self.assertEqual([i["content"] for i in self.t.query(D.list_params(ANA, prefixo))], ["outra"])


class TestIsolamentoEPublico(unittest.TestCase):
    def test_assert_owner(self):
        n = D.new_notebook(ANA, "IA")
        self.assertIs(D.assert_owner(n, ANA), n)
        with self.assertRaises(D.OwnershipError):
            D.assert_owner(n, BIA)
        for vazio in (None, {}):
            with self.assertRaises(D.OwnershipError):
                D.assert_owner(vazio, ANA)
        adulterado = dict(n, ownerId=BIA)
        with self.assertRaises(D.OwnershipError):
            D.assert_owner(adulterado, ANA)

    def test_to_public_remove_campos_internos(self):
        n = D.new_notebook(ANA, "IA")
        publico = D.to_public(n)
        for campo in ("PK", "SK", "ownerId", "entityType"):
            self.assertNotIn(campo, publico)
        self.assertEqual(publico["name"], "IA")
        self.assertIn("notebookId", publico)
        self.assertNotIn(ANA, json.dumps(publico))  # o sub do usuário não vaza na resposta


class TestJsonDoDynamo(unittest.TestCase):
    def test_conversao(self):
        r = D.to_dynamodb_json({"a": "x", "b": 3, "c": True, "d": ["y"], "e": {"f": 1}})
        self.assertEqual(r, {"M": {
            "a": {"S": "x"}, "b": {"N": "3"}, "c": {"BOOL": True},
            "d": {"L": [{"S": "y"}]}, "e": {"M": {"f": {"N": "1"}}},
        }})

    def test_tipo_nao_suportado(self):
        with self.assertRaises(TypeError):
            D.to_dynamodb_json(1.5)


if __name__ == "__main__":
    unittest.main()
