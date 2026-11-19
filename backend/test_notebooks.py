"""
Testes da API de notebooks com uma tabela DynamoDB falsa em memória (sem AWS, sem custo).
Requer: boto3 instalado (pip install boto3)
Rodar:  python3 -m unittest test_notebooks -v

A tabela falsa recusa o que o DynamoDB real recusaria nas chamadas que usamos
(placeholder sobrando, tipo float, expressão diferente da esperada), para os
testes não passarem por acaso.
"""
import json
import os
import re
import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ.setdefault("MODEL_ID", "modelo-de-teste")
os.environ.setdefault("KB_ID", "KBTESTE123")
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

import lambda_function as L  # noqa: E402
import modelos as M  # noqa: E402
import documentos as D  # noqa: E402
import notebooks as N  # noqa: E402
from botocore.exceptions import BotoCoreError, ClientError  # noqa: E402

CONTEXTO = SimpleNamespace(aws_request_id="req-12345678")
USUARIO_A = "11111111-aaaa-4aaa-8aaa-111111111111"
USUARIO_B = "22222222-bbbb-4bbb-8bbb-222222222222"

EXPRESSAO_QUERY = "pk = :pk AND begins_with(sk, :prefixo)"


def erro_aws(codigo):
    return ClientError({"Error": {"Code": codigo, "Message": "detalhe interno arn:aws:dynamodb:..."}}, "Op")


def sem_float(valor):
    if isinstance(valor, float):
        raise TypeError("Float types are not supported. Use Decimal types instead.")
    if isinstance(valor, dict):
        for v in valor.values():
            sem_float(v)
    if isinstance(valor, list):
        for v in valor:
            sem_float(v)


def como_o_boto3_devolve(item):
    """O boto3 (resource) devolve todo número do DynamoDB como Decimal, nunca como int."""
    def converte(v):
        if isinstance(v, bool):
            return v
        if isinstance(v, int):
            return Decimal(v)
        if isinstance(v, dict):
            return {k: converte(x) for k, x in v.items()}
        if isinstance(v, list):
            return [converte(x) for x in v]
        return v
    return converte(item)


class TabelaFalsa:
    """Implementa só o que notebooks.py usa: get_item, put_item, update_item, delete_item, query, batch_writer."""

    def __init__(self, tamanho_pagina=1000):
        self.itens = {}
        self.tamanho_pagina = tamanho_pagina
        self.consultas = []
        self.falhar_com = None  # erro a lançar na próxima chamada

    def _checar_falha(self):
        if self.falhar_com is not None:
            erro, self.falhar_com = self.falhar_com, None
            raise erro

    def get_item(self, Key, ConsistentRead=False):
        self._checar_falha()
        item = self.itens.get((Key["pk"], Key["sk"]))
        return {"Item": como_o_boto3_devolve(item)} if item else {}

    def put_item(self, Item, ConditionExpression=None):
        self._checar_falha()
        sem_float(Item)
        chave = (Item["pk"], Item["sk"])
        if ConditionExpression == "attribute_not_exists(pk)" and chave in self.itens:
            raise erro_aws("ConditionalCheckFailedException")
        self.itens[chave] = dict(Item)

    def update_item(self, Key, UpdateExpression, ConditionExpression, ExpressionAttributeNames,
                    ExpressionAttributeValues, ReturnValues):
        self._checar_falha()
        assert ConditionExpression == "attribute_exists(pk)"
        assert ReturnValues == "ALL_NEW"
        sem_float(ExpressionAttributeValues)
        chave = (Key["pk"], Key["sk"])
        if chave not in self.itens:
            raise erro_aws("ConditionalCheckFailedException")

        assert UpdateExpression.startswith("SET ")
        atribuicoes = [p.strip() for p in UpdateExpression[4:].split(",")]
        usados_nomes, usados_valores = set(), set()
        novos = {}
        for a in atribuicoes:
            nome, valor = [x.strip() for x in a.split("=")]
            usados_nomes.add(nome)
            usados_valores.add(valor)
            novos[ExpressionAttributeNames[nome]] = ExpressionAttributeValues[valor]
        # o DynamoDB real recusa placeholders declarados e não usados
        assert usados_nomes == set(ExpressionAttributeNames), "ExpressionAttributeNames sobrando"
        assert usados_valores == set(ExpressionAttributeValues), "ExpressionAttributeValues sobrando"

        self.itens[chave].update(novos)
        return {"Attributes": como_o_boto3_devolve(self.itens[chave])}

    def delete_item(self, Key):
        self._checar_falha()
        self.itens.pop((Key["pk"], Key["sk"]), None)

    def query(self, KeyConditionExpression, ExpressionAttributeValues, ConsistentRead=False,
              ProjectionExpression=None, ExclusiveStartKey=None):
        self._checar_falha()
        assert KeyConditionExpression == EXPRESSAO_QUERY
        assert set(ExpressionAttributeValues) == {":pk", ":prefixo"}
        assert ConsistentRead is True
        self.consultas.append((ExpressionAttributeValues[":pk"], ExpressionAttributeValues[":prefixo"]))

        pk, prefixo = ExpressionAttributeValues[":pk"], ExpressionAttributeValues[":prefixo"]
        chaves = sorted(k for k in self.itens if k[0] == pk and k[1].startswith(prefixo))
        if ExclusiveStartKey:
            chaves = [k for k in chaves if k[1] > ExclusiveStartKey["sk"]]
        pagina = chaves[: self.tamanho_pagina]

        itens = []
        for k in pagina:
            item = como_o_boto3_devolve(self.itens[k])
            if ProjectionExpression:
                campos = [c.strip() for c in ProjectionExpression.split(",")]
                item = {c: item[c] for c in campos}
            itens.append(item)
        resposta = {"Items": itens}
        if len(chaves) > self.tamanho_pagina:
            resposta["LastEvaluatedKey"] = {"pk": pk, "sk": pagina[-1][1]}
        return resposta

    def batch_writer(self):
        tabela = self

        class Lote:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def delete_item(self, Key):
                tabela.delete_item(Key=Key)

        return Lote()


def contexto_autenticado(sub):
    return {"authorizer": {"jwt": {"claims": {"sub": sub}, "scopes": None}}}


class S3Falso:
    """Bucket em memória: objetos = {chave: tamanho}. `enviar` simula o upload do navegador."""

    def __init__(self):
        self.chamadas = []  # presign
        self.objetos = {}
        self.corpos = {}  # conteúdo dos objetos gravados pela Lambda (metadados)
        self.falhar_com = None

    def _falha(self):
        if self.falhar_com is not None:
            erro, self.falhar_com = self.falhar_com, None
            raise erro

    def enviar(self, chave, tamanho=1):
        self.objetos[chave] = tamanho

    def generate_presigned_post(self, Bucket, Key, Fields, Conditions, ExpiresIn):
        self._falha()
        self.chamadas.append({"Bucket": Bucket, "Key": Key, "Fields": Fields, "Conditions": Conditions, "ExpiresIn": ExpiresIn})
        return {"url": f"https://{Bucket}.s3.amazonaws.com/", "fields": {**Fields, "key": Key, "policy": "p", "x-amz-signature": "s"}}

    def put_object(self, Bucket, Key, Body, ContentType):
        self._falha()
        self.objetos[Key] = len(Body)
        self.corpos[Key] = Body

    def head_object(self, Bucket, Key):
        self._falha()
        if Key not in self.objetos:
            raise ClientError({"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject")
        return {"ContentLength": self.objetos[Key]}

    def delete_object(self, Bucket, Key):
        self._falha()
        self.objetos.pop(Key, None)

    def delete_objects(self, Bucket, Delete):
        self._falha()
        for o in Delete["Objects"]:
            self.objetos.pop(o["Key"], None)
        return {}


class AgenteFalso:
    """bedrock-agent: sincronização (ingestion job) da Knowledge Base."""

    def __init__(self):
        self.jobs = {}
        self.ocupado = False
        self.falhar_com = None
        self.chamadas = []

    def start_ingestion_job(self, knowledgeBaseId, dataSourceId):
        if self.falhar_com is not None:
            raise self.falhar_com
        if self.ocupado:
            raise ClientError({"Error": {"Code": "ConflictException", "Message": "job em andamento"}}, "StartIngestionJob")
        job_id = f"JOB{len(self.jobs) + 1}"
        self.jobs[job_id] = {"status": "IN_PROGRESS", "falhas": 0}
        self.chamadas.append((knowledgeBaseId, dataSourceId, job_id))
        return {"ingestionJob": {"ingestionJobId": job_id, "status": "STARTING"}}

    def get_ingestion_job(self, knowledgeBaseId, dataSourceId, ingestionJobId):
        if self.falhar_com is not None:
            raise self.falhar_com
        j = self.jobs[ingestionJobId]
        return {"ingestionJob": {"status": j["status"], "statistics": {"numberOfDocumentsFailed": j["falhas"]}}}

    def concluir(self, job_id, status="COMPLETE", falhas=0):
        self.jobs[job_id].update(status=status, falhas=falhas)


class Base(unittest.TestCase):

    def setUp(self):
        self.tabela = TabelaFalsa()
        N.tabela = self.tabela
        self.s3 = S3Falso()
        D.s3 = self.s3
        self.agente = AgenteFalso()
        D.agente = self.agente
        patcher = patch.dict(os.environ, {"DOCS_BUCKET": "bucket-de-teste", "KB_ID": "KBTESTE123", "DATA_SOURCE_ID": "DSTESTE123"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        N.tabela = None
        D.s3 = None
        D.agente = None

    def chamar(self, rota, usuario=USUARIO_A, corpo=None, notebook_id=None, bruto=None, autenticado=True):
        evento = {"routeKey": rota}
        if corpo is not None:
            evento["body"] = json.dumps(corpo)
        if bruto is not None:
            evento["body"] = bruto
        if notebook_id is not None:
            evento["pathParameters"] = {"notebookId": notebook_id}
        if autenticado:
            evento["requestContext"] = contexto_autenticado(usuario)
        r = L.lambda_handler(evento, CONTEXTO)
        return r["statusCode"], (json.loads(r["body"]) if r["body"] else None), r

    def criar(self, nome="Inteligência Artificial", usuario=USUARIO_A, **extra):
        status, corpo, _ = self.chamar("POST /notebooks", usuario, {"nome": nome, **extra})
        self.assertEqual(status, 201, corpo)
        return corpo["notebook"]

    def popular_filhos(self, usuario, notebook_id):
        """Documento, conversa e mensagens do notebook, gravados direto na tabela falsa."""
        itens = [
            M.novo_documento(usuario, notebook_id, "apostila.pdf", 1000, f"usuarios/{usuario}/{notebook_id}/doc.pdf"),
            conversa := M.nova_conversa(usuario, notebook_id, "Árvores"),
            M.nova_mensagem(usuario, notebook_id, conversa["conversaId"], "usuario", "O que é uma árvore?"),
            M.nova_mensagem(usuario, notebook_id, conversa["conversaId"], "assistente", "Uma estrutura...",
                            [{"id": 1, "arquivo": "a.md", "trecho": "texto"}]),
        ]
        for i in itens:
            self.tabela.itens[(i["pk"], i["sk"])] = i
        return itens

    def chaves_do_usuario(self, usuario):
        return {k for k in self.tabela.itens if k[0] == f"USER#{usuario}"}


class TestCriar(Base):

    def test_cria_e_devolve_201_sem_atributos_internos(self):
        status, corpo, _ = self.chamar("POST /notebooks", corpo={"nome": "  Sistemas Operacionais ", "descricao": "Aulas"})
        self.assertEqual(status, 201)
        nb = corpo["notebook"]
        self.assertEqual(nb["nome"], "Sistemas Operacionais")
        self.assertEqual(nb["descricao"], "Aulas")
        self.assertRegex(nb["notebookId"], M.ID_DE_ITEM)
        for interno in ("pk", "sk", "entidade", "usuarioId"):
            self.assertNotIn(interno, nb)
        self.assertNotIn(USUARIO_A, json.dumps(corpo))

    def test_grava_na_particao_do_usuario_do_token(self):
        nb = self.criar()
        self.assertEqual(self.chaves_do_usuario(USUARIO_A), {(f"USER#{USUARIO_A}", f"NB#{nb['notebookId']}")})

    def test_dono_enviado_no_corpo_e_ignorado(self):
        nb = self.criar(usuarioId=USUARIO_B, pk=f"USER#{USUARIO_B}", sub=USUARIO_B, notebookId="X")
        self.assertEqual(self.chaves_do_usuario(USUARIO_B), set())
        self.assertEqual(len(self.chaves_do_usuario(USUARIO_A)), 1)
        self.assertRegex(nb["notebookId"], M.ID_DE_ITEM)

    def test_corpo_invalido(self):
        casos = [
            ({"nome": ""}, None),
            ({"nome": "   "}, None),
            ({"nome": 123}, None),
            ({"nome": "x" * 81}, None),
            ({"descricao": "sem nome"}, None),
            ({"nome": "ok", "descricao": "x" * 501}, None),
            (None, "{quebrado"),
            (None, "[]"),
            (None, '"texto"'),
        ]
        for corpo, bruto in casos:
            with self.subTest(corpo=corpo, bruto=bruto):
                status, resp, _ = self.chamar("POST /notebooks", corpo=corpo, bruto=bruto)
                self.assertEqual(status, 400)
                self.assertIn("erro", resp)
        self.assertEqual(self.tabela.itens, {})

    def test_sem_corpo_devolve_400(self):
        self.assertEqual(self.chamar("POST /notebooks")[0], 400)

    def test_corpo_grande_demais(self):
        status, _, _ = self.chamar("POST /notebooks", bruto=json.dumps({"nome": "a", "descricao": "x" * 20_000}))
        self.assertEqual(status, 413)

    def test_ids_diferentes_a_cada_criacao(self):
        ids = {self.criar()["notebookId"] for _ in range(5)}
        self.assertEqual(len(ids), 5)


class TestListar(Base):

    def test_lista_vazia(self):
        status, corpo, _ = self.chamar("GET /notebooks")
        self.assertEqual((status, corpo), (200, {"notebooks": []}))

    def test_lista_em_ordem_de_criacao(self):
        nomes = ["IA", "SO", "Estrutura de Dados", "Criptografia"]
        for n in nomes:
            self.criar(n)
        _, corpo, _ = self.chamar("GET /notebooks")
        self.assertEqual([n["nome"] for n in corpo["notebooks"]], nomes)

    def test_lista_so_notebooks_e_nao_documentos_conversas_ou_mensagens(self):
        nb = self.criar()
        self.popular_filhos(USUARIO_A, nb["notebookId"])
        _, corpo, _ = self.chamar("GET /notebooks")
        self.assertEqual([n["notebookId"] for n in corpo["notebooks"]], [nb["notebookId"]])

    def test_percorre_todas_as_paginas(self):
        self.tabela.tamanho_pagina = 2
        criados = [self.criar(f"Notebook {i}")["notebookId"] for i in range(7)]
        _, corpo, _ = self.chamar("GET /notebooks")
        self.assertEqual([n["notebookId"] for n in corpo["notebooks"]], criados)

    def test_cada_usuario_ve_so_os_seus(self):
        a = self.criar("do A", USUARIO_A)
        b = self.criar("do B", USUARIO_B)
        _, lista_a, _ = self.chamar("GET /notebooks", USUARIO_A)
        _, lista_b, _ = self.chamar("GET /notebooks", USUARIO_B)
        self.assertEqual([n["notebookId"] for n in lista_a["notebooks"]], [a["notebookId"]])
        self.assertEqual([n["notebookId"] for n in lista_b["notebooks"]], [b["notebookId"]])

    def test_consulta_sempre_na_particao_do_token(self):
        self.criar(usuario=USUARIO_A)
        self.tabela.consultas.clear()
        self.chamar("GET /notebooks", USUARIO_B)
        self.assertEqual({pk for pk, _ in self.tabela.consultas}, {f"USER#{USUARIO_B}"})


class TestObter(Base):

    def test_obtem_o_proprio_notebook(self):
        nb = self.criar()
        status, corpo, _ = self.chamar("GET /notebooks/{notebookId}", notebook_id=nb["notebookId"])
        self.assertEqual((status, corpo), (200, {"notebook": nb}))

    def test_inexistente_devolve_404(self):
        status, corpo, _ = self.chamar("GET /notebooks/{notebookId}", notebook_id=M.novo_id())
        self.assertEqual((status, corpo), (404, {"erro": "Notebook não encontrado."}))

    def test_id_malformado_devolve_404_igual_ao_inexistente(self):
        for ruim in ("abc", "", "../../x", "NB#01", "a" * 200, None):
            with self.subTest(id=ruim):
                status, corpo, _ = self.chamar("GET /notebooks/{notebookId}", notebook_id=ruim)
                self.assertEqual((status, corpo), (404, {"erro": "Notebook não encontrado."}))

    def test_sem_path_parameters_devolve_404(self):
        self.assertEqual(self.chamar("GET /notebooks/{notebookId}")[0], 404)

    def test_notebook_de_outro_usuario_devolve_404(self):
        nb_a = self.criar("do A", USUARIO_A)
        status, corpo, _ = self.chamar("GET /notebooks/{notebookId}", USUARIO_B, notebook_id=nb_a["notebookId"])
        self.assertEqual((status, corpo), (404, {"erro": "Notebook não encontrado."}))
        self.assertNotIn("do A", json.dumps(corpo))

    def test_defesa_em_profundidade_item_de_outro_dono_na_particao_certa(self):
        # simula um item corrompido: pk do A, mas cujo conteúdo diz ser de outro dono
        nb = self.criar()
        chave = (f"USER#{USUARIO_A}", f"NB#{nb['notebookId']}")
        self.tabela.itens[chave]["pk"] = f"USER#{USUARIO_B}"
        status, _, _ = self.chamar("GET /notebooks/{notebookId}", notebook_id=nb["notebookId"])
        self.assertEqual(status, 404)


class TestAtualizar(Base):

    def test_atualiza_nome_e_descricao(self):
        nb = self.criar("IA", descricao="antiga")
        status, corpo, _ = self.chamar("PATCH /notebooks/{notebookId}", corpo={"nome": " Machine Learning ", "descricao": "nova"},
                                       notebook_id=nb["notebookId"])
        self.assertEqual(status, 200)
        novo = corpo["notebook"]
        self.assertEqual((novo["nome"], novo["descricao"]), ("Machine Learning", "nova"))
        self.assertEqual(novo["notebookId"], nb["notebookId"])
        self.assertEqual(novo["criadoEm"], nb["criadoEm"])
        self.assertGreaterEqual(novo["atualizadoEm"], nb["atualizadoEm"])

    def test_atualiza_so_um_campo_e_preserva_o_outro(self):
        nb = self.criar("IA", descricao="mantida")
        _, corpo, _ = self.chamar("PATCH /notebooks/{notebookId}", corpo={"nome": "IA 2"}, notebook_id=nb["notebookId"])
        self.assertEqual((corpo["notebook"]["nome"], corpo["notebook"]["descricao"]), ("IA 2", "mantida"))

    def test_descricao_pode_ser_limpa(self):
        nb = self.criar("IA", descricao="algo")
        _, corpo, _ = self.chamar("PATCH /notebooks/{notebookId}", corpo={"descricao": ""}, notebook_id=nb["notebookId"])
        self.assertEqual(corpo["notebook"]["descricao"], "")

    def test_campos_protegidos_sao_ignorados(self):
        nb = self.criar()
        ataque = {"nome": "ok", "usuarioId": USUARIO_B, "pk": f"USER#{USUARIO_B}", "sk": "NB#X", "notebookId": "X",
                  "criadoEm": "2000-01-01T00:00:00.000Z", "entidade": "OUTRA"}
        status, corpo, _ = self.chamar("PATCH /notebooks/{notebookId}", corpo=ataque, notebook_id=nb["notebookId"])
        self.assertEqual(status, 200)
        self.assertEqual(corpo["notebook"]["notebookId"], nb["notebookId"])
        self.assertEqual(corpo["notebook"]["criadoEm"], nb["criadoEm"])
        item = self.tabela.itens[(f"USER#{USUARIO_A}", f"NB#{nb['notebookId']}")]
        self.assertEqual(item["usuarioId"], USUARIO_A)
        self.assertEqual(item["entidade"], "NOTEBOOK")
        self.assertEqual(len(self.tabela.itens), 1)

    def test_corpo_invalido_devolve_400_e_nao_altera(self):
        nb = self.criar("IA")
        for corpo, bruto in (({}, None), ({"nome": ""}, None), ({"nome": 5}, None), ({"x": 1}, None), (None, "{quebrado"), (None, "[]")):
            with self.subTest(corpo=corpo, bruto=bruto):
                status, _, _ = self.chamar("PATCH /notebooks/{notebookId}", corpo=corpo, bruto=bruto, notebook_id=nb["notebookId"])
                self.assertEqual(status, 400)
        item = self.tabela.itens[(f"USER#{USUARIO_A}", f"NB#{nb['notebookId']}")]
        self.assertEqual(item["nome"], "IA")

    def test_inexistente_devolve_404_e_nao_cria_item(self):
        status, _, _ = self.chamar("PATCH /notebooks/{notebookId}", corpo={"nome": "x"}, notebook_id=M.novo_id())
        self.assertEqual(status, 404)
        self.assertEqual(self.tabela.itens, {})

    def test_id_malformado_devolve_404(self):
        self.assertEqual(self.chamar("PATCH /notebooks/{notebookId}", corpo={"nome": "x"}, notebook_id="abc")[0], 404)

    def test_notebook_de_outro_usuario_devolve_404_e_nao_e_alterado(self):
        nb_a = self.criar("do A", USUARIO_A)
        status, _, _ = self.chamar("PATCH /notebooks/{notebookId}", USUARIO_B, {"nome": "invadido"}, nb_a["notebookId"])
        self.assertEqual(status, 404)
        self.assertEqual(self.tabela.itens[(f"USER#{USUARIO_A}", f"NB#{nb_a['notebookId']}")]["nome"], "do A")
        self.assertEqual(self.chaves_do_usuario(USUARIO_B), set())


class TestExcluir(Base):

    def test_exclui_e_devolve_204_sem_corpo(self):
        nb = self.criar()
        status, corpo, resp = self.chamar("DELETE /notebooks/{notebookId}", notebook_id=nb["notebookId"])
        self.assertEqual((status, corpo, resp["body"]), (204, None, ""))
        self.assertEqual(self.tabela.itens, {})
        self.assertEqual(self.chamar("GET /notebooks/{notebookId}", notebook_id=nb["notebookId"])[0], 404)

    def test_excluir_duas_vezes_devolve_404_na_segunda(self):
        nb = self.criar()
        self.chamar("DELETE /notebooks/{notebookId}", notebook_id=nb["notebookId"])
        self.assertEqual(self.chamar("DELETE /notebooks/{notebookId}", notebook_id=nb["notebookId"])[0], 404)

    def test_exclusao_em_cascata_apaga_documentos_conversas_e_mensagens(self):
        nb = self.criar()
        self.popular_filhos(USUARIO_A, nb["notebookId"])
        self.assertEqual(len(self.chaves_do_usuario(USUARIO_A)), 5)
        self.assertEqual(self.chamar("DELETE /notebooks/{notebookId}", notebook_id=nb["notebookId"])[0], 204)
        self.assertEqual(self.chaves_do_usuario(USUARIO_A), set())

    def test_cascata_so_atinge_o_notebook_excluido(self):
        nb1 = self.criar("um")
        nb2 = self.criar("dois")
        self.popular_filhos(USUARIO_A, nb1["notebookId"])
        filhos2 = self.popular_filhos(USUARIO_A, nb2["notebookId"])
        self.chamar("DELETE /notebooks/{notebookId}", notebook_id=nb1["notebookId"])
        restantes = self.chaves_do_usuario(USUARIO_A)
        self.assertEqual(restantes, {(f"USER#{USUARIO_A}", f"NB#{nb2['notebookId']}")} | {(i["pk"], i["sk"]) for i in filhos2})

    def test_cascata_com_varias_paginas(self):
        self.tabela.tamanho_pagina = 2
        nb = self.criar()
        conversa = M.nova_conversa(USUARIO_A, nb["notebookId"])
        self.tabela.itens[(conversa["pk"], conversa["sk"])] = conversa
        for i in range(9):
            m = M.nova_mensagem(USUARIO_A, nb["notebookId"], conversa["conversaId"], "usuario", f"m{i}")
            self.tabela.itens[(m["pk"], m["sk"])] = m
        self.assertEqual(self.chamar("DELETE /notebooks/{notebookId}", notebook_id=nb["notebookId"])[0], 204)
        self.assertEqual(self.tabela.itens, {})

    def test_notebook_de_outro_usuario_devolve_404_e_nada_e_apagado(self):
        nb_a = self.criar("do A", USUARIO_A)
        self.popular_filhos(USUARIO_A, nb_a["notebookId"])
        antes = dict(self.tabela.itens)
        status, _, _ = self.chamar("DELETE /notebooks/{notebookId}", USUARIO_B, notebook_id=nb_a["notebookId"])
        self.assertEqual(status, 404)
        self.assertEqual(self.tabela.itens, antes)

    def test_usuario_b_nunca_apaga_itens_do_a_nem_com_o_mesmo_notebook_id(self):
        nb_a = self.criar("do A", USUARIO_A)
        self.popular_filhos(USUARIO_A, nb_a["notebookId"])
        # B tem um notebook próprio com filhos; excluí-lo não toca nos itens do A
        nb_b = self.criar("do B", USUARIO_B)
        self.popular_filhos(USUARIO_B, nb_b["notebookId"])
        total_a = len(self.chaves_do_usuario(USUARIO_A))
        self.chamar("DELETE /notebooks/{notebookId}", USUARIO_B, notebook_id=nb_b["notebookId"])
        self.assertEqual(len(self.chaves_do_usuario(USUARIO_A)), total_a)
        self.assertEqual(self.chaves_do_usuario(USUARIO_B), set())

    def test_falha_no_meio_mantem_o_notebook_para_nova_tentativa(self):
        nb = self.criar()
        self.popular_filhos(USUARIO_A, nb["notebookId"])
        original = self.tabela.delete_item
        chamadas = {"n": 0}

        def falha_na_terceira(Key):
            chamadas["n"] += 1
            if chamadas["n"] == 3:
                raise erro_aws("InternalServerError")
            original(Key=Key)

        self.tabela.delete_item = falha_na_terceira
        self.assertEqual(self.chamar("DELETE /notebooks/{notebookId}", notebook_id=nb["notebookId"])[0], 500)
        self.assertIn((f"USER#{USUARIO_A}", f"NB#{nb['notebookId']}"), self.tabela.itens)  # notebook segue de pé

        self.tabela.delete_item = original
        self.assertEqual(self.chamar("DELETE /notebooks/{notebookId}", notebook_id=nb["notebookId"])[0], 204)
        self.assertEqual(self.tabela.itens, {})


class TestIsolamentoEntreUsuarios(Base):
    """Cenário de acesso cruzado: B conhece o ID do notebook de A e tenta tudo."""

    def test_b_com_o_id_do_a_nao_le_nao_altera_nao_exclui_e_nao_lista(self):
        nb_a = self.criar("segredo do A", USUARIO_A)
        self.popular_filhos(USUARIO_A, nb_a["notebookId"])
        estado = {k: dict(v) for k, v in self.tabela.itens.items()}
        nid = nb_a["notebookId"]

        tentativas = [
            ("GET /notebooks/{notebookId}", None),
            ("PATCH /notebooks/{notebookId}", {"nome": "invadido"}),
            ("DELETE /notebooks/{notebookId}", None),
        ]
        for rota, corpo in tentativas:
            with self.subTest(rota=rota):
                status, resp, bruto = self.chamar(rota, USUARIO_B, corpo, nid)
                self.assertEqual(status, 404)
                self.assertNotIn("segredo", bruto["body"])

        _, lista, bruto = self.chamar("GET /notebooks", USUARIO_B)
        self.assertEqual(lista, {"notebooks": []})
        self.assertNotIn("segredo", bruto["body"])
        self.assertEqual(self.tabela.itens, estado)

    def test_id_do_usuario_no_corpo_cabecalho_ou_query_nao_muda_o_dono(self):
        evento = {
            "routeKey": "GET /notebooks",
            "headers": {"x-user-id": USUARIO_A},
            "queryStringParameters": {"userId": USUARIO_A, "sub": USUARIO_A},
            "body": json.dumps({"userId": USUARIO_A}),
            "requestContext": contexto_autenticado(USUARIO_B),
        }
        self.criar("do A", USUARIO_A)
        r = L.lambda_handler(evento, CONTEXTO)
        self.assertEqual(json.loads(r["body"]), {"notebooks": []})


class TestAutenticacaoERotas(Base):

    ROTAS = [
        ("POST /notebooks", {"nome": "x"}, None),
        ("GET /notebooks", None, None),
        ("GET /notebooks/{notebookId}", None, "X"),
        ("PATCH /notebooks/{notebookId}", {"nome": "x"}, "X"),
        ("DELETE /notebooks/{notebookId}", None, "X"),
    ]

    def test_sem_autenticacao_devolve_401_e_nao_toca_na_tabela(self):
        for rota, corpo, nid in self.ROTAS:
            with self.subTest(rota=rota):
                status, resp, _ = self.chamar(rota, corpo=corpo, notebook_id=nid, autenticado=False)
                self.assertEqual((status, resp), (401, {"erro": "Não autenticado."}))
        self.assertEqual(self.tabela.consultas, [])
        self.assertEqual(self.tabela.itens, {})

    def test_autenticacao_vem_antes_do_corpo(self):
        self.assertEqual(self.chamar("POST /notebooks", bruto="{quebrado", autenticado=False)[0], 401)

    def test_rota_desconhecida_devolve_404(self):
        for rota in ("GET /outra", "PUT /notebooks/{notebookId}", "POST /notebooks/{notebookId}", "$default"):
            with self.subTest(rota=rota):
                status, resp, _ = self.chamar(rota)
                self.assertEqual((status, resp), (404, {"erro": "Rota não encontrada."}))

    def test_chat_continua_funcionando_com_route_key(self):
        L.agente = MagicMock()
        L.agente.retrieve.return_value = {"retrievalResults": []}
        L.bedrock = MagicMock()
        L.bedrock.converse.return_value = {"output": {"message": {"content": [{"text": "Não encontrei isso nos materiais."}]}}}
        nb = self.criar()["notebookId"]
        evento = {"routeKey": "POST /chat", "body": json.dumps({"pergunta": "O que é recursão?", "notebookId": nb}),
                  "requestContext": contexto_autenticado(USUARIO_A)}
        r = L.lambda_handler(evento, CONTEXTO)
        self.assertEqual(r["statusCode"], 200)
        self.assertEqual(self.tabela.consultas, [])


class TestErrosDaAws(Base):

    def test_limite_de_requisicoes_do_dynamodb_vira_429(self):
        for codigo in ("ProvisionedThroughputExceededException", "ThrottlingException", "RequestLimitExceeded"):
            with self.subTest(codigo=codigo):
                self.tabela.falhar_com = erro_aws(codigo)
                status, resp, _ = self.chamar("GET /notebooks")
                self.assertEqual(status, 429)

    def test_erro_do_dynamodb_vira_500_generico_com_id_da_requisicao(self):
        self.tabela.falhar_com = erro_aws("InternalServerError")
        status, resp, bruto = self.chamar("GET /notebooks")
        self.assertEqual(status, 500)
        self.assertEqual(resp["id_requisicao"], "req-12345678")
        self.assertNotIn("arn:aws", bruto["body"])
        self.assertNotIn("dynamodb", bruto["body"].lower())

    def test_erro_de_conexao_vira_502(self):
        self.tabela.falhar_com = BotoCoreError()
        self.assertEqual(self.chamar("GET /notebooks")[0], 502)

    def test_tabela_nao_configurada_vira_500_sem_vazar_detalhe(self):
        N.tabela = None
        os.environ.pop("TABLE_NAME", None)
        status, resp, bruto = self.chamar("GET /notebooks")
        self.assertEqual(status, 500)
        self.assertNotIn("TABLE_NAME", bruto["body"])

    def test_log_nao_expoe_id_completo_nem_o_nome_do_notebook(self):
        with self.assertLogs(level="INFO") as logs:
            self.criar("Nome Muito Secreto")
        saida = "\n".join(logs.output)
        self.assertIn(USUARIO_A[:8], saida)
        self.assertNotIn(USUARIO_A, saida)
        self.assertNotIn("Muito Secreto", saida)


if __name__ == "__main__":
    unittest.main()