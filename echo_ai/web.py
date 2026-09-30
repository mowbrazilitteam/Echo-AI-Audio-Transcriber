"""O servidor local da tela (FastAPI). Só o próprio PC entra: escuta em 127.0.0.1 e confere Host, Origin e o cabeçalho
X-Echo nas escritas, para que outro site aberto no navegador não consiga mandar comando para cá.

A rota só trata o HTTP (formato, validação, arquivo); a regra mora em banco, transcricao, memoria, ollama e voz.
"""

import json
import logging
import queue
import re
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from echo_ai import __version__, hardware, memoria
from echo_ai.aceleracao import Aceleracao
from echo_ai.banco import Banco
from echo_ai.config import AUTOMATICO, NOME_COMPLETO, NOME_DO_APP, Config
from echo_ai.downloads import BAIXANDO, Downloads, Relato
from echo_ai.hardware import PLACA, PRECISAO_DA_PLACA, Maquina
from echo_ai.motor_ia import AUSENTE, MotorDeIA
from echo_ai.ollama import CATALOGO_DE_IA, NOMES_DO_CATALOGO_DE_IA, Ollama, OllamaFora
from echo_ai.transcricao import (
    CATALOGO,
    IDIOMAS,
    NOMES_DO_CATALOGO,
    Avisos,
    Fila,
    Placa,
    baixar_modelo,
    modelos_baixados,
    tem_audio,
)
from echo_ai.voz import VOZES, FalhaDaVoz, falar

log = logging.getLogger("echo")

PASTA = Path(__file__).parent
# O formato é decidido pelo conteúdo (transcricao.tem_audio), não pelo nome. A extensão só é guardada para ajudar o
# FFmpeg, e só se parecer extensão de verdade (letras e números, curta).
EXTENSAO_GUARDAVEL = re.compile(r"\.[a-z0-9]{1,8}")
PEDACO_DO_UPLOAD = 1024 * 1024
BATIDA_DO_EVENTO_S = 15
AJUSTES_ACEITOS = {"whisper_modelo", "idioma", "ollama_modelo", "responder_em_voz", "voz", "modo_da_pergunta", "idioma_da_tela"}
LINGUAS_DA_TELA = {"en", "pt", "es"}
METODOS_DE_ESCRITA = {"POST", "PUT", "PATCH", "DELETE"}
CABECALHO_DO_APP = "X-Echo"
TIPO_WHISPER, TIPO_IA, TIPO_ACELERACAO = "whisper", "ia", "aceleracao"
PACOTE_DA_ACELERACAO = "nvidia"
TAMANHO_DA_ACELERACAO_GB = 1.4  # cuBLAS + cuDNN + NVRTC, medido no PyPI (29/09/2026)
FASE_MOTOR, FASE_MODELO, FASE_BUSCA = "motor", "modelo", "busca"


class ProtecaoLocal(BaseHTTPMiddleware):
    """Contra DNS rebinding (Host tem de ser o nosso) e contra outro site mandando comando (Origin e cabeçalho próprio,
    que um formulário de outro site não consegue pôr sem passar pelo CORS, que aqui não existe)."""

    def __init__(self, app: Any, porta: int) -> None:
        super().__init__(app)
        self.hosts = {f"127.0.0.1:{porta}", f"localhost:{porta}"}
        self.origens = {"http://" + h for h in self.hosts}

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.headers.get("host") not in self.hosts:
            return PlainTextResponse("host recusado", status_code=403)
        if request.method in METODOS_DE_ESCRITA:
            origem = request.headers.get("origin")
            if origem is not None and origem not in self.origens:
                return PlainTextResponse("origem recusada", status_code=403)
            if request.headers.get(CABECALHO_DO_APP) != "1":
                return PlainTextResponse("pedido sem o cabeçalho do app", status_code=403)
        resposta = await call_next(request)
        resposta.headers["X-Content-Type-Options"] = "nosniff"
        resposta.headers["Referrer-Policy"] = "no-referrer"
        resposta.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; "
            "style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'"
        )
        return resposta


class Titulo(BaseModel):
    titulo: str = Field(min_length=1, max_length=120)


class Pergunta(BaseModel):
    pergunta: str = Field(min_length=1, max_length=4000)
    modo: str = Field(default="conversa", pattern="^(conversa|historico)$")
    modelo: str | None = Field(default=None, max_length=200)
    idioma: str | None = Field(default=None, pattern="^(en|pt|es)$")  # o da tela: a resposta sai nele


class Refazer(BaseModel):
    modelo: str
    idioma: str


class TextoParaVoz(BaseModel):
    texto: str = Field(min_length=1, max_length=20000)


@dataclass
class Servicos:
    config: Config
    banco: Banco
    fila: Fila
    placa: Placa
    avisos: Avisos
    ollama: Ollama
    maquina: Maquina
    motor: MotorDeIA
    aceleracao: Aceleracao
    downloads: Downloads = field(default_factory=Downloads)


def nome_seguro(nome: str) -> str:
    """O nome original só para mostrar (sem caminho, sem controle)."""
    nome = Path(nome or "audio").name
    return re.sub(r"[\x00-\x1f]", "", nome)[:200] or "audio"


def srt(trechos: list[dict[str, Any]]) -> str:
    def t(s: float) -> str:
        ms = int(round(s * 1000))
        return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"

    return "\n".join(f"{i}\n{t(x['inicio'])} --> {t(x['fim'])}\n{x['texto']}\n" for i, x in enumerate(trechos, 1))


def criar_app(s: Servicos) -> FastAPI:
    app = FastAPI(title=NOME_COMPLETO, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(ProtecaoLocal, porta=s.config.porta)
    app.mount("/static", StaticFiles(directory=PASTA / "static"), name="static")
    telas = Jinja2Templates(directory=PASTA / "templates")

    def baixados() -> set[str]:
        andando = frozenset(str(d["nome"]) for d in s.downloads.estado() if d["tipo"] == TIPO_WHISPER and d["estado"] == BAIXANDO)
        return modelos_baixados(andando)

    def conversa_ou_404(conversa_id: int) -> dict[str, Any]:
        c = s.banco.conversa(conversa_id)
        if not c:
            raise HTTPException(404, "conversa não existe")
        return c

    def audio_ou_404(audio_id: int) -> dict[str, Any]:
        a = s.banco.audio(audio_id)
        if not a:
            raise HTTPException(404, "áudio não existe")
        return a

    @app.get("/", response_class=HTMLResponse)
    def tela(request: Request) -> Response:
        return telas.TemplateResponse(request, "index.html", {})

    @app.get("/health")
    def saude() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/api/estado")
    def estado() -> dict[str, Any]:
        try:
            modelos_ollama = s.ollama.modelos()
            ollama_erro = None
        except OllamaFora as erro:
            modelos_ollama, ollama_erro = [], str(erro)
        instalados = {m["nome"] for m in modelos_ollama}
        whisper_baixados = baixados()
        return {
            "fila_viva": s.fila.viva(),
            "fila_erro": s.fila.ultimo_erro,
            "app": {"nome": NOME_DO_APP, "nome_completo": NOME_COMPLETO, "versao": __version__},
            "pasta_dados": str(s.config.pasta_dados),
            "maquina": s.maquina.para_a_tela() | {"dispositivo": s.placa.dispositivo, "motivo_da_cpu": s.placa.motivo_da_cpu},
            "placa": s.placa.carregado(),
            "voz_erro": s.config.voz_disponivel(),
            "ollama_erro": ollama_erro,
            "motor_ia": s.motor.situacao(),
            "aceleracao": {"pendente": s.maquina.aceleracao_pendente, "tamanho_gb": TAMANHO_DA_ACELERACAO_GB},
            "modelos_ollama": modelos_ollama,
            "catalogo_de_ia": [
                {
                    "nome": m.nome,
                    "rotulo": m.rotulo,
                    "bilhoes": m.bilhoes,
                    "download_gb": m.download_gb,
                    "qualidade": m.qualidade,
                    "velocidade": m.velocidade,
                    "instalado": m.nome in instalados,
                    "recomendado": m.nome == s.maquina.ia_recomendada,
                }
                for m in CATALOGO_DE_IA
            ],
            "modelos_whisper": [
                {
                    "nome": m.nome,
                    "rotulo": m.rotulo,
                    "download_gb": m.download_gb,
                    "placa_gb": m.placa_gb,
                    "precisao": m.precisao,
                    "velocidade": m.velocidade,
                    "baixado": m.nome in whisper_baixados,
                    "recomendado": m.nome == s.maquina.whisper_recomendado,
                }
                for m in CATALOGO
            ],
            "downloads": s.downloads.estado(),
            "idiomas": IDIOMAS,
            "ajustes": {
                "whisper_modelo": s.config.whisper_modelo,
                "idioma": "auto",
                "ollama_modelo": s.config.ollama_modelo or s.maquina.ia_recomendada,
                "responder_em_voz": "0",
                "voz": s.config.piper_voz,
                "modo_da_pergunta": "conversa",
            }
            | s.banco.ajustes(),
        }

    @app.post("/api/ajustes")
    def gravar_ajustes(novos: dict[str, str]) -> dict[str, str]:
        fora = set(novos) - AJUSTES_ACEITOS
        if fora:
            raise HTTPException(422, "ajuste desconhecido: {}".format(", ".join(sorted(fora))))
        if "whisper_modelo" in novos and novos["whisper_modelo"] not in NOMES_DO_CATALOGO:
            raise HTTPException(422, "modelo do whisper fora do catálogo")
        if "idioma" in novos and novos["idioma"] not in IDIOMAS:
            raise HTTPException(422, "idioma fora da lista")
        if "idioma_da_tela" in novos and novos["idioma_da_tela"] not in LINGUAS_DA_TELA:
            raise HTTPException(422, "idioma da tela fora da lista (en, pt, es)")
        s.banco.gravar_ajustes({k: str(v)[:200] for k, v in novos.items()})
        return s.banco.ajustes()

    @app.get("/api/downloads")
    def downloads() -> list[dict[str, object]]:
        return s.downloads.estado()

    @app.post("/api/modelos/{nome}/baixar")
    def baixar(nome: str) -> dict[str, str]:
        if nome not in NOMES_DO_CATALOGO:
            raise HTTPException(404, "modelo fora do catálogo")
        s.downloads.iniciar(TIPO_WHISPER, nome, lambda relato: baixar_modelo(nome, relato))
        return {"estado": BAIXANDO}

    @app.post("/api/aceleracao/instalar")
    def instalar_aceleracao() -> dict[str, str]:
        """O pacote da NVIDIA (cuBLAS, cuDNN, NVRTC). Terminado, a transcrição passa para a placa sem reiniciar."""
        if not s.maquina.aceleracao_pendente:
            raise HTTPException(409, "esta máquina não precisa do pacote de aceleração")

        def trabalho(relato: Relato) -> None:
            s.aceleracao.instalar(relato)
            s.maquina = hardware.detectar(s.config.pasta_da_aceleracao)
            if s.maquina.dispositivo != PLACA:
                raise RuntimeError("o pacote foi instalado, mas a placa ainda não respondeu: confira o driver da NVIDIA")
            if s.config.whisper_dispositivo == AUTOMATICO:  # quem fixou o dispositivo na configuração continua nele
                s.placa.passar_para_a_placa()
            if s.config.whisper_precisao == AUTOMATICO and s.placa.dispositivo == PLACA:
                s.fila.precisao = PRECISAO_DA_PLACA

        s.downloads.iniciar(TIPO_ACELERACAO, PACOTE_DA_ACELERACAO, trabalho)
        return {"estado": BAIXANDO}

    @app.post("/api/ia/{nome}/instalar")
    def instalar_ia(nome: str) -> dict[str, str]:
        """Instala uma IA da lista. Sem motor, instala o motor antes (fase 'motor'); depois a IA (fase 'modelo') e, se
        faltar, o modelo da busca por significado da memória longa (fase 'busca')."""
        if nome not in NOMES_DO_CATALOGO_DE_IA:
            raise HTTPException(404, "IA fora da lista do app")

        def trabalho(relato: Relato) -> None:
            if s.motor.situacao() == AUSENTE:
                relato.fase(FASE_MOTOR)
                s.motor.instalar(relato)
            s.ollama.trocar_url(s.motor.url())
            relato.fase(FASE_MODELO)
            s.ollama.baixar(nome, relato)
            if s.config.modelo_de_busca not in {n.split(":")[0] for n in s.ollama.instalados()}:
                relato.fase(FASE_BUSCA)
                s.ollama.baixar(s.config.modelo_de_busca, relato)
            s.fila.vetorizar_os_pendentes_logo()  # o que foi transcrito antes da IA entra na busca por significado já

        s.downloads.iniciar(TIPO_IA, nome, trabalho)
        return {"estado": BAIXANDO}

    # ── conversas ──────────────────────────────────────────────────────────────────────────
    @app.get("/api/conversas")
    def listar(busca: str = "") -> list[dict[str, Any]]:
        return s.banco.conversas(busca[:200])

    @app.post("/api/conversas")
    def criar() -> dict[str, Any]:
        return conversa_ou_404(s.banco.criar_conversa())

    @app.get("/api/conversas/{conversa_id}")
    def abrir(conversa_id: int) -> dict[str, Any]:
        c = conversa_ou_404(conversa_id)
        audios = {a["id"]: a | {"trechos": s.banco.trechos(a["id"])} for a in s.banco.audios_da_conversa(conversa_id)}
        return {"conversa": c, "mensagens": s.banco.mensagens(conversa_id), "audios": audios}

    @app.patch("/api/conversas/{conversa_id}")
    def renomear(conversa_id: int, corpo: Titulo) -> dict[str, Any]:
        conversa_ou_404(conversa_id)
        s.banco.renomear(conversa_id, corpo.titulo)
        return conversa_ou_404(conversa_id)

    @app.delete("/api/conversas/{conversa_id}")
    def apagar(conversa_id: int) -> dict[str, int]:
        conversa_ou_404(conversa_id)
        arquivos = s.banco.apagar_conversa(conversa_id)
        raiz = s.config.pasta_audios.resolve()
        apagados = 0
        for nome in arquivos:
            caminho = (raiz / nome).resolve()
            if caminho.parent == raiz and caminho.is_file():  # nunca fora da pasta dos áudios
                caminho.unlink()
                apagados += 1
        return {"arquivos_apagados": apagados}

    # ── áudios ─────────────────────────────────────────────────────────────────────────────
    @app.post("/api/conversas/{conversa_id}/audios")
    async def enviar_audio(
        conversa_id: int, arquivo: Annotated[UploadFile, File()], modelo: Annotated[str, Form()], idioma: Annotated[str, Form()]
    ) -> dict[str, Any]:
        conversa = conversa_ou_404(conversa_id)
        if modelo not in NOMES_DO_CATALOGO:
            raise HTTPException(422, "modelo do whisper fora do catálogo")
        if modelo not in baixados():
            raise HTTPException(422, f"o modelo {modelo} ainda não foi baixado: baixe em Ajustes")
        if idioma not in IDIOMAS:
            raise HTTPException(422, "idioma fora da lista")
        original = nome_seguro(arquivo.filename or "audio")
        extensao = Path(original).suffix.lower()
        if not EXTENSAO_GUARDAVEL.fullmatch(extensao):
            extensao = ""
        limite = s.config.tamanho_maximo_do_audio_mb * 1024 * 1024
        nome = uuid.uuid4().hex + extensao
        destino = s.config.pasta_audios / nome
        total = 0
        try:
            with destino.open("wb") as saida:
                while pedaco := await arquivo.read(PEDACO_DO_UPLOAD):
                    total += len(pedaco)
                    if total > limite:
                        raise HTTPException(413, f"áudio maior que {s.config.tamanho_maximo_do_audio_mb} MB")
                    saida.write(pedaco)
        except BaseException:
            destino.unlink(missing_ok=True)
            raise
        if total == 0:
            destino.unlink(missing_ok=True)
            raise HTTPException(422, "arquivo vazio")
        try:
            tem = await run_in_threadpool(tem_audio, str(destino))
        except BaseException:  # erro do sistema ao ler o arquivo: sobe, mas o arquivo não fica órfão na pasta
            destino.unlink(missing_ok=True)
            raise
        if not tem:
            destino.unlink(missing_ok=True)
            raise HTTPException(415, f"{original} não tem áudio que dê para transcrever")
        audio_id = s.banco.acrescentar_audio(conversa_id, original, nome, modelo, idioma)
        if conversa["titulo"] == "Nova transcrição":
            s.banco.renomear(conversa_id, Path(original).stem[:80])
        s.fila.acordar()
        return audio_ou_404(audio_id)

    @app.post("/api/audios/{audio_id}/refazer")
    def refazer(audio_id: int, corpo: Refazer) -> dict[str, Any]:
        audio_ou_404(audio_id)
        if corpo.modelo not in baixados() or corpo.idioma not in IDIOMAS:
            raise HTTPException(422, "modelo não baixado ou idioma fora da lista")
        if not s.banco.refazer_audio(audio_id, corpo.modelo, corpo.idioma):
            raise HTTPException(409, "o áudio ainda está na fila ou transcrevendo")
        s.fila.acordar()
        return audio_ou_404(audio_id)

    @app.get("/api/audios/{audio_id}/arquivo")
    def arquivo_do_audio(audio_id: int) -> FileResponse:
        a = audio_ou_404(audio_id)
        caminho = (s.config.pasta_audios / a["arquivo"]).resolve()
        if caminho.parent != s.config.pasta_audios.resolve() or not caminho.is_file():
            raise HTTPException(404, "arquivo do áudio sumiu")
        return FileResponse(caminho, filename=a["nome_original"])

    @app.get("/api/audios/{audio_id}/texto.{formato}")
    def baixar_texto(audio_id: int, formato: str) -> PlainTextResponse:
        a = audio_ou_404(audio_id)
        trechos = s.banco.trechos(audio_id)
        if formato == "txt":
            corpo = "\n".join(t["texto"] for t in trechos) + "\n"
        elif formato == "srt":
            corpo = srt(trechos)
        else:
            raise HTTPException(404, "formato: txt ou srt")
        nome = Path(a["nome_original"]).stem + "." + formato
        # nome com acento: filename* (RFC 5987) para o navegador, e um nome só ASCII para o cabeçalho antigo
        simples = nome.encode("ascii", "ignore").decode().replace('"', "") or "transcricao." + formato
        return PlainTextResponse(
            corpo, headers={"Content-Disposition": f"attachment; filename=\"{simples}\"; filename*=UTF-8''{quote(nome)}"}
        )

    @app.get("/api/audios/{audio_id}/eventos")
    def eventos(audio_id: int) -> StreamingResponse:
        """O andamento da transcrição em tempo real (Server-Sent Events): primeiro o que já existe, depois o que chega."""
        audio_ou_404(audio_id)
        fila = s.avisos.ouvir(audio_id)

        def fluxo() -> Iterator[str]:
            try:
                a = s.banco.audio(audio_id) or {}
                vistos = {t["id"] for t in s.banco.trechos(audio_id)}
                yield "data: {}\n\n".format(
                    json.dumps({"tipo": "estado", "estado": a.get("estado"), "progresso": a.get("progresso")})
                )
                if a.get("estado") in ("pronto", "erro"):
                    return
                while True:
                    try:
                        evento = fila.get(timeout=BATIDA_DO_EVENTO_S)
                    except queue.Empty:
                        yield ": batida\n\n"
                        continue
                    if evento.get("tipo") == "trecho" and evento["id"] in vistos:
                        continue
                    yield f"data: {json.dumps(evento, ensure_ascii=False)}\n\n"
                    if evento.get("tipo") == "estado" and evento.get("estado") in ("pronto", "erro"):
                        return
            finally:
                s.avisos.parar_de_ouvir(audio_id, fila)

        return StreamingResponse(fluxo(), media_type="text/event-stream", headers={"Cache-Control": "no-store"})

    # ── perguntas ao Ollama ────────────────────────────────────────────────────────────────
    @app.post("/api/conversas/{conversa_id}/perguntar")
    def perguntar(conversa_id: int, corpo: Pergunta) -> StreamingResponse:
        conversa_ou_404(conversa_id)
        modelo = corpo.modelo or s.banco.ajustes().get("ollama_modelo") or s.config.ollama_modelo
        try:
            aceitos = {m["nome"] for m in s.ollama.modelos()}
        except OllamaFora as erro:
            if s.motor.situacao() == AUSENTE:
                # nenhum motor respondeu (o primeiro uso, ou o Ollama do usuário fechado): 409, e a tela mostra o cartão
                # de instalar, que também lembra de abrir o Ollama de quem já usa
                log.info("nenhum motor de IA respondeu (%s): a tela oferece instalar", erro)
                raise HTTPException(409, f"nenhuma IA instalada: instale uma (recomendada: {s.maquina.ia_recomendada})") from erro
            # o motor existe mas está fora (desligado, travado): 503 com o motivo; não é "falta instalar"
            log.warning("a IA local não respondeu: %s", erro)
            raise HTTPException(503, f"a IA local não respondeu: {erro}") from erro
        if not aceitos:
            # 409: o motor responde mas não há IA de 1 a 8 B; a tela mostra o cartão de instalar a recomendada
            raise HTTPException(409, f"nenhuma IA instalada: instale uma (recomendada: {s.maquina.ia_recomendada})")
        if modelo not in aceitos:
            raise HTTPException(422, f"a IA {modelo} não serve para a conversa: use um modelo de conversa de 1 a 8 bilhões")
        historico = s.banco.mensagens(conversa_id)
        s.banco.acrescentar_mensagem(conversa_id, "usuario", corpo.pergunta)

        def fluxo() -> Iterator[str]:
            fontes: list[dict[str, Any]] = []
            try:
                if corpo.modo == "historico":
                    trechos, com_significado = memoria.buscar(s.banco, s.ollama, s.config.modelo_de_busca, corpo.pergunta)
                    fontes = memoria.fontes_numeradas(trechos)
                    contexto = memoria.contexto_da_memoria(fontes)
                    yield (
                        json.dumps({"tipo": "fontes", "fontes": fontes, "com_significado": com_significado}, ensure_ascii=False)
                        + "\n"
                    )
                else:
                    contexto = memoria.contexto_da_conversa(s.banco, conversa_id)
                if not s.banco.ha_trabalho_na_fila():
                    s.placa.liberar()  # o Ollama precisa da placa; a transcrição recarrega quando voltar
                resposta = []
                for pedaco in s.ollama.conversar(
                    modelo, memoria.mensagens_para_o_ollama(corpo.pergunta, historico, contexto, corpo.idioma)
                ):
                    resposta.append(pedaco)
                    yield json.dumps({"tipo": "texto", "texto": pedaco}, ensure_ascii=False) + "\n"
                texto = "".join(resposta).strip() or "(o modelo não respondeu nada)"
                mensagem_id = s.banco.acrescentar_mensagem(conversa_id, "assistente", texto, fontes=fontes, modelo=modelo)
                yield json.dumps({"tipo": "fim", "mensagem_id": mensagem_id, "modelo": modelo}) + "\n"
            except OllamaFora as erro:
                log.warning("a resposta da IA parou: %s", erro)
                yield json.dumps({"tipo": "erro", "erro": str(erro)}, ensure_ascii=False) + "\n"
            except Exception as erro:  # o fluxo já começou: sem isto a tela via só "erro de rede" e o log ficava vazio
                log.exception("a resposta da pergunta falhou")
                yield json.dumps({"tipo": "erro", "erro": f"{type(erro).__name__}: {erro}"}, ensure_ascii=False) + "\n"

        return StreamingResponse(fluxo(), media_type="application/x-ndjson", headers={"Cache-Control": "no-store"})

    # ── voz ────────────────────────────────────────────────────────────────────────────────
    @app.post("/api/voz")
    def gerar_voz(corpo: TextoParaVoz) -> dict[str, str]:
        motivo = s.config.voz_disponivel()
        if motivo:
            raise HTTPException(503, motivo)
        voz = s.banco.ajustes().get("voz") or s.config.piper_voz
        if voz not in VOZES:
            raise HTTPException(422, "voz desconhecida")
        try:
            wav = falar(corpo.texto, s.config.piper, s.config.piper_vozes, voz, s.config.pasta_vozes_geradas)
        except ValueError as erro:
            raise HTTPException(422, str(erro)) from erro
        except FalhaDaVoz as erro:
            log.error("voz: %s", erro)
            raise HTTPException(502, str(erro)) from erro
        return {"url": "/api/voz/" + wav.name}

    @app.get("/api/voz/{nome}")
    def ouvir_voz(nome: str) -> FileResponse:
        if not re.fullmatch(r"[0-9a-f]{24}\.wav", nome):
            raise HTTPException(404, "voz não existe")
        caminho = s.config.pasta_vozes_geradas / nome
        if not caminho.is_file():
            raise HTTPException(404, "voz não existe")
        return FileResponse(caminho, media_type="audio/wav")

    @app.exception_handler(HTTPException)
    def erro_http(_: Request, erro: HTTPException) -> JSONResponse:
        return JSONResponse({"erro": erro.detail}, status_code=erro.status_code)

    @app.exception_handler(Exception)
    def erro_inesperado(pedido: Request, erro: Exception) -> JSONResponse:
        """Erro que ninguém esperava: vai para o log com o traceback, e a tela recebe o motivo em JSON (não um 500 mudo)."""
        log.error("erro inesperado em %s %s", pedido.method, pedido.url.path, exc_info=erro)
        return JSONResponse({"erro": f"erro inesperado: {type(erro).__name__}: {erro}"}, status_code=500)

    return app
