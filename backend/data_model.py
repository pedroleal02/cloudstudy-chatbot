"""
Modelo de dados do CloudStudy (Amazon DynamoDB, tabela única).

Este módulo só define CHAVES, ITENS e PARÂMETROS DE CONSULTA. Ele não chama a AWS
nem importa boto3: serve para a Lambda (commit 5 em diante) e é testável offline,
sem custo.

Desenho (detalhes e motivos em docs/modelo-de-dados.md):

    PK = "USER#<sub do Cognito>"        tudo de um usuário vive na MESMA partição

    SK = "NB#<nb>"                      notebook
         "DOC#<nb>#<doc>"               documento do notebook
         "CONV#<nb>#<conv>"             conversa do notebook
         "MSG#<nb>#<conv>#<msg>"        mensagem da conversa

Consequências importantes:
  * Toda consulta exige o PK do usuário autenticado: ler dados de outra pessoa é
    estruturalmente impossível, mesmo que o frontend envie um notebookId alheio.
  * Os prefixos de SK são todos diferentes entre si ("NB#" não é prefixo de
    "DOC#...", etc.), então cada listagem é uma única consulta begins_with.
  * Os IDs são ordenáveis por data de criação (estilo ULID), então a ordem das
    mensagens é a própria ordem das chaves.
"""
import re
import secrets
import threading
import time
from datetime import datetime, timezone

# ---------------------------------------------------------------- limites

NAME_MAX = 80
DESCRIPTION_MAX = 500
TITLE_MAX = 120
SUMMARY_MAX = 5000
FILE_NAME_MAX = 255
CONTENT_TYPE_MAX = 127
S3_KEY_MAX = 1024
MESSAGE_MAX = 50_000          # 50 mil caracteres (<= 200 KB em UTF-8, abaixo do limite de 400 KB do item)
SOURCES_MAX = 20

ROLES = ("user", "assistant")
DOCUMENT_STATUSES = ("PENDING_UPLOAD", "UPLOADED", "PROCESSING", "READY", "FAILED")

# Condições para PutItem/UpdateItem/DeleteItem: sem elas o DynamoDB sobrescreve
# ou CRIA itens sem avisar (UpdateItem em chave inexistente cria um item novo).
CONDITION_NEW = "attribute_not_exists(PK)"
CONDITION_EXISTS = "attribute_exists(PK)"

NOTEBOOK_PREFIX = "NB#"


class ValidationError(ValueError):
    """Dado de entrada inválido (a Lambda deve responder 400)."""


class OwnershipError(PermissionError):
    """Item que não pertence ao usuário autenticado (a Lambda deve responder 404)."""


# ---------------------------------------------------------------- IDs

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # Crockford base32 (sem I, L, O, U)
_ID_RE = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")
_USER_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

_state = {"ms": -1, "rand": 0}
_lock = threading.Lock()


def _encode(valor, tamanho):
    chars = []
    for _ in range(tamanho):
        chars.append(_ALPHABET[valor & 31])
        valor >>= 5
    return "".join(reversed(chars))


def new_id(now_ms=None):
    """
    ID de 26 caracteres, ordenável por data (48 bits de milissegundos + 80 bits
    aleatórios). Dentro do mesmo milissegundo, o contador sobe de 1 em 1: assim a
    mensagem do usuário e a resposta, gravadas juntas, mantêm a ordem.
    """
    with _lock:
        ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
        if ms <= _state["ms"]:  # mesmo milissegundo, ou relógio que voltou
            ms = _state["ms"]
            rand = _state["rand"] + 1
            if rand >= 1 << 80:
                ms += 1
                rand = secrets.randbits(80)
        else:
            rand = secrets.randbits(80)
        _state["ms"], _state["rand"] = ms, rand
        return _encode(ms, 10) + _encode(rand, 16)


def _valid_id(valor, nome):
    if not isinstance(valor, str) or not _ID_RE.fullmatch(valor):
        raise ValidationError(f"{nome} inválido")
    return valor


def _valid_user(user_id):
    # O "sub" do Cognito é um UUID. Validar impede caracteres como "#" de
    # alterar o significado da chave.
    if not isinstance(user_id, str) or not _USER_RE.fullmatch(user_id):
        raise ValidationError("userId inválido")
    return user_id


# ---------------------------------------------------------------- chaves

def pk(user_id):
    return f"USER#{_valid_user(user_id)}"


def notebook_sk(notebook_id):
    return f"NB#{_valid_id(notebook_id, 'notebookId')}"


def document_sk(notebook_id, document_id):
    return f"DOC#{_valid_id(notebook_id, 'notebookId')}#{_valid_id(document_id, 'documentId')}"


def conversation_sk(notebook_id, conversation_id):
    return f"CONV#{_valid_id(notebook_id, 'notebookId')}#{_valid_id(conversation_id, 'conversationId')}"


def message_sk(notebook_id, conversation_id, message_id):
    return (
        f"MSG#{_valid_id(notebook_id, 'notebookId')}"
        f"#{_valid_id(conversation_id, 'conversationId')}"
        f"#{_valid_id(message_id, 'messageId')}"
    )


def documents_prefix(notebook_id):
    return f"DOC#{_valid_id(notebook_id, 'notebookId')}#"


def conversations_prefix(notebook_id):
    return f"CONV#{_valid_id(notebook_id, 'notebookId')}#"


def messages_prefix(notebook_id, conversation_id):
    return f"MSG#{_valid_id(notebook_id, 'notebookId')}#{_valid_id(conversation_id, 'conversationId')}#"


def notebook_messages_prefix(notebook_id):
    """Todas as mensagens de todas as conversas de um notebook (usado ao excluí-lo)."""
    return f"MSG#{_valid_id(notebook_id, 'notebookId')}#"


def notebook_children_prefixes(notebook_id):
    """Prefixos a apagar, além do item do notebook, ao excluir um notebook."""
    return [documents_prefix(notebook_id), conversations_prefix(notebook_id), notebook_messages_prefix(notebook_id)]


def item_key(user_id, sk):
    """Chave completa de um item (para GetItem, UpdateItem e DeleteItem)."""
    return {"PK": pk(user_id), "SK": sk}


# ---------------------------------------------------------------- consultas

def list_params(user_id, sk_prefix, newest_first=False, limit=None, consistent=False):
    """
    Parâmetros para Table.query() (boto3 resource): todos os itens do usuário cuja
    SK começa com `sk_prefix`. O PK vem SEMPRE do usuário autenticado.

    Exemplos:
        list_params(u, NOTEBOOK_PREFIX)                       # notebooks
        list_params(u, documents_prefix(nb))                  # documentos do notebook
        list_params(u, conversations_prefix(nb), True)        # conversas, mais novas primeiro
        list_params(u, messages_prefix(nb, c), True, 10, True)  # últimas 10 mensagens

    Uma consulta devolve no máximo 1 MB; use LastEvaluatedKey para paginar.
    `consistent=True` lê o dado mais recente (custa o dobro de leitura).
    """
    if not isinstance(sk_prefix, str) or not sk_prefix:
        raise ValidationError("prefixo inválido")
    params = {
        "KeyConditionExpression": "PK = :pk AND begins_with(SK, :sk)",
        "ExpressionAttributeValues": {":pk": pk(user_id), ":sk": sk_prefix},
        "ScanIndexForward": not newest_first,
    }
    if limit is not None:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValidationError("limit inválido")
        params["Limit"] = limit
    if consistent:
        params["ConsistentRead"] = True
    return params


# ---------------------------------------------------------------- validação

def utc_now_iso(now=None):
    momento = now or datetime.now(timezone.utc)
    return momento.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{momento.microsecond // 1000:03d}Z"


def _texto(valor, nome, maximo, obrigatorio=True):
    if not isinstance(valor, str):
        raise ValidationError(f"{nome} deve ser texto")
    limpo = valor.strip()
    if obrigatorio and not limpo:
        raise ValidationError(f"{nome} não pode ser vazio")
    if len(limpo) > maximo:
        raise ValidationError(f"{nome} excede {maximo} caracteres")
    return limpo


def _inteiro(valor, nome):
    # bool é subclasse de int em Python: True não vale como tamanho
    if isinstance(valor, bool) or not isinstance(valor, int) or valor < 0:
        raise ValidationError(f"{nome} deve ser um inteiro não negativo")
    return valor


def _base(user_id, sk, tipo, agora):
    return {
        "PK": pk(user_id),
        "SK": sk,
        "entityType": tipo,
        "ownerId": user_id,
        "createdAt": agora,
        "updatedAt": agora,
    }


# ---------------------------------------------------------------- itens
# Só valores str, int, bool, list e dict: o boto3 não aceita float (exige Decimal).

def new_notebook(user_id, name, description="", *, notebook_id=None, now=None):
    nb = notebook_id or new_id()
    item = _base(user_id, notebook_sk(nb), "NOTEBOOK", utc_now_iso(now))
    item.update(
        notebookId=nb,
        name=_texto(name, "name", NAME_MAX),
        description=_texto(description, "description", DESCRIPTION_MAX, obrigatorio=False),
    )
    return item


def new_document(user_id, notebook_id, file_name, content_type, size_bytes, s3_key, *,
                 status="PENDING_UPLOAD", document_id=None, now=None):
    if status not in DOCUMENT_STATUSES:
        raise ValidationError("status inválido")
    doc = document_id or new_id()
    item = _base(user_id, document_sk(notebook_id, doc), "DOCUMENT", utc_now_iso(now))
    item.update(
        documentId=doc,
        notebookId=notebook_id,
        fileName=_texto(file_name, "fileName", FILE_NAME_MAX),
        contentType=_texto(content_type, "contentType", CONTENT_TYPE_MAX),
        sizeBytes=_inteiro(size_bytes, "sizeBytes"),
        s3Key=_texto(s3_key, "s3Key", S3_KEY_MAX),
        status=status,
    )
    return item


def new_conversation(user_id, notebook_id, title="Nova conversa", *, conversation_id=None, now=None):
    conv = conversation_id or new_id()
    item = _base(user_id, conversation_sk(notebook_id, conv), "CONVERSATION", utc_now_iso(now))
    item.update(
        conversationId=conv,
        notebookId=notebook_id,
        title=_texto(title, "title", TITLE_MAX),
    )
    return item


def new_message(user_id, notebook_id, conversation_id, role, content, *,
                sources=None, message_id=None, now=None):
    if role not in ROLES:
        raise ValidationError("role inválido")
    msg = message_id or new_id()
    item = _base(user_id, message_sk(notebook_id, conversation_id, msg), "MESSAGE", utc_now_iso(now))
    item.update(
        messageId=msg,
        conversationId=conversation_id,
        notebookId=notebook_id,
        role=role,
        content=_texto(content, "content", MESSAGE_MAX),
    )
    if sources is not None:
        if not isinstance(sources, list) or len(sources) > SOURCES_MAX:
            raise ValidationError("sources inválido")
        item["sources"] = [_texto(s, "sources", FILE_NAME_MAX) for s in sources]
    return item


# ---------------------------------------------------------------- isolamento

def assert_owner(item, user_id):
    """Segunda barreira, além da chave: o item lido precisa ser do usuário autenticado."""
    if not item or item.get("PK") != pk(user_id) or item.get("ownerId") != user_id:
        raise OwnershipError("recurso não encontrado")
    return item


_INTERNOS = ("PK", "SK", "ownerId", "entityType")


def to_public(item):
    """Item sem os campos internos, pronto para devolver ao frontend."""
    return {k: v for k, v in item.items() if k not in _INTERNOS}


# ---------------------------------------------------------------- utilitário de teste manual

def to_dynamodb_json(valor):
    """Converte um item para o JSON tipado do DynamoDB (colar em 'Criar item' no console)."""
    if isinstance(valor, bool):
        return {"BOOL": valor}
    if isinstance(valor, int):
        return {"N": str(valor)}
    if isinstance(valor, str):
        return {"S": valor}
    if isinstance(valor, list):
        return {"L": [to_dynamodb_json(v) for v in valor]}
    if isinstance(valor, dict):
        return {"M": {k: to_dynamodb_json(v) for k, v in valor.items()}}
    raise TypeError(f"tipo não suportado: {type(valor).__name__}")


if __name__ == "__main__":
    # python data_model.py  ->  itens de exemplo (usuário fictício) para o teste manual no console
    import json

    usuario = "00000000-0000-4000-8000-000000000001"
    caderno = new_notebook(usuario, "Inteligência Artificial", "Estudos da disciplina")
    conversa = new_conversation(usuario, caderno["notebookId"], "Árvores binárias")
    pergunta = new_message(usuario, caderno["notebookId"], conversa["conversationId"], "user", "O que é uma árvore binária?")
    resposta = new_message(usuario, caderno["notebookId"], conversa["conversationId"], "assistant", "Uma árvore binária é uma estrutura em que cada nó tem no máximo dois filhos.")
    for rotulo, it in (("NOTEBOOK", caderno), ("CONVERSA", conversa), ("MENSAGEM 1 (user)", pergunta), ("MENSAGEM 2 (assistant)", resposta)):
        print(f"--- {rotulo} ---")
        print(json.dumps({k: to_dynamodb_json(v) for k, v in it.items()}, ensure_ascii=False, indent=2))
    print("--- CONSULTAS PARA TESTAR ---")
    print("PK =", pk(usuario))
    print("Notebooks:  SK begins_with", NOTEBOOK_PREFIX)
    print("Mensagens:  SK begins_with", messages_prefix(caderno["notebookId"], conversa["conversationId"]))
