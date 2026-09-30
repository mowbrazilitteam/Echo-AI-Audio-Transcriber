"""Testes do Transcritor: banco e busca, a fila com um motor falso (sem placa), a proteção local, as rotas, o filtro do
pensamento dos modelos de raciocínio e a memória longa. Nada toca a placa, a rede nem a pasta de dados real."""

import hashlib
import importlib.util
import io
import json
import math
import queue
import sqlite3
import struct
import sys
import tarfile
import threading
import wave
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from huggingface_hub import try_to_load_from_cache as hf_try_to_load_from_cache

from echo_ai import aceleracao, hardware, memoria, motor_ia, servidor
from echo_ai import banco as banco_do_app
from echo_ai import transcricao as tr
from echo_ai.aceleracao import Aceleracao
from echo_ai.banco import Banco, consulta_fts
from echo_ai.config import Config
from echo_ai.hardware import Maquina, recomendar_ia, recomendar_whisper
from echo_ai.motor_ia import MotorDeIA
from echo_ai.ollama import FiltroDePensamento, Ollama, OllamaFora, parametros, serve_para_a_conversa
from echo_ai.transcricao import Avisos, Fila, Placa, contorno
from echo_ai.voz import FalhaDaVoz, falar
from echo_ai.web import Servicos, criar_app, srt

PORTA = 8799
MAQUINA_DE_TESTE = Maquina(
    sistema="linux",
    arquitetura="x86_64",
    ram_gb=16.0,
    placa=None,
    vram_gb=None,
    placa_sem_driver=False,
    aceleracao_pendente=False,
    dispositivo="cpu",
    whisper_recomendado="small",
    ia_recomendada="qwen2.5:7b",
)
HOST = f"127.0.0.1:{PORTA}"
ESCRITA = {"X-Echo": "1", "Origin": "http://" + HOST}


@dataclass
class TrechoFalso:
    start: float
    end: float
    text: str


@dataclass
class InfoFalsa:
    duration: float = 12.0
    language: str = "pt"


class MotorFalso:
    def __init__(self, trechos: list[TrechoFalso], erro: Exception | None = None) -> None:
        self.trechos, self.erro = trechos, erro
        self.recebido: Any = None

    def transcribe(self, audio: Any, **opcoes: Any) -> tuple[list[TrechoFalso], InfoFalsa]:
        if self.erro:
            raise self.erro
        self.recebido = (audio, opcoes)
        return self.trechos, InfoFalsa()


def ollama_falso(
    vetor: list[float] | None = None,
    resposta: str = "Resumo [1].",
    puxados: list[str] | None = None,
    vazio: bool = False,
    liberados: list[str] | None = None,
) -> Ollama:
    puxados = puxados if puxados is not None else []
    liberados = liberados if liberados is not None else []

    def responder(pedido: httpx.Request) -> httpx.Response:
        if pedido.url.path == "/api/embed":
            corpo = json.loads(pedido.content)
            return httpx.Response(200, json={"embeddings": [vetor or [1.0, 0.0]] * len(corpo["input"])})
        if pedido.url.path == "/api/tags" and vazio:
            return httpx.Response(200, json={"models": []})
        if pedido.url.path == "/api/tags":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {"name": "qwen2.5:1.5b", "size": 1e9, "details": {"families": ["qwen2"], "parameter_size": "1.5B"}},
                        {
                            "name": "nomic-embed-text:latest",
                            "size": 2e8,
                            "details": {"families": ["nomic-bert"], "parameter_size": "137M"},
                        },
                        {"name": "qwen3:30b-a3b", "size": 19e9, "details": {"families": ["qwen3moe"], "parameter_size": "30.5B"}},
                    ]
                },
            )
        if pedido.url.path == "/api/chat":
            linhas = [json.dumps({"message": {"content": p}, "done": False}) for p in ("<think>pens", "ando</think>", resposta)]
            return httpx.Response(200, content=("\n".join(linhas + [json.dumps({"done": True})]) + "\n").encode())
        if pedido.url.path == "/api/ps":
            return httpx.Response(200, json={"models": [{"name": "qwen2.5:1.5b"}]})
        if pedido.url.path == "/api/generate":
            corpo = json.loads(pedido.content)
            if corpo.get("keep_alive") == 0:
                liberados.append(corpo["model"])
            return httpx.Response(200, json={"done": True})
        if pedido.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.34.4"})
        if pedido.url.path == "/api/pull":
            nome = json.loads(pedido.content)["model"]
            puxados.append(nome)
            andamento: list[dict[str, Any]] = [
                {"status": "pulling manifest"},
                {"status": "pulling", "digest": "sha256:a", "total": 300, "completed": 100},
                {"status": "pulling", "digest": "sha256:a", "total": 300, "completed": 300},
                {"status": "pulling", "digest": "sha256:b", "total": 100, "completed": 100},
                {"status": "success"},
            ]
            return httpx.Response(200, content=("\n".join(json.dumps(x) for x in andamento) + "\n").encode())
        return httpx.Response(404, json={"error": "rota falsa"})

    return Ollama("http://ollama.teste", transporte=httpx.MockTransport(responder))


@pytest.fixture
def servicos(tmp_path: Path) -> Servicos:
    config = Config(
        pasta_dados=tmp_path / "dados", porta=PORTA, piper_binario=tmp_path / "sem-piper", ollama_modelo="qwen2.5:1.5b"
    )
    config.preparar()
    assert str(config.banco).startswith(str(tmp_path)), "o teste NÃO pode encostar no banco real"
    banco = Banco(config.banco)
    banco.migrar()
    liberados: list[str] = []
    ollama = ollama_falso(liberados=liberados)
    avisos = Avisos()
    motor = MotorFalso(
        [
            TrechoFalso(0.0, 4.0, " Bom dia, equipe. "),
            TrechoFalso(4.0, 9.5, "O contrato foi ajustado hoje."),
            TrechoFalso(9.5, 10.0, "  "),
        ]
    )
    placa = Placa(lambda nome, disp, prec: motor, "cpu", 3)
    fila = Fila(
        banco=banco,
        placa=placa,
        avisos=avisos,
        pasta_audios=config.pasta_audios,
        precisao="int8",
        liberar_ollama=ollama.liberar_da_placa,
        vetorizar=lambda textos: ollama.vetores(config.modelo_de_busca, textos),
    )
    # o motor "do sistema" é o Ollama falso: /api/version responde, então nada é baixado nem ligado
    motor_de_ia = MotorDeIA(tmp_path / "motor", "http://ollama.teste", cliente=httpx.Client(transport=ollama._cliente._transport))
    servicos = Servicos(
        config=config,
        banco=banco,
        fila=fila,
        placa=placa,
        avisos=avisos,
        ollama=ollama,
        maquina=MAQUINA_DE_TESTE,
        motor=motor_de_ia,
        aceleracao=Aceleracao(tmp_path / "aceleracao"),
    )
    servicos.liberados_do_ollama = liberados  # type: ignore[attr-defined]  # o que o Ollama falso tirou da placa
    return servicos


@pytest.fixture(autouse=True)
def cache_do_hugging_face(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[str]:
    """O cache do Hugging Face é a fronteira: o teste diz quais repositórios estão nele (o turbo, por padrão), e a
    pasta dos modelos incluídos no instalador aponta para uma pasta vazia do teste."""
    nomes = [tr.repositorio_do_modelo("large-v3-turbo")]

    def no_cache(repositorio: str, arquivo: str, revision: str | None = None) -> str | None:
        return f"/cache/{repositorio}/{arquivo}" if repositorio in nomes else None

    monkeypatch.setattr("huggingface_hub.try_to_load_from_cache", no_cache)
    monkeypatch.setattr(tr, "PASTA_DOS_MODELOS_INCLUIDOS", tmp_path / "modelos-incluidos")
    return nomes


@pytest.fixture
def cliente(servicos: Servicos) -> TestClient:
    return TestClient(criar_app(servicos), base_url="http://" + HOST)


def por_audio(servicos: Servicos, nome: str) -> str:
    """Um áudio de verdade na pasta dos áudios (a fila decodifica com o PyAV real)."""
    (servicos.config.pasta_audios / nome).write_bytes(wav_de_teste())
    return nome


# ── banco ────────────────────────────────────────────────────────────────────────────────────
def test_migracao_cria_o_esquema_uma_vez_e_a_busca_por_palavra_acha_sem_acento(servicos: Servicos) -> None:
    b = servicos.banco
    assert b.migrar() == 1  # rodar de novo não recria nada
    c = b.criar_conversa()
    a = b.acrescentar_audio(c, "reuniao.m4a", "x.m4a", "large-v3-turbo", "pt")
    b.gravar_trecho(a, 1, 0, 3, "Reunião sobre o orçamento da Associação", 0.5)
    assert [t["texto"] for t in b.buscar_por_palavra("associacao")] == ["Reunião sobre o orçamento da Associação"]
    assert [x["id"] for x in b.conversas("orcamento")] == [c]
    assert b.conversas("inexistente") == []


def test_consulta_fts_nao_deixa_a_busca_quebrar_o_sql() -> None:
    assert consulta_fts('a" OR 1=1 --') == '"a"* "OR"* "1=1"* "--"*'
    assert consulta_fts("   ") == '""'


def test_apagar_conversa_leva_audios_trechos_e_mensagens(servicos: Servicos) -> None:
    b = servicos.banco
    c = b.criar_conversa()
    a = b.acrescentar_audio(c, "x.mp3", "arq.mp3", "large-v3-turbo", "pt")
    b.gravar_trecho(a, 1, 0, 1, "texto", 1.0)
    assert b.apagar_conversa(c) == ["arq.mp3"]
    assert b.audio(a) is None and b.trechos(a) == [] and b.buscar_por_palavra("texto") == []


# ── fila ─────────────────────────────────────────────────────────────────────────────────────
def test_a_fila_transcreve_grava_os_trechos_a_onda_e_os_vetores_e_avisa_quem_olha(servicos: Servicos) -> None:
    b = servicos.banco
    c = b.criar_conversa()
    a = b.acrescentar_audio(c, "nota.ogg", por_audio(servicos, "nota.ogg"), "large-v3-turbo", "pt")
    ouvinte = servicos.avisos.ouvir(a)
    servicos.fila.transcrever(b.proximo_da_fila())  # type: ignore[arg-type]
    audio = b.audio(a)
    assert audio and audio["estado"] == "pronto" and audio["idioma_detectado"] == "pt"
    assert [t["texto"] for t in b.trechos(a)] == [
        "Bom dia, equipe.",
        "O contrato foi ajustado hoje.",
    ]  # trecho vazio fica de fora
    assert len(json.loads(audio["onda"])) == 600 and max(json.loads(audio["onda"])) == 1.0
    assert b.trechos_sem_vetor(a) == []
    eventos = [ouvinte.get_nowait()["tipo"] for _ in range(ouvinte.qsize())]
    assert eventos == ["estado", "onda", "trecho", "trecho", "estado"]


def test_falha_do_motor_vira_erro_na_tela_e_a_fila_segue(servicos: Servicos) -> None:
    b = servicos.banco
    servicos.placa.carregar = lambda n, d, p: MotorFalso([], erro=RuntimeError("CUDA out of memory"))
    c = b.criar_conversa()
    a = b.acrescentar_audio(c, "x.mp3", por_audio(servicos, "x.mp3"), "large-v3", "pt")
    servicos.fila.transcrever(b.proximo_da_fila())  # type: ignore[arg-type]
    audio = b.audio(a)
    assert audio and audio["estado"] == "erro" and "CUDA out of memory" in audio["erro"]


def test_placa_que_falha_antes_do_primeiro_trecho_passa_para_a_cpu_e_transcreve(servicos: Servicos) -> None:
    b = servicos.banco
    bom = MotorFalso([TrechoFalso(0.0, 2.0, "Na CPU.")])
    servicos.placa.dispositivo = "cuda"
    servicos.fila.precisao = "float16"
    servicos.placa.carregar = lambda n, d, p: (
        MotorFalso([], erro=RuntimeError("Library libcublas.so.12 is not found")) if d == "cuda" else bom
    )
    a = b.acrescentar_audio(b.criar_conversa(), "x.mp3", por_audio(servicos, "x.mp3"), "small", "pt")
    servicos.fila.transcrever(b.proximo_da_fila())  # type: ignore[arg-type]
    assert b.audio(a)["estado"] == "pronto"  # type: ignore[index]
    assert [t["texto"] for t in b.trechos(a)] == ["Na CPU."]
    assert (servicos.placa.dispositivo, servicos.fila.precisao) == ("cpu", "int8")
    assert "libcublas" in (servicos.placa.motivo_da_cpu or "")


def test_erro_que_nao_e_da_placa_nao_troca_para_a_cpu(servicos: Servicos) -> None:
    b = servicos.banco
    servicos.placa.dispositivo = "cuda"
    servicos.placa.carregar = lambda n, d, p: MotorFalso([], erro=ValueError("arquivo truncado"))
    a = b.acrescentar_audio(b.criar_conversa(), "x.mp3", por_audio(servicos, "x.mp3"), "small", "pt")
    servicos.fila.transcrever(b.proximo_da_fila())  # type: ignore[arg-type]
    assert b.audio(a)["estado"] == "erro" and servicos.placa.dispositivo == "cuda"  # type: ignore[index]


@pytest.mark.parametrize(
    ("dispositivo", "vram", "esperado"), [("cuda", 6.0, "large-v3-turbo"), ("cuda", 2.0, "small"), ("cpu", None, "small")]
)
def test_o_whisper_recomendado_cabe_na_maquina(dispositivo: str, vram: float | None, esperado: str) -> None:
    assert recomendar_whisper(dispositivo, vram) == esperado


@pytest.mark.parametrize(
    ("ram", "vram", "esperado"),
    [(32.0, None, "qwen2.5:7b"), (8.0, 6.0, "qwen2.5:7b"), (8.0, None, "qwen2.5:3b"), (4.0, None, "qwen2.5:1.5b")],
)
def test_a_ia_recomendada_cabe_na_maquina(ram: float, vram: float | None, esperado: str) -> None:
    assert recomendar_ia(ram, vram) == esperado


def test_o_que_caiu_no_meio_volta_para_a_fila_e_recomeça_limpo(servicos: Servicos) -> None:
    b = servicos.banco
    c = b.criar_conversa()
    a = b.acrescentar_audio(c, "x.mp3", por_audio(servicos, "x.mp3"), "large-v3-turbo", "pt")
    assert b.proximo_da_fila()["id"] == a  # type: ignore[index]
    b.gravar_trecho(a, 1, 0, 1, "meio texto", 0.1)
    assert b.devolver_os_interrompidos() == 1
    assert b.proximo_da_fila()["id"] == a and b.trechos(a) == []  # type: ignore[index]


def test_dois_pegadores_nunca_pegam_o_mesmo_audio(servicos: Servicos) -> None:
    b = servicos.banco
    c = b.criar_conversa()
    ids = [b.acrescentar_audio(c, f"{i}.mp3", f"{i}.mp3", "large-v3-turbo", "pt") for i in range(20)]
    pegos: list[int] = []
    trava = threading.Lock()

    def pegar() -> None:
        while (a := b.proximo_da_fila()) is not None:
            with trava:
                pegos.append(a["id"])

    threads = [threading.Thread(target=pegar) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(pegos) == ids


def test_contorno_normaliza_os_picos_e_audio_vazio_nao_quebra() -> None:
    assert contorno([]) == []
    picos = contorno([0.0, -0.5, 0.25, 1.0] * 10, barras=4)
    assert len(picos) == 4 and max(picos) == 1.0


def test_placa_libera_o_modelo_parado_e_troca_quando_pedem_outro() -> None:
    carregados: list[str] = []

    def carregar(nome: str, dispositivo: str, precisao: str) -> MotorFalso:
        carregados.append(nome)
        return MotorFalso([])

    placa = Placa(carregar, "cpu", 0)
    placa.motor("small", "int8")
    placa.motor("small", "int8")
    placa.motor("medium", "int8")
    assert carregados == ["small", "medium"] and placa.carregado() == "medium"
    assert placa.liberar_se_parada() is True and placa.carregado() is None


# ── Ollama e memória ─────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "pedacos,esperado",
    [
        (["<think>a</think>Oi"], "Oi"),
        (["<thi", "nk>pensando</th", "ink>Resposta", " final"], "Resposta final"),
        (["Sem pensamento"], "Sem pensamento"),
        (["texto <", "b>ok"], "texto <b>ok"),
    ],
)
def test_filtro_tira_o_pensamento_mesmo_com_a_marca_cortada(pedacos: list[str], esperado: str) -> None:
    f = FiltroDePensamento()
    assert "".join(f.passar(p) for p in pedacos) == esperado


def test_modelos_do_ollama_deixam_de_fora_o_de_vetor_e_o_de_30_bilhoes() -> None:
    assert [(m["nome"], m["bilhoes"]) for m in ollama_falso().modelos()] == [("qwen2.5:1.5b", 1.5)]


@pytest.mark.parametrize(
    ("nome", "familias", "tamanho", "serve"),
    [
        ("qwen2.5:7b", ["qwen2"], "7.6B", True),
        ("gemma3:1b", ["gemma3"], "999.89M", True),  # vendido como 1 B
        ("deepseek-r1:8b", ["qwen3"], "8.2B", True),  # vendido como 8 B
        ("llama3.1:70b", ["llama"], "70.6B", False),
        ("qwen3:30b-a3b", ["qwen3moe"], "30.5B", False),
        ("qwen3:4b-moe-teste", ["qwen3moe"], "4.0B", False),  # mistura de especialistas, mesmo pequena
        ("qwen2.5-coder:7b", ["qwen2"], "7.6B", False),
        ("smollm:360m", ["llama"], "362M", False),
        ("nomic-embed-text:latest", ["nomic-bert"], "137M", False),
        ("sem-tamanho:latest", ["llama"], None, False),
        ("tamanho-estranho:latest", ["llama"], "sete bilhões", False),
    ],
)
def test_so_serve_para_a_conversa_o_modelo_de_1_a_8_bilhoes(
    nome: str, familias: list[str], tamanho: str | None, serve: bool
) -> None:
    assert serve_para_a_conversa(nome, familias, parametros(tamanho)) is serve


def test_memoria_junta_palavra_e_significado(servicos: Servicos) -> None:
    b = servicos.banco
    c = b.criar_conversa("Reunião")
    a = b.acrescentar_audio(c, "r.mp3", "r.mp3", "large-v3-turbo", "pt")
    t1 = b.gravar_trecho(a, 1, 0, 5, "O frete para Manzanillo subiu", 0.5)
    t2 = b.gravar_trecho(a, 2, 5, 9, "Falamos de outra coisa", 1.0)
    b.gravar_vetores({t1: [0.0, 1.0], t2: [1.0, 0.0]})
    trechos, com_significado = memoria.buscar(b, ollama_falso(vetor=[1.0, 0.0]), "nomic", "frete Manzanillo")
    assert com_significado is True
    assert {t["id"] for t in trechos} == {t1, t2}  # t1 pela palavra, t2 pelo significado
    assert memoria.fontes_numeradas(trechos)[0]["n"] == 1


# ── rotas ────────────────────────────────────────────────────────────────────────────────────
def test_protecao_recusa_outro_host_outra_origem_e_escrita_sem_o_cabecalho(cliente: TestClient) -> None:
    assert cliente.get("/health").status_code == 200
    assert cliente.get("/health", headers={"Host": f"evil.test:{PORTA}"}).status_code == 403
    assert cliente.post("/api/conversas").status_code == 403
    assert cliente.post("/api/conversas", headers={"X-Echo": "1", "Origin": "http://evil.test"}).status_code == 403
    assert cliente.post("/api/conversas", headers=ESCRITA).status_code == 200


def wav_de_teste() -> bytes:
    """Um WAV de verdade (0,2 s de um tom de 440 Hz, 16 kHz mono): o formato e a onda saem do PyAV real."""
    saida = io.BytesIO()
    with wave.open(saida, "wb") as arquivo:
        arquivo.setnchannels(1)
        arquivo.setsampwidth(2)
        arquivo.setframerate(16000)
        tom = (int(12000 * math.sin(2 * math.pi * 440 * i / 16000)) for i in range(3200))
        arquivo.writeframes(b"".join(struct.pack("<h", amostra) for amostra in tom))
    return saida.getvalue()


def enviar(cliente: TestClient, nome: str, conteudo: bytes, modelo: str = "large-v3-turbo") -> Any:
    c = cliente.post("/api/conversas", headers=ESCRITA).json()["id"]
    return c, cliente.post(
        f"/api/conversas/{c}/audios",
        headers=ESCRITA,
        data={"modelo": modelo, "idioma": "pt"},
        files={"arquivo": (nome, conteudo, "application/octet-stream")},
    )


def test_enviar_audio_guarda_com_nome_do_servidor_e_poe_na_fila(cliente: TestClient, servicos: Servicos) -> None:
    conteudo = wav_de_teste()
    c, r = enviar(cliente, "../../minha reunião.m4a", conteudo)
    assert r.status_code == 200, r.text
    audio = r.json()
    assert audio["estado"] == "na_fila" and audio["nome_original"] == "minha reunião.m4a"
    assert (servicos.config.pasta_audios / audio["arquivo"]).read_bytes() == conteudo
    assert "/" not in audio["arquivo"] and audio["arquivo"].endswith(".m4a")
    assert servicos.banco.conversa(c)["titulo"] == "minha reunião"  # type: ignore[index]


def test_arquivo_com_audio_entra_qualquer_que_seja_o_nome(cliente: TestClient, servicos: Servicos) -> None:
    _c, r = enviar(cliente, "gravacao do celular.bin", wav_de_teste())
    assert r.status_code == 200, r.text
    assert (servicos.config.pasta_audios / r.json()["arquivo"]).is_file()


def test_arquivo_sem_audio_e_recusado_mesmo_com_nome_de_mp3(cliente: TestClient, servicos: Servicos) -> None:
    _c, r = enviar(cliente, "reuniao.mp3", "isto é um texto, não um áudio".encode())
    assert r.status_code == 415 and "não tem áudio" in r.json()["erro"]
    assert list(servicos.config.pasta_audios.iterdir()) == []


@pytest.mark.parametrize(
    "nome,modelo,esperado", [("x.exe", "large-v3-turbo", 415), ("x.mp3", "large-v3", 422), ("x.mp3", "inventado", 422)]
)
def test_enviar_audio_recusa_formato_e_modelo_nao_baixado(cliente: TestClient, nome: str, modelo: str, esperado: int) -> None:
    c = cliente.post("/api/conversas", headers=ESCRITA).json()["id"]
    r = cliente.post(
        f"/api/conversas/{c}/audios",
        headers=ESCRITA,
        data={"modelo": modelo, "idioma": "pt"},
        files={"arquivo": (nome, b"abc", "application/octet-stream")},
    )
    assert r.status_code == esperado


def test_eventos_de_audio_pronto_mandam_o_estado_e_fecham(cliente: TestClient, servicos: Servicos) -> None:
    b = servicos.banco
    c = b.criar_conversa()
    a = b.acrescentar_audio(c, "x.mp3", por_audio(servicos, "x.mp3"), "large-v3-turbo", "pt")
    servicos.fila.transcrever(b.proximo_da_fila())  # type: ignore[arg-type]
    corpo = cliente.get(f"/api/audios/{a}/eventos").text
    assert json.loads(corpo.split("data: ", 1)[1].split("\n")[0])["estado"] == "pronto"


def test_perguntar_responde_sem_o_pensamento_e_grava_a_conversa(cliente: TestClient, servicos: Servicos) -> None:
    b = servicos.banco
    c = b.criar_conversa()
    a = b.acrescentar_audio(c, "x.mp3", por_audio(servicos, "x.mp3"), "large-v3-turbo", "pt")
    servicos.fila.transcrever(b.proximo_da_fila())  # type: ignore[arg-type]
    linhas = [
        json.loads(x)
        for x in cliente.post(
            f"/api/conversas/{c}/perguntar", headers=ESCRITA, json={"pergunta": "resuma", "modo": "conversa"}
        ).text.splitlines()
    ]
    assert "".join(x["texto"] for x in linhas if x["tipo"] == "texto") == "Resumo [1]."
    assert linhas[-1]["tipo"] == "fim"
    assert [m["papel"] for m in b.mensagens(c)] == ["audio", "usuario", "assistente"]
    assert b.audio(a)["estado"] == "pronto"  # type: ignore[index]


def test_perguntar_com_ia_de_30_bilhoes_e_recusado_sem_gravar_a_pergunta(cliente: TestClient, servicos: Servicos) -> None:
    c = servicos.banco.criar_conversa()
    r = cliente.post(
        f"/api/conversas/{c}/perguntar",
        headers=ESCRITA,
        json={"pergunta": "resuma", "modo": "conversa", "modelo": "qwen3:30b-a3b"},
    )
    assert r.status_code == 422 and "1 a 8 bilhões" in r.json()["erro"]
    assert servicos.banco.mensagens(c) == []


def test_baixar_texto_e_legenda(cliente: TestClient, servicos: Servicos) -> None:
    b = servicos.banco
    c = b.criar_conversa()
    a = b.acrescentar_audio(c, "Reunião.mp3", por_audio(servicos, "x.mp3"), "large-v3-turbo", "pt")
    servicos.fila.transcrever(b.proximo_da_fila())  # type: ignore[arg-type]
    assert cliente.get(f"/api/audios/{a}/texto.txt").text == "Bom dia, equipe.\nO contrato foi ajustado hoje.\n"
    legenda = cliente.get(f"/api/audios/{a}/texto.srt")
    assert legenda.text.startswith("1\n00:00:00,000 --> 00:00:04,000\nBom dia, equipe.")
    assert "filename*=UTF-8''Reuni%C3%A3o.srt" in legenda.headers["content-disposition"]


def test_srt_com_hora() -> None:
    assert srt([{"inicio": 3723.5, "fim": 3724.25, "texto": "x"}]) == "1\n01:02:03,500 --> 01:02:04,250\nx\n"


def test_voz_sem_piper_diz_o_motivo(cliente: TestClient) -> None:
    r = cliente.post("/api/voz", headers=ESCRITA, json={"texto": "oi"})
    assert r.status_code == 503 and "Piper" in r.json()["erro"]


def piper_falsa(pasta: Path, corpo: str) -> Path:
    """Um executável no lugar da Piper: o teste roda o subprocess de verdade, só troca o programa chamado."""
    programa = pasta / "piper"
    programa.write_text("#!/usr/bin/env bash\n" + corpo, encoding="utf-8")
    programa.chmod(0o755)
    return programa


def test_voz_que_falha_diz_o_motivo_e_nao_deixa_arquivo(tmp_path: Path) -> None:
    piper = piper_falsa(tmp_path, 'echo "modelo corrompido" >&2\nexit 1\n')
    with pytest.raises(FalhaDaVoz, match="modelo corrompido"):
        falar("bom dia", piper, tmp_path, "faber", tmp_path)
    assert list(tmp_path.glob("*.wav")) == []


def test_voz_guarda_o_audio_e_nao_gera_duas_vezes(tmp_path: Path) -> None:
    contador = tmp_path / "chamadas"
    piper = piper_falsa(
        tmp_path, f'while [ "$1" != "--output_file" ]; do shift; done\nprintf RIFF > "$2"\necho x >> "{contador}"\n'
    )
    primeiro = falar("bom dia [1]", piper, tmp_path, "faber", tmp_path)
    segundo = falar("bom dia", piper, tmp_path, "faber", tmp_path)
    assert primeiro == segundo and primeiro.read_bytes() == b"RIFF"
    assert contador.read_text(encoding="utf-8").count("x") == 1


def test_ajuste_desconhecido_e_recusado(cliente: TestClient) -> None:
    assert cliente.post("/api/ajustes", headers=ESCRITA, json={"cor": "azul"}).status_code == 422
    assert cliente.post("/api/ajustes", headers=ESCRITA, json={"whisper_modelo": "small"}).json()["whisper_modelo"] == "small"


def test_apagar_nao_sai_da_pasta_dos_audios(cliente: TestClient, servicos: Servicos, tmp_path: Path) -> None:
    fora = tmp_path / "importante.txt"
    fora.write_text("não apagar", encoding="utf-8")
    b = servicos.banco
    c = b.criar_conversa()
    b.acrescentar_audio(c, "x.mp3", "../../importante.txt", "large-v3-turbo", "pt")  # dados/audios -> tmp_path
    assert cliente.delete(f"/api/conversas/{c}", headers=ESCRITA).json() == {"arquivos_apagados": 0}
    assert fora.read_text(encoding="utf-8") == "não apagar"


# ── downloads, IA e motor ────────────────────────────────────────────────────────────────────
def test_instalar_ia_baixa_o_modelo_com_o_andamento_somando_as_camadas(cliente: TestClient, servicos: Servicos) -> None:
    puxados: list[str] = []
    servicos.ollama = ollama_falso(puxados=puxados)
    r = cliente.post("/api/ia/qwen2.5:7b/instalar", headers=ESCRITA)
    servicos.downloads.esperar(5)
    andamento = cliente.get("/api/downloads").json()
    assert r.status_code == 200 and puxados == ["qwen2.5:7b"]  # a busca (nomic-embed-text) já estava instalada
    assert [(a["nome"], a["estado"], a["fase"], a["baixado"], a["total"]) for a in andamento] == [
        ("qwen2.5:7b", "pronto", "modelo", 400, 400)
    ]


def test_ia_fora_da_lista_do_app_nao_instala(cliente: TestClient) -> None:
    assert cliente.post("/api/ia/qwen3:30b-a3b/instalar", headers=ESCRITA).status_code == 404


def test_perguntar_sem_ia_instalada_pede_para_instalar_a_recomendada(cliente: TestClient, servicos: Servicos) -> None:
    servicos.ollama = ollama_falso(vazio=True)
    c = servicos.banco.criar_conversa()
    r = cliente.post(f"/api/conversas/{c}/perguntar", headers=ESCRITA, json={"pergunta": "resuma", "modo": "conversa"})
    assert r.status_code == 409 and "qwen2.5:7b" in r.json()["erro"]
    assert servicos.banco.mensagens(c) == []


def test_baixar_whisper_mostra_o_andamento(
    cliente: TestClient, servicos: Servicos, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    hugging_face_falso(monkeypatch, tmp_path)
    assert cliente.post("/api/modelos/medium/baixar", headers=ESCRITA).json() == {"estado": "baixando"}
    servicos.downloads.esperar(5)
    [andamento] = cliente.get("/api/downloads").json()
    assert (andamento["nome"], andamento["estado"], andamento["baixado"], andamento["total"]) == ("medium", "pronto", 1010, 1010)


def test_download_que_falha_mostra_o_motivo(
    cliente: TestClient, servicos: Servicos, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    hugging_face_falso(monkeypatch, tmp_path, falhar=True)
    cliente.post("/api/modelos/medium/baixar", headers=ESCRITA)
    servicos.downloads.esperar(5)
    [andamento] = cliente.get("/api/downloads").json()
    assert andamento["estado"] == "erro" and "caiu no meio" in andamento["erro"]


def test_estado_compara_os_modelos_e_marca_o_recomendado_para_a_maquina(cliente: TestClient) -> None:
    estado = cliente.get("/api/estado").json()
    ias = {m["nome"]: m for m in estado["catalogo_de_ia"]}
    whisper = {m["nome"]: m for m in estado["modelos_whisper"]}
    assert [n for n, m in ias.items() if m["recomendado"]] == ["qwen2.5:7b"]
    assert all(1 <= m["qualidade"] <= 5 and 1 <= m["velocidade"] <= 5 and 1 <= m["bilhoes"] <= 8 for m in ias.values())
    assert [n for n, m in whisper.items() if m["recomendado"]] == ["small"]
    assert whisper["large-v3"]["precisao"] == 5 and estado["motor_ia"] == "sistema"
    assert estado["app"]["nome"] == "Echo-AI"


@pytest.mark.parametrize(
    ("sistema", "maquina", "esperado"),
    [
        ("win32", "AMD64", "ollama-windows-amd64.zip"),
        ("darwin", "arm64", "ollama-darwin.tgz"),
        ("linux", "x86_64", "ollama-linux-amd64.tar.zst"),
        ("linux", "aarch64", "ollama-linux-arm64.tar.zst"),
    ],
)
def test_o_pacote_do_motor_e_o_do_sistema(monkeypatch: pytest.MonkeyPatch, sistema: str, maquina: str, esperado: str) -> None:
    monkeypatch.setattr(motor_ia.sys, "platform", sistema)
    monkeypatch.setattr(motor_ia.platform, "machine", lambda: maquina)
    assert motor_ia.pacote_do_sistema() == esperado


def test_pacote_do_motor_que_nao_confere_com_a_soma_e_recusado_e_apagado(tmp_path: Path) -> None:
    pacote = motor_ia.pacote_do_sistema()

    def github(pedido: httpx.Request) -> httpx.Response:  # a página de versões do Ollama é a fronteira
        if pedido.url.path.endswith("sha256sum.txt"):
            return httpx.Response(200, text=f"{'0' * 64}  ./{pacote}\n")
        return httpx.Response(200, content=b"um pacote adulterado")

    motor = motor_ia.MotorDeIA(
        tmp_path / "motor", "http://sem-ollama.teste", cliente=httpx.Client(transport=httpx.MockTransport(github))
    )
    with pytest.raises(ValueError, match="não confere"):
        motor.instalar(lambda baixado, total: None)
    assert list((tmp_path / "motor").iterdir()) == [] and motor.executavel is None


def test_zip_que_tenta_gravar_fora_da_pasta_e_recusado(tmp_path: Path) -> None:
    pacote = tmp_path / "malicioso.zip"
    with zipfile.ZipFile(pacote, "w") as arquivo:
        arquivo.writestr("../../fora.txt", "não")
    with pytest.raises(ValueError, match="fora da pasta"):
        motor_ia._extrair(pacote, tmp_path / "destino")
    assert not (tmp_path.parent / "fora.txt").exists()


def wheel_falso(pasta: Path, nome: str, bibliotecas: list[str]) -> Path:
    arquivo = pasta / nome
    with zipfile.ZipFile(arquivo, "w") as wheel:
        for caminho in bibliotecas:
            wheel.writestr(caminho, b"biblioteca")
        wheel.writestr("nvidia_cublas_cu12-12.9.2.10.dist-info/METADATA", "x")
    return arquivo


def pypi_falso(tmp_path: Path, adulterar: bool = False) -> httpx.Client:
    """O PyPI é a fronteira: o JSON de cada pacote e o download do wheel, com a SHA-256 de verdade dos bytes."""
    wheels = {
        "nvidia-cublas-cu12": wheel_falso(
            tmp_path, "cublas.whl", ["nvidia/cublas/lib/libcublas.so.12", "nvidia/cublas/include/x.h"]
        ),
        "nvidia-cudnn-cu12": wheel_falso(tmp_path, "cudnn.whl", ["nvidia/cudnn/lib/libcudnn.so.9"]),
        "nvidia-cuda-nvrtc-cu12": wheel_falso(tmp_path, "nvrtc.whl", ["nvidia/cuda_nvrtc/lib/libnvrtc.so.12"]),
    }

    def responder(pedido: httpx.Request) -> httpx.Response:
        if pedido.url.host == "pypi.org":
            pacote = pedido.url.path.split("/")[2]
            conteudo = wheels[pacote].read_bytes()
            soma = "0" * 64 if adulterar else hashlib.sha256(conteudo).hexdigest()
            arquivo = {
                "filename": f"{pacote}-py3-none-manylinux_2_27_x86_64.whl",
                "url": f"https://files.teste/{pacote}.whl",
                "size": len(conteudo),
                "digests": {"sha256": soma},
            }
            return httpx.Response(200, json={"urls": [arquivo]})
        pacote = pedido.url.path.strip("/").removesuffix(".whl")
        return httpx.Response(200, content=wheels[pacote].read_bytes())

    return httpx.Client(transport=httpx.MockTransport(responder))


def test_aceleracao_baixa_confere_e_extrai_so_as_bibliotecas(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(aceleracao.sys, "platform", "linux")
    monkeypatch.setattr(aceleracao.platform, "machine", lambda: "x86_64")
    pasta = tmp_path / "aceleracao"
    andamento: list[tuple[int, int]] = []
    Aceleracao(pasta, cliente=pypi_falso(tmp_path)).instalar(lambda b, t: andamento.append((b, t)))
    assert aceleracao.instalada(pasta)
    assert not (pasta / "nvidia" / "cublas" / "include").exists()  # só a pasta das bibliotecas
    assert andamento[-1][0] == andamento[-1][1] and not list(pasta.glob("*.parcial"))


def test_aceleracao_que_nao_confere_com_o_pypi_e_recusada(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(aceleracao.sys, "platform", "linux")
    monkeypatch.setattr(aceleracao.platform, "machine", lambda: "x86_64")
    pasta = tmp_path / "aceleracao"
    with pytest.raises(ValueError, match="não confere"):
        Aceleracao(pasta, cliente=pypi_falso(tmp_path, adulterar=True)).instalar(lambda b, t: None)
    assert not aceleracao.instalada(pasta) and not list(pasta.glob("*.parcial"))


def test_mac_nao_tem_pacote_de_aceleracao(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(aceleracao.sys, "platform", "darwin")
    with pytest.raises(ValueError, match="CPU"):
        Aceleracao(tmp_path, cliente=pypi_falso(tmp_path)).instalar(lambda b, t: None)


def test_instalar_aceleracao_sem_precisar_e_recusado(cliente: TestClient) -> None:
    assert cliente.post("/api/aceleracao/instalar", headers=ESCRITA).status_code == 409


@pytest.mark.parametrize(("idioma", "esperado"), [("en", "English"), ("es", "Spanish"), (None, "the same language as the user")])
def test_a_ia_responde_no_idioma_da_tela(idioma: str | None, esperado: str) -> None:
    [sistema, pergunta] = memoria.mensagens_para_o_ollama("resuma", [], "Bom dia, equipe.", idioma)
    assert f"Always write your answer in {esperado}" in sistema["content"] and "Bom dia, equipe." in sistema["content"]
    assert pergunta == {"role": "user", "content": "resuma"}


def test_idioma_da_resposta_fora_da_lista_e_recusado(cliente: TestClient, servicos: Servicos) -> None:
    c = servicos.banco.criar_conversa()
    r = cliente.post(f"/api/conversas/{c}/perguntar", headers=ESCRITA, json={"pergunta": "resuma", "idioma": "klingon"})
    assert r.status_code == 422 and servicos.banco.mensagens(c) == []


def test_catalogo_so_tem_modelos_que_o_faster_whisper_conhece() -> None:
    from faster_whisper.utils import _MODELS

    assert tr.NOMES_DO_CATALOGO <= set(_MODELS)


# ── achados da banca (29/09): fronteiras, guardas e fluxos que não tinham teste ─────────────────
@dataclass
class ArquivoDoRepo:
    rfilename: str
    size: int


def hugging_face_falso(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    falhar: bool = False,
    arquivos_no_snapshot: tuple[str, ...] = ("model.bin", "config.json", "tokenizer.json"),
) -> Path:
    """O Hugging Face é a fronteira: a lista de arquivos do repositório e o download, gravando no cache do teste."""
    cache = tmp_path / "hf"
    arquivos = [ArquivoDoRepo("model.bin", 1000), ArquivoDoRepo("config.json", 10), ArquivoDoRepo("README.md", 5000)]
    monkeypatch.setattr("huggingface_hub.constants.HF_HUB_CACHE", str(cache))
    monkeypatch.setattr(
        "huggingface_hub.HfApi",
        lambda: SimpleNamespace(model_info=lambda repo, revision, files_metadata: SimpleNamespace(siblings=arquivos)),
    )

    def baixar(repositorio: str, revision: str, allow_patterns: list[str]) -> str:
        pasta = cache / ("models--" + repositorio.replace("/", "--"))
        (pasta / "blobs").mkdir(parents=True, exist_ok=True)
        (pasta / "blobs" / "modelo.incomplete").write_bytes(b"x" * (500 if falhar else 1010))
        if falhar:
            raise OSError("a conexão caiu no meio")
        snapshot = pasta / "snapshots" / revision
        snapshot.mkdir(parents=True)
        for arquivo in arquivos_no_snapshot:
            (snapshot / arquivo).write_bytes(b"x")
        return str(snapshot)

    monkeypatch.setattr("huggingface_hub.snapshot_download", baixar)
    return cache


def test_baixar_modelo_so_conta_os_arquivos_do_modelo(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    hugging_face_falso(monkeypatch, tmp_path)
    vistos: list[tuple[int, int]] = []
    tr.baixar_modelo("tiny", lambda b, t: vistos.append((b, t)))
    assert vistos[-1] == (1010, 1010)  # model.bin + config.json; o README fica de fora


def test_baixar_modelo_fora_do_catalogo_e_recusado() -> None:
    with pytest.raises(ValueError, match="fora do catálogo"):
        tr.baixar_modelo("inventado", lambda b, t: None)


def test_modelo_incluido_no_instalador_conta_sem_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / "modelos-incluidos" / "small").mkdir(parents=True)
    (tmp_path / "modelos-incluidos" / "small" / "model.bin").write_bytes(b"modelo")

    monkeypatch.setattr("huggingface_hub.try_to_load_from_cache", lambda repositorio, arquivo, revision=None: None)
    assert tr.modelos_baixados() == {"small"}
    assert tr.modelos_baixados(frozenset({"small"})) == set()  # o que está baixando agora não conta


def test_download_que_caiu_no_meio_nao_conta_como_baixado(monkeypatch: pytest.MonkeyPatch) -> None:
    turbo = tr.repositorio_do_modelo("large-v3-turbo")

    def so_o_config(repositorio: str, arquivo: str, revision: str | None = None) -> str | None:  # o model.bin não chegou
        return f"/cache/{repositorio}/{arquivo}" if repositorio == turbo and arquivo == "config.json" else None

    monkeypatch.setattr("huggingface_hub.try_to_load_from_cache", so_o_config)
    assert "large-v3-turbo" not in tr.modelos_baixados()


def test_apagar_conversa_apaga_o_audio_do_disco(cliente: TestClient, servicos: Servicos) -> None:
    c, r = enviar(cliente, "reuniao.wav", wav_de_teste())
    arquivo = servicos.config.pasta_audios / r.json()["arquivo"]
    assert arquivo.is_file()
    assert cliente.delete(f"/api/conversas/{c}", headers=ESCRITA).json() == {"arquivos_apagados": 1}
    assert not arquivo.exists()


def test_config_recusa_escutar_fora_do_proprio_computador(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="127.0.0.1"):
        Config(pasta_dados=tmp_path / "a", host="0.0.0.0").preparar()
    Config(pasta_dados=tmp_path / "b", host="localhost").preparar()


def test_protecao_vale_para_apagar_e_renomear_e_manda_os_cabecalhos(cliente: TestClient, servicos: Servicos) -> None:
    c = servicos.banco.criar_conversa()
    sem_cabecalho = {"Origin": "http://" + HOST}
    assert cliente.delete(f"/api/conversas/{c}", headers=sem_cabecalho).status_code == 403
    assert cliente.patch(f"/api/conversas/{c}", headers=sem_cabecalho, json={"titulo": "outro"}).status_code == 403
    assert cliente.delete(f"/api/conversas/{c}", headers={"X-Echo": "0"}).status_code == 403
    assert cliente.post("/api/conversas", headers={"X-Echo": "1", "Origin": "null"}).status_code == 403
    assert servicos.banco.conversa(c) is not None
    cabecalhos = cliente.get("/health").headers
    assert "frame-ancestors 'none'" in cabecalhos["content-security-policy"] and cabecalhos["x-content-type-options"] == "nosniff"
    assert TestClient(criar_app(servicos), base_url=f"http://localhost:{PORTA}").get("/health").status_code == 200


def test_audio_maior_que_o_limite_e_recusado_e_apagado(cliente: TestClient, servicos: Servicos) -> None:
    servicos.config.tamanho_maximo_do_audio_mb = 1
    _c, r = enviar(cliente, "grande.wav", wav_de_teste() + b"\0" * (1024 * 1024))
    assert r.status_code == 413 and list(servicos.config.pasta_audios.iterdir()) == []


def test_arquivo_do_audio_fora_da_pasta_nao_e_servido(cliente: TestClient, servicos: Servicos, tmp_path: Path) -> None:
    (tmp_path / "segredo.txt").write_text("não servir", encoding="utf-8")
    a = servicos.banco.acrescentar_audio(servicos.banco.criar_conversa(), "x.mp3", "../../segredo.txt", "small", "pt")
    assert cliente.get(f"/api/audios/{a}/arquivo").status_code == 404


@pytest.mark.parametrize(("idioma", "esperado"), [("auto", None), ("pt", "pt")])
def test_idioma_auto_deixa_o_whisper_descobrir(servicos: Servicos, idioma: str, esperado: str | None) -> None:
    motor = MotorFalso([TrechoFalso(0.0, 1.0, "Olá.")])
    servicos.placa.carregar = lambda n, d, p: motor
    b = servicos.banco
    b.acrescentar_audio(b.criar_conversa(), "x.wav", por_audio(servicos, "x.wav"), "small", idioma)
    servicos.fila.transcrever(b.proximo_da_fila())  # type: ignore[arg-type]
    assert motor.recebido[1]["language"] == esperado


def test_a_fila_tira_a_ia_da_placa_antes_de_transcrever(servicos: Servicos) -> None:
    b = servicos.banco
    b.acrescentar_audio(b.criar_conversa(), "x.wav", por_audio(servicos, "x.wav"), "small", "pt")
    servicos.fila.transcrever(b.proximo_da_fila())  # type: ignore[arg-type]
    assert servicos.liberados_do_ollama == ["qwen2.5:1.5b"]  # type: ignore[attr-defined]


def ollama_que_responde(respostas: dict[str, httpx.Response | Exception]) -> Ollama:
    def responder(pedido: httpx.Request) -> httpx.Response:
        resposta = respostas.get(pedido.url.path, httpx.Response(404))
        if isinstance(resposta, Exception):
            raise resposta
        return resposta

    return Ollama("http://ollama.teste", transporte=httpx.MockTransport(responder))


def linhas(*pedacos: dict[str, Any]) -> bytes:
    return ("\n".join(json.dumps(p) for p in pedacos) + "\n").encode()


@pytest.mark.parametrize(
    ("resposta", "motivo"),
    [
        (httpx.Response(500, text="sem espaço"), "recusou"),
        (httpx.Response(200, content=linhas({"status": "pulling"}, {"error": "manifesto não existe"})), "manifesto"),
        (httpx.Response(200, content=linhas({"status": "pulling", "digest": "a", "total": 10, "completed": 5})), "parou antes"),
        (httpx.ConnectError("caiu"), "caiu"),
    ],
)
def test_download_da_ia_que_nao_termina_e_erro(resposta: httpx.Response | Exception, motivo: str) -> None:
    with pytest.raises(OllamaFora, match=motivo):
        ollama_que_responde({"/api/pull": resposta}).baixar("qwen2.5:7b", lambda b, t: None)


def test_linha_quebrada_do_ollama_vira_erro_na_resposta(cliente: TestClient, servicos: Servicos) -> None:
    chat = httpx.Response(200, content=b'{"message": {"content": "Oi"}, "done": false}\n{quebrado\n')
    tags = httpx.Response(
        200, json={"models": [{"name": "qwen2.5:1.5b", "details": {"families": ["qwen2"], "parameter_size": "1.5B"}}]}
    )
    servicos.ollama = ollama_que_responde({"/api/chat": chat, "/api/tags": tags})
    c = servicos.banco.criar_conversa()
    corpo = cliente.post(f"/api/conversas/{c}/perguntar", headers=ESCRITA, json={"pergunta": "oi", "modo": "historico"})
    eventos = [json.loads(x)["tipo"] for x in corpo.text.splitlines()]
    assert eventos[-1] == "erro"


class QuebraNoMeio(httpx.SyncByteStream):
    def __iter__(self) -> Iterator[bytes]:
        yield b"o comeco do pacote"
        raise httpx.ReadError("a conexão caiu")


def test_pacote_do_motor_que_cai_no_meio_nao_deixa_parcial_nem_motor(tmp_path: Path) -> None:
    anterior = tmp_path / "motor" / "modelos" / "ja-baixado"
    anterior.parent.mkdir(parents=True)
    anterior.write_text("os modelos de antes ficam", encoding="utf-8")

    def github(pedido: httpx.Request) -> httpx.Response:
        if pedido.url.path.endswith("sha256sum.txt"):
            return httpx.Response(200, text=f"{'0' * 64}  ./{motor_ia.pacote_do_sistema()}\n")
        return httpx.Response(200, stream=QuebraNoMeio())

    motor = motor_ia.MotorDeIA(
        tmp_path / "motor", "http://sem-ollama.teste", cliente=httpx.Client(transport=httpx.MockTransport(github))
    )
    with pytest.raises(httpx.ReadError):
        motor.instalar(lambda b, t: None)
    assert not list((tmp_path / "motor").glob("*.parcial")) and motor.executavel is None
    assert anterior.read_text(encoding="utf-8") == "os modelos de antes ficam"


def test_aceleracao_que_cai_no_segundo_pacote_nao_fica_instalada(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(aceleracao.sys, "platform", "linux")
    monkeypatch.setattr(aceleracao.platform, "machine", lambda: "x86_64")
    bom = pypi_falso(tmp_path)

    def quebra_no_cudnn(pedido: httpx.Request) -> httpx.Response:
        if pedido.url.host == "files.teste" and "cudnn" in pedido.url.path:
            return httpx.Response(200, stream=QuebraNoMeio())
        return bom._transport.handle_request(pedido)

    pasta = tmp_path / "aceleracao"
    with pytest.raises(httpx.ReadError):
        Aceleracao(pasta, cliente=httpx.Client(transport=httpx.MockTransport(quebra_no_cudnn))).instalar(lambda b, t: None)
    assert not aceleracao.instalada(pasta) and not list(pasta.glob("*.parcial"))


def test_wheel_que_tenta_gravar_fora_da_pasta_e_recusado(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(aceleracao.sys, "platform", "linux")
    wheel = wheel_falso(tmp_path, "mau.whl", ["nvidia/x/lib/../../../../fora.so"])
    with pytest.raises(ValueError, match="fora da pasta"):
        Aceleracao(tmp_path / "aceleracao")._extrair_bibliotecas(wheel)
    assert not (tmp_path / "fora.so").exists()


def test_tar_do_motor_que_tenta_gravar_fora_da_pasta_e_recusado(tmp_path: Path) -> None:
    pacote = tmp_path / "mau.tgz"
    with tarfile.open(pacote, "w:gz") as arquivo:
        dado = b"nao"
        info = tarfile.TarInfo("../fora.txt")
        info.size = len(dado)
        arquivo.addfile(info, io.BytesIO(dado))
    with pytest.raises(tarfile.FilterError):
        motor_ia._extrair(pacote, tmp_path / "destino")
    assert not (tmp_path / "fora.txt").exists()


def sem_o_pacote_nvidia(monkeypatch: pytest.MonkeyPatch) -> None:
    """O ambiente sem o pacote nvidia do pip (o app instalado, o CI): o find_spec levanta, como na vida real."""
    original = importlib.util.find_spec

    def sem_nvidia(nome: str, *argumentos: Any, **opcoes: Any) -> Any:
        if nome.startswith("nvidia"):
            raise ModuleNotFoundError("No module named 'nvidia'")
        return original(nome, *argumentos, **opcoes)

    monkeypatch.setattr(importlib.util, "find_spec", sem_nvidia)


def nvidia_smi_falso(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """O nvidia-smi é programa do sistema (fronteira): um falso no PATH, respondendo como uma placa de 6 GB."""
    pasta = tmp_path / "bin"
    pasta.mkdir()
    programa = pasta / "nvidia-smi"
    programa.write_text("#!/bin/sh\necho 'NVIDIA Placa de Teste, 6144'\n", encoding="utf-8")
    programa.chmod(0o755)
    monkeypatch.setenv("PATH", str(pasta))
    monkeypatch.setattr("ctranslate2.get_cuda_device_count", lambda: 1)
    monkeypatch.setattr("ctranslate2.get_supported_compute_types", lambda dispositivo: {"float16", "int8"})


def test_aceleracao_instalada_passa_a_transcrever_na_placa(
    cliente: TestClient, servicos: Servicos, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(aceleracao.sys, "platform", "linux")
    monkeypatch.setattr(aceleracao.platform, "machine", lambda: "x86_64")
    nvidia_smi_falso(tmp_path, monkeypatch)
    sem_o_pacote_nvidia(monkeypatch)  # como no app instalado: só o pacote baixado pela tela liga a placa
    servicos.maquina = replace(MAQUINA_DE_TESTE, aceleracao_pendente=True)
    servicos.config.pasta_dados = tmp_path / "dados"
    servicos.aceleracao = Aceleracao(servicos.config.pasta_da_aceleracao, cliente=pypi_falso(tmp_path))
    assert cliente.post("/api/aceleracao/instalar", headers=ESCRITA).status_code == 200
    servicos.downloads.esperar(5)
    assert (servicos.placa.dispositivo, servicos.fila.precisao, servicos.placa.motivo_da_cpu) == ("cuda", "float16", None)
    assert servicos.maquina.dispositivo == "cuda" and not servicos.maquina.aceleracao_pendente


def test_sem_as_bibliotecas_da_nvidia_a_deteccao_cai_para_a_cpu(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """O .deb instalado num Ubuntu limpo não tem o pacote nvidia: a detecção não pode quebrar (achado em 29/09)."""
    sem_o_pacote_nvidia(monkeypatch)
    monkeypatch.setenv("PATH", str(tmp_path))  # sem nvidia-smi
    maquina = hardware.detectar(tmp_path / "aceleracao")
    assert (maquina.dispositivo, maquina.aceleracao_pendente) == ("cpu", False)
    tr.preparar_bibliotecas_da_placa([])  # sem o pacote, carregar a placa também não quebra


def motor_empacotado(tmp_path: Path, quebrado_no_meio: bool = False) -> bytes:
    """Um .tar.zst como o do Ollama para Linux, com um 'ollama' que só espera (o motor de verdade é fronteira).
    `quebrado_no_meio`: depois do binário vem um membro que a extração recusa, como um download corrompido."""
    import zstandard

    bruto = io.BytesIO()
    with tarfile.open(fileobj=bruto, mode="w") as arquivo:
        dado = b'#!/bin/sh\necho "$1 $OLLAMA_HOST $OLLAMA_MODELS" > "$(dirname "$OLLAMA_MODELS")/marca"\nexec sleep 30\n'
        info = tarfile.TarInfo("bin/ollama")
        info.size, info.mode = len(dado), 0o755
        arquivo.addfile(info, io.BytesIO(dado))
        if quebrado_no_meio:
            fora = tarfile.TarInfo("../fora.txt")
            fora.size = 1
            arquivo.addfile(fora, io.BytesIO(b"x"))
    return zstandard.ZstdCompressor().compress(bruto.getvalue())


def test_instalar_ia_sem_motor_instala_o_motor_antes_e_liga_na_porta_propria(
    cliente: TestClient, servicos: Servicos, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(motor_ia.sys, "platform", "linux")
    monkeypatch.setattr(motor_ia.platform, "machine", lambda: "x86_64")
    pacote = motor_empacotado(tmp_path)
    marca = tmp_path / "motor" / "marca"

    def rede(pedido: httpx.Request) -> httpx.Response:
        if pedido.url.host == "sem-ollama.teste":
            raise httpx.ConnectError("o Ollama do sistema não existe")
        if pedido.url.path.endswith("sha256sum.txt"):
            return httpx.Response(200, text=f"{hashlib.sha256(pacote).hexdigest()}  ./ollama-linux-amd64.tar.zst\n")
        if pedido.url.host == "github.com":
            return httpx.Response(200, content=pacote)
        if pedido.url.port == motor_ia.PORTA_PROPRIA and pedido.url.path == "/api/version":
            # só responde depois que o processo de verdade subiu (e gravou a marca com o ambiente que recebeu)
            return httpx.Response(200, json={"version": "0.34.4"}) if marca.exists() else httpx.Response(503)
        return httpx.Response(404)

    servicos.motor = motor_ia.MotorDeIA(
        tmp_path / "motor", "http://sem-ollama.teste", cliente=httpx.Client(transport=httpx.MockTransport(rede))
    )
    try:
        assert cliente.post("/api/ia/qwen2.5:7b/instalar", headers=ESCRITA).status_code == 200
        servicos.downloads.esperar(15)
        [andamento] = cliente.get("/api/downloads").json()
        assert (andamento["estado"], andamento["fase"]) == ("pronto", "modelo"), andamento
        assert servicos.motor.situacao() == motor_ia.PROPRIO and servicos.ollama.url == servicos.motor.url_propria
        assert marca.read_text(encoding="utf-8").split() == [
            "serve",
            f"127.0.0.1:{motor_ia.PORTA_PROPRIA}",
            str(tmp_path / "motor" / "modelos"),
        ]
        processo = servicos.motor._processo
        assert processo is not None and processo.poll() is None
    finally:
        servicos.motor.desligar()
    assert processo.poll() is not None  # desligar encerra o motor de verdade


# ── rodada 2 da banca: as correções centrais com teste de regressão ──────────────────────────
CARREGAR_DO_CACHE_REAL = hf_try_to_load_from_cache


def test_baixar_na_revisao_fixa_deixa_o_modelo_pronto_para_o_cache_de_verdade(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    hugging_face_falso(monkeypatch, tmp_path)
    monkeypatch.setattr("huggingface_hub.try_to_load_from_cache", CARREGAR_DO_CACHE_REAL)
    assert "tiny" not in tr.modelos_baixados()
    tr.baixar_modelo("tiny", lambda b, t: None)
    assert "tiny" in tr.modelos_baixados()  # o snapshot da revisão fixa (sem revisão fixa, "main" não acharia)


def test_carregar_whisper_pede_a_revisao_fixa(monkeypatch: pytest.MonkeyPatch) -> None:
    recebido: dict[str, Any] = {}

    def whisper_falso(nome: str, **opcoes: Any) -> object:  # o faster-whisper é a fronteira
        recebido.update(opcoes, nome=nome)
        return object()

    monkeypatch.setattr("faster_whisper.WhisperModel", whisper_falso)
    tr.carregar_whisper("medium", "cpu", "int8")
    assert (recebido["nome"], recebido["revision"], recebido["local_files_only"]) == ("medium", tr.REVISAO["medium"], True)


class MotorQueTravaOBanco(MotorFalso):
    """Na primeira transcrição, outro programa trava o banco no meio do caminho (backup, antivírus, outra cópia do app)."""

    def __init__(self, banco: Path) -> None:
        super().__init__([TrechoFalso(0.0, 1.0, "Olá.")])
        self.outro_programa = sqlite3.connect(banco, isolation_level=None, check_same_thread=False)
        self.chamadas, self.solto = 0, False

    def transcribe(self, audio: Any, **opcoes: Any) -> tuple[list[TrechoFalso], InfoFalsa]:
        self.chamadas += 1
        if self.chamadas == 1:
            self.outro_programa.execute("BEGIN EXCLUSIVE")
        return super().transcribe(audio, **opcoes)

    def soltar_o_banco(self) -> None:
        if self.solto:
            return
        if self.outro_programa.in_transaction:
            self.outro_programa.execute("COMMIT")
        self.outro_programa.close()
        self.solto = True


def estados_ate_o_fim(ouvinte: queue.Queue[dict[str, Any]]) -> list[str]:
    estados: list[str] = []
    while not estados or estados[-1] not in ("pronto", "erro"):
        evento = ouvinte.get(timeout=10)
        if evento["tipo"] == "estado":
            estados.append(evento["estado"])
    return estados


def test_a_fila_sobrevive_a_um_erro_do_banco_mostra_o_motivo_e_refaz_o_audio_quando_o_banco_volta(
    servicos: Servicos, cliente: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tr, "ESPERA_DEPOIS_DE_ERRO_S", 0.1)
    monkeypatch.setattr(banco_do_app, "ESPERA_PELO_BANCO_S", 0.2)
    motor = MotorQueTravaOBanco(servicos.config.banco)
    servicos.placa.carregar = lambda n, d, p: motor
    b = servicos.banco
    audio = b.acrescentar_audio(b.criar_conversa(), "x.wav", por_audio(servicos, "x.wav"), "small", "pt")
    ouvinte = servicos.avisos.ouvir(audio)
    servicos.fila.iniciar()
    try:
        assert estados_ate_o_fim(ouvinte)[-1] == "erro"  # a tela não fica em "transcrevendo" para sempre
        estado = cliente.get("/api/estado").json()
        assert servicos.fila.viva() and "locked" in (estado["fila_erro"] or "")
        motor.soltar_o_banco()
        servicos.fila.acordar()
        assert estados_ate_o_fim(ouvinte)[-1] == "pronto"  # o banco voltou: o áudio volta para a fila sem reabrir o app
        assert (b.audio(audio) or {}).get("estado") == "pronto"
        assert cliente.get("/api/estado").json()["fila_erro"] is None
    finally:
        motor.soltar_o_banco()
        servicos.fila.parar()


def test_nota_de_voz_cortada_e_recusada_como_sem_audio_e_nao_fica_na_pasta(cliente: TestClient, servicos: Servicos) -> None:
    _c, r = enviar(cliente, "nota.ogg", b"OggS" + b"\0" * 200)  # baixada pela metade
    assert r.status_code == 415 and list(servicos.config.pasta_audios.iterdir()) == []


def test_trecho_antigo_sem_vetor_ganha_vetor_com_a_fila_parada(servicos: Servicos) -> None:
    b = servicos.banco
    pronto = b.acrescentar_audio(b.criar_conversa(), "a.mp3", "a.mp3", "small", "pt")
    b.gravar_trecho(pronto, 1, 0, 1, "trecho de um áudio pronto", 1.0)
    b.concluir_audio(pronto, 1.0, "pt", 1.0)
    falhou = b.acrescentar_audio(b.criar_conversa(), "b.mp3", "b.mp3", "small", "pt")
    b.gravar_trecho(falhou, 1, 0, 1, "trecho de um áudio que falhou", 1.0)
    b.falhar_audio(falhou, "erro")
    servicos.fila._vetorizar_os_pendentes()
    assert b.trechos_sem_vetor(pronto) == [] and len(b.trechos_sem_vetor(falhou)) == 1


def test_memoria_acha_o_trecho_por_uma_pergunta_em_linguagem_natural(servicos: Servicos) -> None:
    b = servicos.banco
    a = b.acrescentar_audio(b.criar_conversa(), "r.mp3", "r.mp3", "small", "pt")
    b.gravar_trecho(a, 1, 0, 3, "O orçamento de marketing foi aprovado na reunião.", 1.0)
    sem_ia = ollama_que_responde({"/api/embed": httpx.ConnectError("fora")})
    trechos, com_significado = memoria.buscar(b, sem_ia, "nomic-embed-text", "o que foi dito sobre o orçamento?")
    assert [t["texto"] for t in trechos] == ["O orçamento de marketing foi aprovado na reunião."] and not com_significado


def conexao_recusada(pedido: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("sem Ollama")


def test_primeira_pergunta_sem_motor_nenhum_mostra_o_cartao_de_instalar(
    cliente: TestClient, servicos: Servicos, tmp_path: Path
) -> None:
    fora = httpx.MockTransport(conexao_recusada)
    servicos.ollama = Ollama("http://sem-ollama.teste", transporte=fora)
    servicos.motor = motor_ia.MotorDeIA(tmp_path / "motor-vazio", "http://sem-ollama.teste", cliente=httpx.Client(transport=fora))
    c = servicos.banco.criar_conversa()
    r = cliente.post(f"/api/conversas/{c}/perguntar", headers=ESCRITA, json={"pergunta": "resuma"})
    assert r.status_code == 409 and servicos.banco.mensagens(c) == []


def test_motor_instalado_mas_fora_do_ar_responde_503_com_o_motivo(cliente: TestClient, servicos: Servicos) -> None:
    servicos.ollama = Ollama("http://sem-ollama.teste", transporte=httpx.MockTransport(conexao_recusada))
    c = servicos.banco.criar_conversa()  # o motor "do sistema" da fixture responde /api/version, mas o Ollama caiu
    r = cliente.post(f"/api/conversas/{c}/perguntar", headers=ESCRITA, json={"pergunta": "resuma"})
    assert r.status_code == 503 and "não respondeu" in r.json()["erro"] and servicos.banco.mensagens(c) == []


def test_resposta_cortada_sem_done_vira_erro(cliente: TestClient, servicos: Servicos) -> None:
    chat = httpx.Response(200, content=b'{"message": {"content": "Metade"}, "done": false}\n')
    tags = httpx.Response(
        200, json={"models": [{"name": "qwen2.5:1.5b", "details": {"families": ["qwen2"], "parameter_size": "1.5B"}}]}
    )
    servicos.ollama = ollama_que_responde({"/api/chat": chat, "/api/tags": tags})
    c = servicos.banco.criar_conversa()
    corpo = cliente.post(f"/api/conversas/{c}/perguntar", headers=ESCRITA, json={"pergunta": "oi", "modo": "historico"})
    assert json.loads(corpo.text.splitlines()[-1])["tipo"] == "erro"


def test_tar_zst_do_motor_que_tenta_gravar_fora_da_pasta_e_recusado(tmp_path: Path) -> None:
    import zstandard

    bruto = io.BytesIO()
    with tarfile.open(fileobj=bruto, mode="w") as arquivo:
        info = tarfile.TarInfo("../fora.txt")
        info.size = 3
        arquivo.addfile(info, io.BytesIO(b"nao"))
    pacote = tmp_path / "mau.tar.zst"
    pacote.write_bytes(zstandard.ZstdCompressor().compress(bruto.getvalue()))
    with pytest.raises(tarfile.FilterError):
        motor_ia._extrair(pacote, tmp_path / "destino")
    assert not (tmp_path / "fora.txt").exists()


def test_modelo_que_esta_baixando_nao_aceita_audio(cliente: TestClient, servicos: Servicos) -> None:
    solta = threading.Event()

    def download_parado(relato: Any) -> None:
        solta.wait(5)

    servicos.downloads.iniciar("whisper", "large-v3-turbo", download_parado)
    try:
        _c, r = enviar(cliente, "x.wav", wav_de_teste())
        assert r.status_code == 422 and "não foi baixado" in r.json()["erro"]
    finally:
        solta.set()
        servicos.downloads.esperar(5)


# ── rodada 3 da banca ──────────────────────────────────────────────────────────────────
def test_a_espera_da_fila_dobra_a_cada_falha_e_para_no_teto_sem_estourar() -> None:
    assert [tr.espera_depois_de(n) for n in (1, 2, 3, 5)] == [5.0, 10.0, 20.0, 60.0]
    assert tr.espera_depois_de(5000) == 60.0  # ~17 h de falha seguida: sem teto no expoente, 2 ** 5000 estourava


@pytest.mark.parametrize(
    ("dispositivo_fixado", "esperado"), [("auto", (hardware.PLACA, "float16")), ("cpu", (hardware.CPU, "int8"))]
)
def test_a_precisao_automatica_segue_o_dispositivo_em_uso(
    tmp_path: Path, dispositivo_fixado: str, esperado: tuple[str, str]
) -> None:
    """CPU fixada numa máquina com placa não pode herdar o float16 da placa: a CPU não roda float16."""
    config = Config(
        pasta_dados=tmp_path / "dados", porta=PORTA, piper_binario=tmp_path / "sem-piper", whisper_dispositivo=dispositivo_fixado
    )
    com_placa = replace(MAQUINA_DE_TESTE, placa="NVIDIA RTX 4060", vram_gb=8.0, dispositivo=hardware.PLACA)
    montado = servidor.montar(config, com_placa)
    assert (montado.placa.dispositivo, montado.fila.precisao) == esperado


def relogio_da_fila(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """O relógio da fila, parado: o teste avança o tempo à mão."""
    agora = [1000.0]
    monkeypatch.setattr(tr, "time", SimpleNamespace(monotonic=lambda: agora[0]))
    return agora


def audio_pronto_com_trechos(banco: Banco, quantos: int) -> int:
    audio = banco.acrescentar_audio(banco.criar_conversa(), "a.mp3", "a.mp3", "small", "pt")
    for n in range(1, quantos + 1):
        banco.gravar_trecho(audio, n, float(n), float(n + 1), f"trecho {n} da reunião", 1.0)
    banco.concluir_audio(audio, float(quantos + 1), "pt", 1.0)
    return audio


def test_trechos_antigos_sem_vetor_vao_em_lotes_seguidos_ate_acabar(servicos: Servicos, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tr, "TRECHOS_POR_LOTE_DE_VETOR", 2)
    relogio_da_fila(monkeypatch)
    audio = audio_pronto_com_trechos(servicos.banco, 3)
    lotes: list[int] = []

    def vetorizar(textos: list[str]) -> list[list[float]]:
        lotes.append(len(textos))
        return [[1.0, 0.0]] * len(textos)

    servicos.fila.vetorizar = vetorizar
    servicos.fila._vetorizar_os_pendentes()
    servicos.fila._vetorizar_os_pendentes()  # o lote veio cheio: o próximo vai na volta seguinte, sem esperar o intervalo
    servicos.fila._vetorizar_os_pendentes()  # acabou: agora espera o intervalo
    assert lotes == [2, 1] and servicos.banco.trechos_sem_vetor(audio) == []


def test_sem_ia_os_trechos_antigos_espacam_e_a_ia_instalada_volta_na_hora(
    servicos: Servicos, monkeypatch: pytest.MonkeyPatch
) -> None:
    agora = relogio_da_fila(monkeypatch)
    audio_pronto_com_trechos(servicos.banco, 1)
    tentativas: list[int] = []

    def sem_ia(textos: list[str]) -> list[list[float]] | None:
        tentativas.append(len(textos))
        return None

    servicos.fila.vetorizar = sem_ia
    servicos.fila._vetorizar_os_pendentes()  # tenta
    agora[0] += 61
    servicos.fila._vetorizar_os_pendentes()  # o intervalo dobrou para 120 s: não tenta
    agora[0] += 60
    servicos.fila._vetorizar_os_pendentes()  # 121 s depois: tenta
    servicos.fila.vetorizar_os_pendentes_logo()
    servicos.fila._vetorizar_os_pendentes()  # a IA acabou de ser instalada: tenta na hora
    assert len(tentativas) == 3


def test_erro_nos_vetores_dos_trechos_antigos_vai_ao_log_com_o_rastro_uma_vez_e_espaca(
    servicos: Servicos, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    agora = relogio_da_fila(monkeypatch)
    audio_pronto_com_trechos(servicos.banco, 1)

    def disco_cheio(textos: list[str]) -> list[list[float]] | None:
        raise sqlite3.OperationalError("database or disk is full")

    servicos.fila.vetorizar = disco_cheio
    servicos.fila._vetorizar_os_pendentes()
    agora[0] += 61
    servicos.fila._vetorizar_os_pendentes()  # espaçou: não tenta
    agora[0] += 60
    servicos.fila._vetorizar_os_pendentes()  # tenta de novo: só a linha, sem o rastro inteiro
    registros = [r for r in caplog.records if "vetores dos trechos antigos" in r.getMessage()]
    assert [r.exc_info is not None for r in registros] == [True, False]


def test_modelo_sem_o_tokenizer_nao_conta_como_baixado(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    hugging_face_falso(monkeypatch, tmp_path, arquivos_no_snapshot=("model.bin", "config.json"))
    monkeypatch.setattr("huggingface_hub.try_to_load_from_cache", CARREGAR_DO_CACHE_REAL)
    tr.baixar_modelo("tiny", lambda b, t: None)
    assert "tiny" not in tr.modelos_baixados()  # sem o tokenizer.json o faster-whisper não carrega o modelo


def test_pacote_do_motor_que_quebra_no_meio_nao_deixa_motor_pela_metade(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(motor_ia.sys, "platform", "linux")
    monkeypatch.setattr(motor_ia.platform, "machine", lambda: "x86_64")
    pacote = motor_empacotado(tmp_path, quebrado_no_meio=True)

    def rede(pedido: httpx.Request) -> httpx.Response:
        if pedido.url.path.endswith("sha256sum.txt"):
            return httpx.Response(200, text=f"{hashlib.sha256(pacote).hexdigest()}  ./ollama-linux-amd64.tar.zst\n")
        if pedido.url.host == "github.com":
            return httpx.Response(200, content=pacote)
        return httpx.Response(404)

    motor = motor_ia.MotorDeIA(
        tmp_path / "motor", "http://sem-ollama.teste", cliente=httpx.Client(transport=httpx.MockTransport(rede))
    )
    with pytest.raises(tarfile.FilterError):
        motor.instalar(lambda baixado, total: None)
    # sem a troca no fim, o bin/ollama já extraído contaria como motor instalado, sem o resto do pacote
    assert motor.executavel is None and not (tmp_path / "motor" / "programa.parcial").exists()


def test_erro_inesperado_ao_ler_o_arquivo_responde_500_e_nao_deixa_o_arquivo(
    servicos: Servicos, monkeypatch: pytest.MonkeyPatch
) -> None:
    def disco_com_defeito(caminho: str) -> None:
        raise OSError("erro de leitura no disco")

    monkeypatch.setattr("av.open", disco_com_defeito)
    sem_repassar_o_erro = TestClient(criar_app(servicos), base_url="http://" + HOST, raise_server_exceptions=False)
    _c, r = enviar(sem_repassar_o_erro, "reuniao.wav", wav_de_teste())
    assert r.status_code == 500 and list(servicos.config.pasta_audios.iterdir()) == []


def test_ctrl_c_nao_vira_erro_critico_no_log(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)  # o teste devolve os ganchos originais no fim
    monkeypatch.setattr(threading, "excepthook", threading.excepthook)
    servidor.registrar_os_erros_soltos()
    sys.excepthook(KeyboardInterrupt, KeyboardInterrupt(), None)
    sys.excepthook(RuntimeError, RuntimeError("quebrou"), None)
    assert [r.getMessage() for r in caplog.records if r.levelname == "CRITICAL"] == ["o Echo-AI parou com um erro"]
