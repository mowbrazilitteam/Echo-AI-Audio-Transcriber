"""O que vai para o Ollama junto com a pergunta: o texto dos áudios da conversa, ou os trechos certos de todo o histórico.

A memória longa junta duas buscas: por palavra (FTS5 do SQLite) e por significado (vetores do nomic-embed-text). As
duas listas se juntam por posição (reciprocal rank fusion): o trecho que aparece bem nas duas sobe.
"""

import math
from typing import Any

from echo_ai.banco import Banco
from echo_ai.ollama import Ollama

TRECHOS_DA_MEMORIA = 8
CANDIDATOS_POR_BUSCA = 20
PESO_DA_POSICAO = 60  # a constante do reciprocal rank fusion
CARACTERES_DA_CONVERSA = 24000  # cabe folgado no contexto dos modelos de 7-8B do Ollama

# Em inglês: os modelos pequenos seguem melhor a instrução em inglês. O idioma da resposta vem da tela (en, pt, es):
# sem isso, com o áudio em português e a tela em inglês, o modelo de 7 B copiava a transcrição em vez de resumir.
NOMES_DOS_IDIOMAS = {"en": "English", "pt": "Brazilian Portuguese", "es": "Spanish"}
INSTRUCAO = (
    "You are the assistant inside Echo-AI, a private, local audio transcription app. You answer questions about the "
    "transcripts below, which may be in any language.\n"
    "Rules:\n"
    "1. Always write your answer in {idioma}, even when the transcript is in another language. Translate what you quote.\n"
    "2. Use ONLY the transcripts. Do not copy them word for word: summarize, organize and explain.\n"
    "3. Be direct and brief. Use short paragraphs or a bulleted list when it helps.\n"
    "4. If the answer is not in the transcripts, say you did not find it in the audio.\n"
    "5. When you use a numbered excerpt, cite its number in brackets, like [2].\n"
    "6. Never invent names, numbers or dates."
)


def minuto(segundos: float) -> str:
    return f"{int(segundos) // 60}:{int(segundos) % 60:02d}"


def cosseno(a: list[float], b: list[float]) -> float:
    produto = sum(x * y for x, y in zip(a, b, strict=True))
    norma = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return produto / norma if norma else 0.0


def buscar(
    banco: Banco, ollama: Ollama, modelo_de_busca: str, pergunta: str, limite: int = TRECHOS_DA_MEMORIA
) -> tuple[list[dict[str, Any]], bool]:
    """Os trechos de todo o histórico que mais têm a ver com a pergunta. -> (trechos, usou_significado)."""
    por_palavra = banco.buscar_por_palavra(pergunta, CANDIDATOS_POR_BUSCA, qualquer_palavra=True)
    por_significado: list[dict[str, Any]] = []
    vetor = ollama.vetores(modelo_de_busca, [pergunta])
    if vetor:
        todos = banco.todos_os_vetores()
        por_significado = sorted(todos, key=lambda t: cosseno(vetor[0], t["vetor"]), reverse=True)[:CANDIDATOS_POR_BUSCA]
    nota: dict[int, float] = {}
    trecho: dict[int, dict[str, Any]] = {}
    for lista in (por_palavra, por_significado):
        for posicao, t in enumerate(lista):
            nota[t["id"]] = nota.get(t["id"], 0.0) + 1.0 / (PESO_DA_POSICAO + posicao)
            trecho[t["id"]] = {k: v for k, v in t.items() if k not in ("vetor", "nota")}
    melhores = sorted(nota, key=lambda i: nota[i], reverse=True)[:limite]
    return [trecho[i] for i in melhores], bool(vetor)


def fontes_numeradas(trechos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "n": n,
            "conversa_id": t["conversa_id"],
            "audio_id": t["audio_id"],
            "titulo": t["titulo"],
            "audio": t["nome_original"],
            "inicio": t["inicio"],
            "texto": t["texto"],
        }
        for n, t in enumerate(trechos, 1)
    ]


def mensagens_para_o_ollama(
    pergunta: str, historico: list[dict[str, Any]], contexto: str, idioma: str | None = None
) -> list[dict[str, str]]:
    """A instrução + as transcrições + as últimas trocas da conversa + a pergunta. `idioma` = o da tela (en, pt, es);
    sem ele, a resposta sai no idioma da pergunta."""
    nome = NOMES_DOS_IDIOMAS.get(idioma or "", "the same language as the user's question")
    mensagens = [{"role": "system", "content": INSTRUCAO.format(idioma=nome) + "\n\nTRANSCRIPTS:\n" + (contexto or "(none)")}]
    trocas = [m for m in historico if m["papel"] in ("usuario", "assistente")][-6:]
    for m in trocas:
        mensagens.append({"role": "user" if m["papel"] == "usuario" else "assistant", "content": m["texto"]})
    mensagens.append({"role": "user", "content": pergunta})
    return mensagens


def contexto_da_conversa(banco: Banco, conversa_id: int) -> str:
    """O texto dos áudios prontos desta conversa, cortado para caber no modelo (o começo de cada áudio vem primeiro)."""
    partes = []
    for audio in banco.audios_da_conversa(conversa_id):
        if audio["estado"] != "pronto":
            continue
        partes.append('Audio "{}":\n{}'.format(audio["nome_original"], banco.texto_do_audio(audio["id"])))
    texto = "\n\n".join(partes)
    return texto[:CARACTERES_DA_CONVERSA] + (" [...the rest was cut]" if len(texto) > CARACTERES_DA_CONVERSA else "")


def contexto_da_memoria(fontes: list[dict[str, Any]]) -> str:
    return "\n".join(
        f'[{f["n"]}] chat "{f["titulo"]}", audio "{f["audio"]}", at {minuto(f["inicio"])}: {f["texto"]}' for f in fontes
    )
