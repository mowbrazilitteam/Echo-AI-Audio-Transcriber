"""O Ollama do próprio PC: a conversa sobre os áudios (em fluxo), os vetores da memória longa e a placa de vídeo.

Nada daqui sai do PC: o Ollama escuta em 127.0.0.1. Os modelos de raciocínio (deepseek-r1) escrevem o pensamento entre
<think> e </think>; a conversa mostra só a resposta (FiltroDePensamento).
"""

import json
import logging
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

import httpx

log = logging.getLogger("echo")

TEMPO_MAXIMO = httpx.Timeout(connect=3.0, read=600.0, write=60.0, pool=5.0)
RESPOSTA_RAPIDA_S = 2.0  # para saber se o motor está de pé
ABRE_PENSAMENTO, FECHA_PENSAMENTO = "<think>", "</think>"

# A IA do app só resume e responde sobre o que o whisper transcreveu: modelos de conversa da classe de 1 a 8 bilhões
# de parâmetros (decisão do projeto). Abaixo disso responde mal; acima, não cabe numa placa comum junto do whisper e deixa o
# app pesado. A folga é porque o tamanho real varia em torno do nome: o gemma3:1b tem 999,89 M e o deepseek-r1:8b, 8,2 B.
MINIMO_DE_PARAMETROS = 0.95e9
MAXIMO_DE_PARAMETROS = 8.5e9
UNIDADES_DE_PARAMETROS = {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}
TAMANHO_DECLARADO = re.compile(r"\s*(\d+(?:\.\d+)?)\s*([KMBT])\s*", re.IGNORECASE)


@dataclass(frozen=True)
class ModeloDeIA:
    """Uma IA da lista do app, com o que a tela mostra para comparar lado a lado (qualidade da resposta e velocidade de 1
    a 5; o texto de "para que serve" fica na tela, nos três idiomas)."""

    nome: str
    rotulo: str
    bilhoes: float
    download_gb: float
    qualidade: int
    velocidade: int


# As IAs que o app oferece para instalar: todas de 1 a 8 B, de conversa, boas em português, inglês e espanhol.
CATALOGO_DE_IA = (
    ModeloDeIA("qwen2.5:1.5b", "Qwen 2.5 1.5B", 1.5, 1.0, 2, 5),
    ModeloDeIA("llama3.2:3b", "Llama 3.2 3B", 3.2, 2.0, 3, 4),
    ModeloDeIA("qwen2.5:3b", "Qwen 2.5 3B", 3.1, 1.9, 3, 4),
    ModeloDeIA("gemma3:4b", "Gemma 3 4B", 4.3, 3.3, 4, 3),
    ModeloDeIA("qwen2.5:7b", "Qwen 2.5 7B", 7.6, 4.7, 5, 2),
    ModeloDeIA("llama3.1:8b", "Llama 3.1 8B", 8.0, 4.9, 4, 2),
)
NOMES_DO_CATALOGO_DE_IA = {m.nome for m in CATALOGO_DE_IA}


def parametros(texto: str | None) -> float | None:
    """O parameter_size do Ollama em número: '7.6B' -> 7.6e9, '999.89M' -> 9.9989e8; formato desconhecido -> None."""
    achado = TAMANHO_DECLARADO.fullmatch(texto or "")
    if not achado:
        return None
    return float(achado.group(1)) * UNIDADES_DE_PARAMETROS[achado.group(2).upper()]


def serve_para_a_conversa(nome: str, familias: list[str], quantos: float | None) -> bool:
    """Fica de fora o modelo de vetor (nomic-embed-text), o de código (codellama, qwen2.5-coder), o de mistura de
    especialistas (qwen3:30b-a3b carrega os 30 B inteiros) e o que está fora de 1 a 8 B ou não declara o tamanho."""
    if "embed" in nome or any(f in ("bert", "nomic-bert") for f in familias):
        return False
    if "code" in nome or any("moe" in f for f in familias):
        return False
    return quantos is not None and MINIMO_DE_PARAMETROS <= quantos <= MAXIMO_DE_PARAMETROS


class OllamaFora(RuntimeError):
    """O Ollama não respondeu (desligado ou ocupado): a conversa avisa na tela; a transcrição continua."""


@dataclass
class Ollama:
    url: str
    transporte: httpx.BaseTransport | None = None  # os testes trocam a rede por um transporte falso
    _cliente: httpx.Client = field(init=False)

    def __post_init__(self) -> None:
        self._cliente = httpx.Client(base_url=self.url, timeout=TEMPO_MAXIMO, transport=self.transporte)

    def trocar_url(self, url: str) -> None:
        """O motor mudou de lugar (o app ligou o Ollama dele numa porta própria): quem guardou este objeto segue valendo."""
        if url != self.url:
            self._cliente.close()
            self.url = url
            self.__post_init__()

    def responde(self) -> bool:
        try:
            return self._cliente.get("/api/version", timeout=RESPOSTA_RAPIDA_S).status_code == 200
        except httpx.HTTPError:
            return False

    def instalados(self) -> set[str]:
        return {m["name"] for m in self._json("GET", "/api/tags").get("models", [])}

    def baixar(self, nome: str, avisar: Callable[[int, int], None]) -> None:
        """ollama pull com o andamento: o Ollama manda o total e o baixado de cada camada, e a barra soma as camadas."""
        total: dict[str, int] = {}
        baixado: dict[str, int] = {}
        try:
            with self._cliente.stream("POST", "/api/pull", json={"model": nome, "stream": True}) as r:
                if r.status_code >= 400:
                    raise OllamaFora("o Ollama recusou baixar {}: {}".format(nome, r.read().decode("utf-8", "replace")[:300]))
                for linha in r.iter_lines():
                    if not linha.strip():
                        continue
                    pedaco = json.loads(linha)
                    if pedaco.get("error"):
                        raise OllamaFora("o Ollama não baixou {}: {}".format(nome, pedaco["error"]))
                    camada = pedaco.get("digest")
                    if camada and pedaco.get("total"):
                        total[camada] = int(pedaco["total"])
                        baixado[camada] = int(pedaco.get("completed") or 0)
                        avisar(sum(baixado.values()), sum(total.values()))
                    if pedaco.get("status") == "success":
                        return
        except httpx.HTTPError as erro:
            raise OllamaFora(f"o Ollama caiu no meio do download de {nome} ({type(erro).__name__})") from erro
        raise OllamaFora(f"o Ollama parou antes de terminar o download de {nome}")

    def _pedir(self, metodo: str, caminho: str, **opcoes: Any) -> httpx.Response:
        try:
            resposta = self._cliente.request(metodo, caminho, **opcoes)
        except httpx.HTTPError as erro:
            raise OllamaFora(f"o Ollama não respondeu em {self.url} ({type(erro).__name__})") from erro
        if resposta.status_code >= 400:
            raise OllamaFora(f"o Ollama recusou {caminho}: {resposta.text[:300]}")
        return resposta

    def _json(self, metodo: str, caminho: str, **opcoes: Any) -> Any:
        """A resposta em JSON; um corpo que não é JSON vira OllamaFora (a tela mostra), não um erro solto."""
        resposta = self._pedir(metodo, caminho, **opcoes)
        try:
            return resposta.json()
        except ValueError as erro:
            raise OllamaFora(f"o Ollama devolveu algo que não é JSON em {caminho}") from erro

    def modelos(self) -> list[dict[str, Any]]:
        """Os modelos instalados que servem para a conversa sobre os áudios (serve_para_a_conversa)."""
        dados = self._json("GET", "/api/tags")
        saida = []
        for m in dados.get("models", []):
            detalhes = m.get("details") or {}
            familias = detalhes.get("families") or [detalhes.get("family") or ""]
            quantos = parametros(detalhes.get("parameter_size"))
            if quantos is None or not serve_para_a_conversa(m["name"], familias, quantos):
                continue
            saida.append({"nome": m["name"], "tamanho_gb": round(m.get("size", 0) / 1e9, 1), "bilhoes": round(quantos / 1e9, 1)})
        return sorted(saida, key=lambda m: m["nome"])

    def conversar(self, modelo: str, mensagens: list[dict[str, str]]) -> Iterator[str]:
        """A resposta em pedaços, na hora em que o modelo escreve (sem o pensamento dos modelos de raciocínio)."""
        filtro = FiltroDePensamento()
        try:
            with self._cliente.stream("POST", "/api/chat", json={"model": modelo, "messages": mensagens, "stream": True}) as r:
                if r.status_code >= 400:
                    raise OllamaFora("o Ollama recusou a conversa: {}".format(r.read().decode("utf-8", "replace")[:300]))
                for linha in r.iter_lines():
                    if not linha.strip():
                        continue
                    try:
                        pedaco = json.loads(linha)
                    except json.JSONDecodeError as erro:
                        raise OllamaFora(f"o Ollama mandou uma resposta quebrada: {linha[:120]!r}") from erro
                    if pedaco.get("error"):
                        raise OllamaFora("o Ollama parou no meio: {}".format(pedaco["error"]))
                    texto = filtro.passar((pedaco.get("message") or {}).get("content", ""))
                    if texto:
                        yield texto
                    if pedaco.get("done"):
                        return
        except httpx.HTTPError as erro:
            raise OllamaFora(f"o Ollama caiu no meio da resposta ({type(erro).__name__})") from erro
        # sem o done: a resposta foi cortada (o Ollama caiu ou fechou a conexão); não vale como resposta completa
        raise OllamaFora("o Ollama parou antes de terminar a resposta")

    def vetores(self, modelo: str, textos: list[str]) -> list[list[float]] | None:
        """Os vetores de significado; None se o Ollama não está de pé (quem chama segue sem eles e avisa)."""
        try:
            dados = self._json("POST", "/api/embed", json={"model": modelo, "input": textos})
        except OllamaFora as erro:
            log.warning("sem vetores: %s", erro)
            return None
        vetores = dados.get("embeddings")
        if not isinstance(vetores, list) or len(vetores) != len(textos):
            log.warning("o Ollama devolveu %s vetores para %d textos", len(vetores or []), len(textos))
            return None
        return vetores

    def liberar_da_placa(self) -> None:
        """Tira da placa os modelos que o Ollama deixou carregados, para o whisper caber. Falhar aqui não é grave:
        se a memória não bastar, a transcrição mostra o erro da placa."""
        try:
            carregados = self._json("GET", "/api/ps").get("models", [])
            for m in carregados:
                self._pedir("POST", "/api/generate", json={"model": m["name"], "keep_alive": 0})
                log.info("Ollama liberou %s da placa", m["name"])
        except OllamaFora as erro:
            log.warning("não consegui liberar o Ollama da placa: %s", erro)


class FiltroDePensamento:
    """Tira o que vem entre <think> e </think>, mesmo quando as marcas chegam cortadas em dois pedaços."""

    def __init__(self) -> None:
        self._dentro = False
        self._sobra = ""

    def passar(self, texto: str) -> str:
        texto = self._sobra + texto
        self._sobra = ""
        saida = []
        while texto:
            marca = FECHA_PENSAMENTO if self._dentro else ABRE_PENSAMENTO
            pos = texto.find(marca)
            if pos >= 0:
                if not self._dentro:
                    saida.append(texto[:pos])
                texto = texto[pos + len(marca) :]
                self._dentro = not self._dentro
                continue
            # a marca pode estar chegando cortada: guarda o fim que pode ser o começo dela
            corte = max((i for i in range(1, len(marca)) if texto.endswith(marca[:i])), default=0)
            if not self._dentro:
                saida.append(texto[: len(texto) - corte])
            self._sobra = texto[len(texto) - corte :] if corte else ""
            break
        return "".join(saida)
