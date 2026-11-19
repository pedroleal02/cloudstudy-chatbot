"""
Modelo de dados do CloudStudy no DynamoDB (tabela única).

Este módulo NÃO chama a AWS: só define as chaves, valida os dados e monta os
itens que serão gravados. Quem lê e grava na tabela é a camada de API
(notebooks.py a partir do commit 5).

Regra de isolamento: a partition key (pk) é SEMPRE derivada do `sub` do token
validado pelo API Gateway. Ela nunca vem do corpo, da URL ou de qualquer dado
enviado pelo frontend. Como todo acesso começa pelo pk, uma pessoa não consegue
alcançar itens de outra, mesmo conhecendo os IDs deles.

Layout (pk = USER#<sub>):

    NB#<notebook>                           notebook
    DOC#<notebook>#<documento>              documento
    CONV#<notebook>#<conversa>              conversa
    MSG#<notebook>#<conversa>#<mensagem>    mensagem
"""
import re
import secrets
import threading
import time
from datetime import datetime, timezone

# ---------- limites ----------

MAX_NOME_NOTEBOOK = 80
MAX_DESCRICAO_NOTEBOOK = 500
MAX_NOME_DOCUMENTO = 255
MAX_TAMANHO_DOCUMENTO = 50 * 1024 * 1024  # a Knowledge Base ignora arquivos maiores
MAX_CHAVE_S3 = 1024
PREFIXO_S3_USUARIOS = "usuarios/"
MAX_TITULO_CONVERSA = 120
MAX_CONTEUDO_MENSAGEM = 10_000
MAX_REFERENCIAS = 10
MAX_TRECHO_REFERENCIA = 400

TITULO_PADRAO_CONVERSA = "Nova conversa"

TIPOS_DOCUMENTO = ("md", "txt", "pdf")
# o tipo do conteúdo é decidido aqui, a partir da extensão; nunca vem do cliente
TIPOS_DE_CONTEUDO = {"md": "text/markdown", "txt": "text/plain", "pdf": "application/pdf"}
STATUS_DOCUMENTO = ("PENDENTE", "ENVIADO", "PRONTO", "ERRO")
AUTORES = ("usuario", "assistente")

# mesmo padrão do lambda_function.py (sub do Cognito)
ID_DE_USUARIO = re.compile(r"[A-Za-z0-9-]{8,64}")
# ULID: 26 caracteres no alfabeto de Crockford (sem I, L, O, U)
ID_DE_ITEM = re.compile(r"[0-9A-HJKMNP-TV-Z]{26}")
CARACTERES_DE_CONTROLE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# atributos internos: nunca devolvidos ao frontend
_ATRIBUTOS_INTERNOS = frozenset({"pk", "sk", "entidade", "usuarioId", "chaveS3"})


class DadoInvalido(ValueError):
    """Dado que não pode virar item. A mensagem é segura para mostrar ao usuário."""


# ---------- identificadores (ULID) ----------
# O ULID mistura tempo e aleatoriedade: ordena por criação (a sort key do
# DynamoDB já entrega a ordem cronológica) e não é adivinhável. Dentro do mesmo
# milissegundo a parte aleatória é incrementada, então a ordem é estritamente
# crescente (a pergunta e a resposta gravadas juntas nunca se invertem).

_ALFABETO = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_trava = threading.Lock()
_ultimo_ms = -1
_ultimo_aleatorio = 0


def _codificar(valor, tamanho):
    caracteres = []
    for _ in range(tamanho):
        caracteres.append(_ALFABETO[valor & 31])
        valor >>= 5
    return "".join(reversed(caracteres))


def novo_id():
    global _ultimo_ms, _ultimo_aleatorio
    with _trava:
        ms = int(time.time() * 1000)
        if ms > _ultimo_ms:
            _ultimo_ms = ms
            _ultimo_aleatorio = secrets.randbits(80)
        else:  # mesmo milissegundo (ou relógio voltou): mantém a ordem crescente
            _ultimo_aleatorio += 1
            if _ultimo_aleatorio >> 80:
                _ultimo_ms += 1
                _ultimo_aleatorio = secrets.randbits(80)
        return _codificar(_ultimo_ms, 10) + _codificar(_ultimo_aleatorio, 16)


def agora_iso():
    """Data/hora UTC em ISO 8601 com milissegundos (ordena como texto)."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


# ---------- validação ----------


def validar_usuario(usuario_id):
    if not isinstance(usuario_id, str) or not ID_DE_USUARIO.fullmatch(usuario_id):
        raise DadoInvalido("Usuário inválido.")
    return usuario_id


def validar_id(valor, rotulo="identificador"):
    if not isinstance(valor, str) or not ID_DE_ITEM.fullmatch(valor):
        raise DadoInvalido(f"O {rotulo} é inválido.")
    return valor


def _texto(valor, campo, maximo, obrigatorio=True):
    if valor is None:
        valor = ""
    if not isinstance(valor, str):
        raise DadoInvalido(f"O campo '{campo}' deve ser um texto.")
    valor = CARACTERES_DE_CONTROLE.sub("", valor).strip()
    if obrigatorio and not valor:
        raise DadoInvalido(f"O campo '{campo}' não pode estar vazio.")
    if len(valor) > maximo:
        raise DadoInvalido(f"O campo '{campo}' é longo demais (máx. {maximo} caracteres).")
    return valor


# ---------- chaves ----------


def pk_usuario(usuario_id):
    return f"USER#{validar_usuario(usuario_id)}"


def sk_notebook(notebook_id):
    return f"NB#{validar_id(notebook_id, 'notebook')}"


def sk_documento(notebook_id, documento_id):
    return f"DOC#{validar_id(notebook_id, 'notebook')}#{validar_id(documento_id, 'documento')}"


def sk_conversa(notebook_id, conversa_id):
    return f"CONV#{validar_id(notebook_id, 'notebook')}#{validar_id(conversa_id, 'conversa')}"


def sk_mensagem(notebook_id, conversa_id, mensagem_id):
    return (
        f"MSG#{validar_id(notebook_id, 'notebook')}"
        f"#{validar_id(conversa_id, 'conversa')}#{validar_id(mensagem_id, 'mensagem')}"
    )


# Prefixos para Query com begins_with(sk, ...) sempre junto de pk = USER#<sub>

PREFIXO_NOTEBOOKS = "NB#"


def prefixo_documentos(notebook_id):
    return f"DOC#{validar_id(notebook_id, 'notebook')}#"


def prefixo_conversas(notebook_id):
    return f"CONV#{validar_id(notebook_id, 'notebook')}#"


def prefixo_mensagens(notebook_id, conversa_id):
    return f"MSG#{validar_id(notebook_id, 'notebook')}#{validar_id(conversa_id, 'conversa')}#"


def prefixo_mensagens_notebook(notebook_id):
    """Todas as mensagens de todas as conversas do notebook (exclusão em cascata)."""
    return f"MSG#{validar_id(notebook_id, 'notebook')}#"


# ---------- itens ----------


def novo_notebook(usuario_id, nome, descricao=""):
    nome = _texto(nome, "nome", MAX_NOME_NOTEBOOK)
    descricao = _texto(descricao, "descricao", MAX_DESCRICAO_NOTEBOOK, obrigatorio=False)
    notebook_id = novo_id()
    agora = agora_iso()
    return {
        "pk": pk_usuario(usuario_id),
        "sk": sk_notebook(notebook_id),
        "entidade": "NOTEBOOK",
        "usuarioId": usuario_id,
        "notebookId": notebook_id,
        "nome": nome,
        "descricao": descricao,
        "criadoEm": agora,
        "atualizadoEm": agora,
    }


def validar_arquivo(nome, tamanho_bytes):
    """Valida o nome e o tamanho de um arquivo. Devolve (nome limpo, tipo pela extensão)."""
    nome = _texto(nome, "nome", MAX_NOME_DOCUMENTO)

    extensao = nome.rsplit(".", 1)[-1].lower() if "." in nome else ""
    if extensao not in TIPOS_DOCUMENTO:
        raise DadoInvalido("Formato não aceito. Envie arquivos .md, .txt ou .pdf.")

    if isinstance(tamanho_bytes, bool) or not isinstance(tamanho_bytes, int) or tamanho_bytes <= 0:
        raise DadoInvalido("O tamanho do arquivo é inválido.")
    if tamanho_bytes > MAX_TAMANHO_DOCUMENTO:
        raise DadoInvalido("O arquivo é grande demais (máx. 50 MB).")
    return nome, extensao


def chave_s3(usuario_id, notebook_id, documento_id, tipo):
    """Chave do arquivo no bucket: usuarios/<sub>/<notebook>/<documento>.<tipo>.

    Só IDs validados entram na chave. O nome original do arquivo nunca: não há como
    escapar da pasta do usuário (../) nem quebrar a chave com caracteres especiais.
    """
    if tipo not in TIPOS_DOCUMENTO:
        raise DadoInvalido("Formato não aceito. Envie arquivos .md, .txt ou .pdf.")
    return (
        f"{PREFIXO_S3_USUARIOS}{validar_usuario(usuario_id)}/"
        f"{validar_id(notebook_id, 'notebook')}/{validar_id(documento_id, 'documento')}.{tipo}"
    )


def novo_documento(usuario_id, notebook_id, nome, tamanho_bytes, chave_s3, documento_id=None):
    nome, extensao = validar_arquivo(nome, tamanho_bytes)
    chave_s3 = _texto(chave_s3, "chaveS3", MAX_CHAVE_S3)

    # o ID pode vir de fora porque a chave do S3 (que usa o ID) é montada antes do item
    documento_id = novo_id() if documento_id is None else documento_id
    agora = agora_iso()
    return {
        "pk": pk_usuario(usuario_id),
        "sk": sk_documento(notebook_id, documento_id),
        "entidade": "DOCUMENTO",
        "usuarioId": usuario_id,
        "notebookId": notebook_id,
        "documentoId": documento_id,
        "nome": nome,
        "tipo": extensao,
        "tamanhoBytes": tamanho_bytes,
        "chaveS3": chave_s3,
        "status": "PENDENTE",
        "criadoEm": agora,
        "atualizadoEm": agora,
    }


def atualizacao_notebook(corpo):
    """Valida o corpo de um PATCH. Devolve só os campos editáveis presentes, já limpos.

    Qualquer outro campo do corpo é ignorado: dono, chaves e datas nunca vêm do cliente.
    """
    if not isinstance(corpo, dict):
        raise DadoInvalido("Informe 'nome' ou 'descricao' para atualizar.")
    campos = {}
    if "nome" in corpo:
        campos["nome"] = _texto(corpo["nome"], "nome", MAX_NOME_NOTEBOOK)
    if "descricao" in corpo:
        campos["descricao"] = _texto(corpo["descricao"], "descricao", MAX_DESCRICAO_NOTEBOOK, obrigatorio=False)
    if not campos:
        raise DadoInvalido("Informe 'nome' ou 'descricao' para atualizar.")
    return campos


def nova_conversa(usuario_id, notebook_id, titulo=None):
    titulo = _texto(titulo, "titulo", MAX_TITULO_CONVERSA, obrigatorio=False) or TITULO_PADRAO_CONVERSA
    conversa_id = novo_id()
    agora = agora_iso()
    return {
        "pk": pk_usuario(usuario_id),
        "sk": sk_conversa(notebook_id, conversa_id),
        "entidade": "CONVERSA",
        "usuarioId": usuario_id,
        "notebookId": notebook_id,
        "conversaId": conversa_id,
        "titulo": titulo,
        "criadoEm": agora,
        "atualizadoEm": agora,
    }


def _referencias(referencias):
    """Mesmo formato que a API devolve em `referencias`: [{id, arquivo, trecho}]."""
    if referencias is None:
        return []
    if not isinstance(referencias, list) or len(referencias) > MAX_REFERENCIAS:
        raise DadoInvalido("As referências são inválidas.")
    limpas = []
    for r in referencias:
        if not isinstance(r, dict) or isinstance(r.get("id"), bool) or not isinstance(r.get("id"), int):
            raise DadoInvalido("As referências são inválidas.")
        limpas.append({
            "id": r["id"],
            "arquivo": _texto(r.get("arquivo"), "arquivo", MAX_NOME_DOCUMENTO, obrigatorio=False),
            "trecho": _texto(r.get("trecho"), "trecho", MAX_TRECHO_REFERENCIA, obrigatorio=False),
        })
    return limpas


def nova_mensagem(usuario_id, notebook_id, conversa_id, autor, conteudo, referencias=None):
    if autor not in AUTORES:
        raise DadoInvalido("O autor da mensagem é inválido.")
    conteudo = _texto(conteudo, "conteudo", MAX_CONTEUDO_MENSAGEM)
    mensagem_id = novo_id()
    return {
        "pk": pk_usuario(usuario_id),
        "sk": sk_mensagem(notebook_id, conversa_id, mensagem_id),
        "entidade": "MENSAGEM",
        "usuarioId": usuario_id,
        "notebookId": notebook_id,
        "conversaId": conversa_id,
        "mensagemId": mensagem_id,
        "autor": autor,
        "conteudo": conteudo,
        "referencias": _referencias(referencias),
        "criadoEm": agora_iso(),
    }


# ---------- saída ----------


def pertence_ao_usuario(item, usuario_id):
    """Defesa em profundidade: confere o dono depois de ler. A chave já garante isso."""
    return isinstance(item, dict) and item.get("pk") == pk_usuario(usuario_id)


def para_api(item):
    """Item sem atributos internos (chaves, dono, caminho no S3), pronto para o JSON da API."""
    return {k: v for k, v in item.items() if k not in _ATRIBUTOS_INTERNOS}