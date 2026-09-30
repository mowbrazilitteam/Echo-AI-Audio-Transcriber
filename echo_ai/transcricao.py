"""A transcrição: o catálogo de modelos do whisper, a placa de vídeo e a fila de trabalho.

Um áudio por vez (a placa tem 6 GB): a thread da fila pega o próximo da tabela `audios`, libera o Ollama da placa,
carrega o modelo pedido e grava cada trecho assim que ele sai, avisando quem está olhando a tela (Avisos). Depois,
calcula os vetores da memória longa pelo Ollama (se ele não estiver de pé, o áudio fica pronto do mesmo jeito e a busca
por significado não acha esses trechos até a próxima vez). Parada, a placa é liberada depois de alguns minutos.
"""

import fnmatch
import gc
import logging
import queue
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from echo_ai.banco import Banco
from echo_ai.hardware import CPU, PLACA, PRECISAO_DA_CPU

log = logging.getLogger("echo")
SINAIS_DE_ERRO_DA_PLACA = ("cuda", "cublas", "cudnn", "gpu", "out of memory")


def e_erro_da_placa(erro: BaseException) -> bool:
    """Erro que vem da placa (biblioteca da CUDA faltando, driver, memória da placa), e não do áudio."""
    texto = str(erro).lower()
    return any(sinal in texto for sinal in SINAIS_DE_ERRO_DA_PLACA)


TRECHOS_POR_LOTE_DE_VETOR = 32
ESPERA_DA_FILA_S = 1.0
ESPERA_DEPOIS_DE_ERRO_S = 5.0
ESPERA_MAXIMA_DEPOIS_DE_ERRO_S = 60.0
DOBRAS_MAXIMAS_DA_ESPERA = 10  # sem teto no expoente, 2 ** 1024 estoura o float depois de ~17 h de falha seguida
INTERVALO_DOS_PENDENTES_S = 60.0
INTERVALO_MAXIMO_DOS_PENDENTES_S = 1800.0
TAXA_DO_WHISPER = 16000  # o whisper trabalha em 16 kHz mono
INTERVALO_DO_ANDAMENTO_S = 0.5
# os arquivos que o faster_whisper.download_model baixa (o resto do repositório não é preciso)
ARQUIVOS_DO_MODELO = ["config.json", "preprocessor_config.json", "model.bin", "tokenizer.json", "vocabulary.*"]
BARRAS_DA_ONDA = 600  # quantos picos a tela desenha no tocador


@dataclass(frozen=True)
class ModeloWhisper:
    """Um modelo do whisper, com o que a tela mostra para comparar lado a lado (precisão e velocidade de 1 a 5; o texto
    de "para que serve" fica na tela, nos três idiomas)."""

    nome: str
    rotulo: str
    download_gb: float
    placa_gb: float  # memória de placa aproximada em float16 (large-v3-turbo medido: 2,1 GB numa RTX 4050)
    precisao: int
    velocidade: int
    revisao: str  # o commit do repositório no Hugging Face: o app baixa sempre esta versão, não o que estiver no "main"


# as revisões conferidas em 29/09/2026 (HfApi().model_info(repo).sha)
CATALOGO = (
    ModeloWhisper("tiny", "Tiny", 0.08, 0.4, 1, 5, "d90ca5fe260221311c53c58e660288d3deb8d356"),
    ModeloWhisper("base", "Base", 0.15, 0.5, 2, 5, "ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66"),
    ModeloWhisper("small", "Small", 0.5, 1.0, 3, 4, "536b0662742c02347bc0e980a01041f333bce120"),
    ModeloWhisper("medium", "Medium", 1.5, 2.0, 4, 3, "08e178d48790749d25932bbc082711ddcfdfbc4f"),
    ModeloWhisper("large-v3-turbo", "Large v3 Turbo", 1.6, 2.1, 4, 4, "0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf"),
    ModeloWhisper("large-v3", "Large v3", 3.1, 4.5, 5, 2, "edaa852ec7e145841d8ffdb056a99866b5f0a478"),
)
REVISAO = {m.nome: m.revisao for m in CATALOGO}
NOMES_DO_CATALOGO = {m.nome for m in CATALOGO}
# os idiomas que a tela oferece para o áudio (o whisper entende uns 100; "auto" deixa ele descobrir). O nome mostrado
# vem do navegador (Intl.DisplayNames), no idioma da tela.
IDIOMAS = {
    codigo: codigo
    for codigo in ("auto", "en", "pt", "es", "fr", "de", "it", "nl", "pl", "ru", "uk", "tr", "ar", "hi", "zh", "ja", "ko")
}


# O instalador traz o small aqui dentro (scripts/incluir_modelo.py, rodado pelo GitHub Actions antes de empacotar):
# a primeira transcrição funciona sem internet.
PASTA_DOS_MODELOS_INCLUIDOS = Path(__file__).parent / "modelos"


def modelo_incluido(nome: str) -> Path | None:
    pasta = PASTA_DOS_MODELOS_INCLUIDOS / nome
    return pasta if (pasta / "model.bin").is_file() else None


def repositorio_do_modelo(nome: str) -> str:
    """O repositório do Hugging Face de onde o faster-whisper baixa o modelo."""
    from faster_whisper.utils import _MODELS  # o mapa do próprio faster-whisper: é ele que decide de onde baixa

    return _MODELS[nome]


ARQUIVOS_ESSENCIAIS = (
    "model.bin",
    "config.json",
    "tokenizer.json",
)  # sem eles o modelo não carrega (o download pode ter caído no meio)


def _completo_no_cache(nome: str) -> bool:
    from huggingface_hub import try_to_load_from_cache

    repositorio = repositorio_do_modelo(nome)
    return all(
        isinstance(try_to_load_from_cache(repositorio, arquivo, revision=REVISAO[nome]), str) for arquivo in ARQUIVOS_ESSENCIAIS
    )


def modelos_baixados(baixando: frozenset[str] = frozenset()) -> set[str]:
    """Os modelos do catálogo prontos para rodar sem internet: o incluído no instalador, ou os que têm os arquivos
    essenciais completos no cache do Hugging Face. O que está `baixando` agora não conta, mesmo com parte no cache."""
    return {m.nome for m in CATALOGO if m.nome not in baixando and (modelo_incluido(m.nome) or _completo_no_cache(m.nome))}


class Trecho(Protocol):
    start: float
    end: float
    text: str


class Motor(Protocol):
    """O que a fila precisa de um modelo de transcrição (o WhisperModel real, ou um falso nos testes)."""

    def transcribe(self, audio: str, **opcoes: Any) -> tuple[Iterable[Trecho], Any]: ...


CarregarMotor = Callable[[str, str, str], Motor]
Decodificar = Callable[[str], Any]  # caminho -> amostras em 16 kHz (numpy), ou lista nos testes


def decodificar_audio(caminho: str) -> Any:
    from faster_whisper.audio import decode_audio

    return decode_audio(caminho, sampling_rate=TAXA_DO_WHISPER)


def tem_audio(caminho: str) -> bool:
    """Se o arquivo tem uma trilha de áudio, pelo conteúdo e não pelo nome: qualquer formato que o FFmpeg (dentro do
    PyAV) abre, de áudio ou de vídeo. Só lê o cabeçalho, então responde rápido mesmo num arquivo de horas."""
    import av

    try:
        recipiente = av.open(caminho)
    # não é mídia, ou está corrompido/cortado (a nota de voz baixada pela metade dá EOFError): não é erro do app
    except (av.error.InvalidDataError, av.error.EOFError, UnicodeDecodeError) as erro:
        log.info("sem áudio que dê para ler em %s: %s", Path(caminho).name, erro)
        return False
    try:
        return any(trilha.type == "audio" for trilha in recipiente.streams)
    finally:
        recipiente.close()


def contorno(amostras: Any, barras: int = BARRAS_DA_ONDA) -> list[float]:
    """Os picos do áudio em `barras` fatias, de 0 a 1 (o maior pico vira 1). Áudio vazio = lista vazia."""
    total = len(amostras)
    if not total:
        return []
    passo = max(1, total // barras)
    picos = [float(max(abs(float(x)) for x in amostras[i : i + passo][:: max(1, passo // 256)])) for i in range(0, total, passo)][
        :barras
    ]
    maior = max(picos) or 1.0
    return [p / maior for p in picos]


def _pastas_das_bibliotecas_da_nvidia(pasta: str) -> list[Path]:
    """As pastas `pasta` (lib no Linux, bin no Windows) do cuBLAS e do cuDNN que vieram com o pip (instalação de
    desenvolvimento, com o extra gpu)."""
    from echo_ai.hardware import especificacao_do_pacote

    pastas = []
    for pacote in ("nvidia.cublas", "nvidia.cudnn", "nvidia.cuda_nvrtc"):
        spec = especificacao_do_pacote(pacote)
        if spec and spec.submodule_search_locations:
            pastas.append(Path(list(spec.submodule_search_locations)[0]) / pasta)
    return [p for p in pastas if p.is_dir()]


def preparar_bibliotecas_da_placa(pastas_extras: Iterable[Path] = ()) -> None:
    """Deixa o cuBLAS e o cuDNN que vieram com o pip à vista do CTranslate2 (sem isso a placa não é achada). No Windows,
    é pôr as pastas na busca de DLL; no Linux, carregar as bibliotecas antes, e a que depende de outra ainda não
    carregada é tentada de novo na volta seguinte. `pastas_extras` = as do pacote de aceleração baixado pelo app. O Mac
    não usa (transcreve na CPU)."""
    import ctypes
    import os
    import sys

    extras = list(pastas_extras)
    if sys.platform == "win32":
        for pasta in _pastas_das_bibliotecas_da_nvidia("bin") + extras:
            os.add_dll_directory(str(pasta))
        return
    if sys.platform == "darwin":
        return
    arquivos: list[Path] = []
    for pasta in _pastas_das_bibliotecas_da_nvidia("lib") + extras:
        arquivos += (
            sorted(pasta.glob("libnvrtc*.so.12")) + sorted(pasta.glob("libcublas*.so.12")) + sorted(pasta.glob("libcudnn*.so.9"))
        )
    faltam = arquivos
    while faltam:
        ainda = []
        for arquivo in faltam:
            try:
                ctypes.CDLL(str(arquivo), mode=ctypes.RTLD_GLOBAL)
            except OSError:
                ainda.append(arquivo)
        if len(ainda) == len(faltam):
            log.warning("bibliotecas da placa que não carregaram: %s", ", ".join(a.name for a in ainda))
            return
        faltam = ainda


def carregar_whisper(nome: str, dispositivo: str, precisao: str, pastas_extras: Iterable[Path] = ()) -> Motor:
    """Só modelo já baixado (local_files_only): nada sai para a internet sem o botão Baixar da tela."""
    from faster_whisper import WhisperModel

    if dispositivo == PLACA:
        preparar_bibliotecas_da_placa(pastas_extras)
    incluido = modelo_incluido(nome)
    if incluido:
        return WhisperModel(str(incluido), device=dispositivo, compute_type=precisao, local_files_only=True)
    return WhisperModel(nome, device=dispositivo, compute_type=precisao, local_files_only=True, revision=REVISAO[nome])


def _bytes_na_pasta(pasta: Path) -> int:
    """Os bytes em disco (a pasta blobs/: os snapshots são links para ela e contariam em dobro). Um arquivo que some
    entre a lista e o stat (o .incomplete renomeado no fim) não conta."""
    total = 0
    for arquivo in pasta.glob("*") if pasta.is_dir() else ():
        try:
            total += arquivo.stat().st_size
        except FileNotFoundError:
            continue
    return total


def baixar_modelo(nome: str, avisar: Callable[[int, int], None]) -> None:
    """Baixa um modelo do catálogo (a única vez em que o app usa a internet, e só pelo botão), avisando o andamento.

    O andamento é medido no disco, a cada meio segundo: o download comum grava o arquivo aos poucos. O download pelo
    Xet (o padrão do Hugging Face) só grava no fim, e a barra ficaria parada; por isso o app sobe com
    HF_HUB_DISABLE_XET=1 (echo_ai/__init__.py)."""
    if nome not in NOMES_DO_CATALOGO:
        raise ValueError(f"modelo fora do catálogo: {nome!r}")
    from huggingface_hub import HfApi, constants, snapshot_download

    repositorio = repositorio_do_modelo(nome)
    arquivos = HfApi().model_info(repositorio, revision=REVISAO[nome], files_metadata=True).siblings or []
    total = sum(a.size or 0 for a in arquivos if any(fnmatch.fnmatch(a.rfilename, p) for p in ARQUIVOS_DO_MODELO))
    pasta = Path(constants.HF_HUB_CACHE) / ("models--" + repositorio.replace("/", "--")) / "blobs"
    ja_havia = _bytes_na_pasta(pasta)
    terminou = threading.Event()

    def medir() -> None:
        while not terminou.wait(INTERVALO_DO_ANDAMENTO_S):
            avisar(min(total, _bytes_na_pasta(pasta) - ja_havia), total)

    medidor = threading.Thread(target=medir, name=f"andamento-{nome}", daemon=True)
    medidor.start()
    try:
        snapshot_download(repositorio, revision=REVISAO[nome], allow_patterns=ARQUIVOS_DO_MODELO)
    finally:
        terminou.set()
        medidor.join()
    avisar(total, total)


class Avisos:
    """Quem está com a tela aberta recebe cada trecho na hora (uma fila por ouvinte, por áudio)."""

    def __init__(self) -> None:
        self._trava = threading.Lock()
        self._ouvintes: dict[int, list[queue.Queue[dict[str, Any]]]] = {}

    def ouvir(self, audio_id: int) -> queue.Queue[dict[str, Any]]:
        fila: queue.Queue[dict[str, Any]] = queue.Queue()
        with self._trava:
            self._ouvintes.setdefault(audio_id, []).append(fila)
        return fila

    def parar_de_ouvir(self, audio_id: int, fila: queue.Queue[dict[str, Any]]) -> None:
        with self._trava:
            lista = self._ouvintes.get(audio_id, [])
            if fila in lista:
                lista.remove(fila)

    def publicar(self, audio_id: int, evento: dict[str, Any]) -> None:
        with self._trava:
            filas = list(self._ouvintes.get(audio_id, []))
        for fila in filas:
            fila.put(evento)


@dataclass
class Placa:
    """O modelo carregado na placa. Um de cada vez; liberado depois de `minutos_parada` sem uso."""

    carregar: CarregarMotor
    dispositivo: str
    minutos_parada: int
    motivo_da_cpu: str | None = None  # por que saiu da placa, quando saiu
    _motor: Motor | None = None
    _chave: tuple[str, str] | None = None
    _ultimo_uso: float = 0.0
    _trava: threading.Lock = field(default_factory=threading.Lock)

    def motor(self, nome: str, precisao: str) -> Motor:
        with self._trava:
            if self._chave != (nome, precisao):
                self._liberar_sem_trava()
                self._motor = self.carregar(nome, self.dispositivo, precisao)
                self._chave = (nome, precisao)
            self._ultimo_uso = time.monotonic()
            if self._motor is None:
                raise RuntimeError(f"o whisper {nome} não carregou")
            return self._motor

    def passar_para_a_placa(self) -> None:
        """A aceleração chegou: daqui em diante transcreve na placa (o modelo carregado na CPU sai)."""
        with self._trava:
            self._liberar_sem_trava()
            self.dispositivo = PLACA
            self.motivo_da_cpu = None

    def cair_para_a_cpu(self, motivo: str) -> None:
        """A placa falhou (driver, CUDA, memória): daqui em diante transcreve na CPU, e a tela mostra o motivo."""
        with self._trava:
            self._liberar_sem_trava()
            self.dispositivo = CPU
            self.motivo_da_cpu = motivo
        log.warning("a placa falhou e a transcrição passou para a CPU: %s", motivo)

    def carregado(self) -> str | None:
        return self._chave[0] if self._chave else None

    def liberar_se_parada(self) -> bool:
        with self._trava:
            if self._motor is not None and time.monotonic() - self._ultimo_uso > self.minutos_parada * 60:
                self._liberar_sem_trava()
                return True
        return False

    def liberar(self) -> None:
        with self._trava:
            self._liberar_sem_trava()

    def _liberar_sem_trava(self) -> None:
        if self._motor is not None:
            self._motor = None
            self._chave = None
            gc.collect()  # o CTranslate2 devolve a memória da placa quando o modelo sai de uso
            log.info("modelo do whisper liberado da placa")


def espera_depois_de(falhas_seguidas: int) -> float:
    """Quanto a fila espera antes de tentar de novo: dobra a cada falha seguida, até ESPERA_MAXIMA_DEPOIS_DE_ERRO_S."""
    dobras = min(max(falhas_seguidas - 1, 0), DOBRAS_MAXIMAS_DA_ESPERA)
    return float(min(ESPERA_DEPOIS_DE_ERRO_S * 2**dobras, ESPERA_MAXIMA_DEPOIS_DE_ERRO_S))


@dataclass
class Fila:
    """A thread que transcreve a fila, um áudio por vez."""

    banco: Banco
    placa: Placa
    avisos: Avisos
    pasta_audios: Path
    precisao: str
    liberar_ollama: Callable[[], None]
    vetorizar: Callable[[list[str]], list[list[float]] | None]
    decodificar: Decodificar = decodificar_audio
    _ultima_vez_dos_pendentes: float = float("-inf")
    _intervalo_dos_pendentes: float = INTERVALO_DOS_PENDENTES_S
    falhas_seguidas: int = 0
    ultimo_erro: str | None = None  # a tela mostra enquanto a fila estiver falhando
    _parar: threading.Event = field(default_factory=threading.Event)
    _acordar: threading.Event = field(default_factory=threading.Event)
    _thread: threading.Thread | None = None

    def iniciar(self) -> None:
        voltaram = self.banco.devolver_os_interrompidos()
        if voltaram:
            log.warning("%d áudio(s) interrompido(s) voltaram para a fila", voltaram)
        self._thread = threading.Thread(target=self._rodar, name="fila-de-transcricao", daemon=True)
        self._thread.start()

    def acordar(self) -> None:
        self._acordar.set()

    def parar(self) -> None:
        self._parar.set()
        self._acordar.set()
        if self._thread:
            self._thread.join(timeout=10)

    def _rodar(self) -> None:
        """O laço da fila. Um erro inesperado (disco cheio, banco travado) vai para o log e a fila continua: sem isto a
        thread morria calada e a tela ficava em "na fila" para sempre."""
        while not self._parar.is_set():
            audio: dict[str, Any] | None = None
            try:
                if self.falhas_seguidas:
                    # o banco pode ter voltado: o áudio que a falha não deixou marcar como erro volta para a fila (só esta
                    # thread transcreve, então nada mais está legitimamente em "transcrevendo" aqui)
                    self.banco.devolver_os_interrompidos()
                audio = self.banco.proximo_da_fila()
                self.falhas_seguidas, self.ultimo_erro = 0, None  # pegar a trava de escrita deu certo: o aviso sai da tela
                if audio is None:
                    self.placa.liberar_se_parada()
                    self._vetorizar_os_pendentes()
                    self._acordar.wait(ESPERA_DA_FILA_S)
                    self._acordar.clear()
                else:
                    self.transcrever(audio)
            except Exception as erro:
                self._registrar_a_falha(erro, audio)

    def _registrar_a_falha(self, erro: Exception, audio: dict[str, Any] | None) -> None:
        """Uma falha que não passa (banco corrompido, disco cheio) vira aviso na tela (`ultimo_erro`), traceback só na
        primeira vez, e espera crescente: o log não enche e a causa não sai do arquivo."""
        self.falhas_seguidas += 1
        self.ultimo_erro = f"{type(erro).__name__}: {erro}"
        espera = espera_depois_de(self.falhas_seguidas)
        if self.falhas_seguidas == 1:
            log.exception("a fila de transcrição falhou; tenta de novo em %.0f s", espera)
        else:
            log.error("a fila de transcrição falhou de novo (%d seguidas): %s", self.falhas_seguidas, self.ultimo_erro)
        if audio is not None:  # o áudio não fica "transcrevendo" sem ninguém trabalhando nele: nem na tela, nem no banco
            try:
                self.banco.falhar_audio(int(audio["id"]), self.ultimo_erro)
            except Exception as outro:  # o banco segue fora; quando voltar, a próxima volta devolve o áudio para a fila
                log.error("não consegui marcar o áudio %s como erro: %s", audio["id"], outro)
            self.avisos.publicar(int(audio["id"]), {"tipo": "estado", "estado": "erro", "erro": self.ultimo_erro})
        self._acordar.wait(espera)  # um áudio novo (ou o parar) encurta a espera
        self._acordar.clear()

    def viva(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def vetorizar_os_pendentes_logo(self) -> None:
        """A IA acabou de ser instalada: os trechos antigos ganham vetor na próxima volta da fila parada, sem esperar o
        intervalo que cresceu enquanto não havia IA."""
        self._intervalo_dos_pendentes = INTERVALO_DOS_PENDENTES_S
        self._ultima_vez_dos_pendentes = float("-inf")
        self.acordar()

    def _espacar_os_pendentes(self) -> None:
        self._intervalo_dos_pendentes = min(self._intervalo_dos_pendentes * 2, INTERVALO_MAXIMO_DOS_PENDENTES_S)

    def _vetorizar_os_pendentes(self) -> None:
        """Com a fila parada, os trechos que ficaram sem vetor (transcritos antes de instalar a IA, ou com o Ollama
        fora) ganham o vetor, um lote por vez. Sem Ollama ou com erro, as tentativas se espaçam até
        INTERVALO_MAXIMO_DOS_PENDENTES_S: é trabalho de fundo, e a busca por palavra segue funcionando sem ele."""
        agora = time.monotonic()
        if agora - self._ultima_vez_dos_pendentes < self._intervalo_dos_pendentes:
            return
        self._ultima_vez_dos_pendentes = agora
        try:
            lote = self.banco.trechos_sem_vetor_de_todos(TRECHOS_POR_LOTE_DE_VETOR)
            if not lote:
                return
            vetores = self.vetorizar([t["texto"] for t in lote])
            if vetores is None:  # sem Ollama: tenta cada vez mais espaçado, para não repetir o mesmo aviso o dia inteiro
                self._espacar_os_pendentes()
                return
            self.banco.gravar_vetores({t["id"]: v for t, v in zip(lote, vetores, strict=True)})
        except Exception:  # disco cheio, banco travado: o traceback vai uma vez, depois só a linha, cada vez mais espaçado
            primeira = self._intervalo_dos_pendentes == INTERVALO_DOS_PENDENTES_S
            self._espacar_os_pendentes()
            if primeira:
                log.exception("os vetores dos trechos antigos falharam; tenta de novo em %.0f s", self._intervalo_dos_pendentes)
            else:
                log.error("os vetores dos trechos antigos falharam de novo; próxima em %.0f s", self._intervalo_dos_pendentes)
            return
        log.info("%d trecho(s) antigos ganharam o vetor da busca por significado", len(lote))
        self._intervalo_dos_pendentes = INTERVALO_DOS_PENDENTES_S
        if len(lote) == TRECHOS_POR_LOTE_DE_VETOR:  # ainda tem: o próximo lote vai na próxima volta da fila parada
            self._ultima_vez_dos_pendentes = float("-inf")

    def transcrever(self, audio: dict[str, Any]) -> None:
        """Um áudio do começo ao fim. Falha = áudio em 'erro' com o motivo na tela; a fila segue para o próximo."""
        audio_id = int(audio["id"])
        inicio = time.monotonic()
        gravados = 0
        na_placa = False  # só erro da etapa da placa (carregar e transcrever) derruba para a CPU
        self.avisos.publicar(audio_id, {"tipo": "estado", "estado": "transcrevendo", "modelo": audio["modelo"]})
        try:
            amostras = self.decodificar(str(self.pasta_audios / audio["arquivo"]))
            onda = contorno(amostras)
            self.banco.gravar_onda(audio_id, onda, len(amostras) / TAXA_DO_WHISPER)
            self.avisos.publicar(audio_id, {"tipo": "onda", "onda": onda, "duracao": len(amostras) / TAXA_DO_WHISPER})
            self.liberar_ollama()
            na_placa = self.placa.dispositivo == PLACA
            motor = self.placa.motor(audio["modelo"], self.precisao)
            trechos, info = motor.transcribe(
                amostras, language=None if audio["idioma"] == "auto" else audio["idioma"], vad_filter=True, beam_size=5
            )
            duracao = float(getattr(info, "duration", 0.0) or 0.0)
            for n, t in enumerate(trechos, 1):
                texto = t.text.strip()
                if not texto:
                    continue
                progresso = min(1.0, t.end / duracao) if duracao else 0.0
                trecho_id = self.banco.gravar_trecho(audio_id, n, float(t.start), float(t.end), texto, progresso)
                gravados += 1
                self.avisos.publicar(
                    audio_id,
                    {
                        "tipo": "trecho",
                        "id": trecho_id,
                        "n": n,
                        "inicio": t.start,
                        "fim": t.end,
                        "texto": texto,
                        "progresso": progresso,
                    },
                )
            segundos = time.monotonic() - inicio
            self.banco.concluir_audio(audio_id, duracao, str(getattr(info, "language", audio["idioma"])), segundos)
            self.avisos.publicar(audio_id, {"tipo": "estado", "estado": "pronto", "duracao": duracao, "segundos": segundos})
        except Exception as erro:
            if na_placa and not gravados and e_erro_da_placa(erro):
                # nada foi gravado ainda: o mesmo áudio recomeça na CPU, sem o usuário fazer nada
                self.placa.cair_para_a_cpu(f"{type(erro).__name__}: {erro}")
                self.precisao = PRECISAO_DA_CPU
                self.transcrever(audio)
                return
            log.exception("a transcrição do áudio %d falhou", audio_id)
            self.banco.falhar_audio(audio_id, f"{type(erro).__name__}: {erro}")
            self.avisos.publicar(audio_id, {"tipo": "estado", "estado": "erro", "erro": str(erro)})
            return
        try:
            self._vetorizar(audio_id)
        except Exception:  # o áudio já está pronto; o vetor fica para a próxima rodada dos pendentes
            log.exception("os vetores do áudio %d falharam", audio_id)

    def _vetorizar(self, audio_id: int) -> None:
        """Os vetores da memória longa. Sem o Ollama, fica sem (a busca por palavra continua achando)."""
        pendentes = self.banco.trechos_sem_vetor(audio_id)
        for i in range(0, len(pendentes), TRECHOS_POR_LOTE_DE_VETOR):
            lote = pendentes[i : i + TRECHOS_POR_LOTE_DE_VETOR]
            vetores = self.vetorizar([t["texto"] for t in lote])
            if vetores is None:
                log.warning("sem vetores para o áudio %d: a busca por significado não acha estes trechos", audio_id)
                return
            self.banco.gravar_vetores({t["id"]: v for t, v in zip(lote, vetores, strict=True)})
