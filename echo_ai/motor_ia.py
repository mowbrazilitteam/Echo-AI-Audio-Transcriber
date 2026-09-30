"""O motor da IA local (o Ollama), para o usuário não instalar nada à parte.

Na ordem: se o Ollama do sistema já responde (porta 11434), o app usa esse. Se não, e o app já baixou o Ollama portátil
dele, liga esse numa porta própria, com os modelos dentro da pasta do app. Se não há nenhum, a tela oferece instalar:
o app baixa o pacote oficial do Ollama para o sistema (versão fixa, conferida pela soma SHA-256 publicada pelo próprio
Ollama), extrai na pasta do app e liga. Nada pede administrador.
"""

import hashlib
import io
import logging
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import threading
import time
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import BinaryIO

import httpx

log = logging.getLogger("echo")

VERSAO_DO_OLLAMA = "v0.34.4"
ENDERECO_DO_PACOTE = "https://github.com/ollama/ollama/releases/download/{versao}/{arquivo}"
PORTA_PROPRIA = 11435
SEGUNDOS_PARA_LIGAR = 30
PEDACO_DO_DOWNLOAD = 1024 * 1024
TEMPO_DO_DOWNLOAD = httpx.Timeout(connect=10.0, read=120.0, write=30.0, pool=10.0)
SISTEMA, PROPRIO, AUSENTE = "sistema", "proprio", "ausente"


def pacote_do_sistema() -> str:
    """O arquivo do Ollama oficial para esta máquina (os nomes da página de versões do Ollama)."""
    arquitetura = "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "amd64"
    if sys.platform == "win32":
        return f"ollama-windows-{arquitetura}.zip"
    if sys.platform == "darwin":
        return "ollama-darwin.tgz"
    return f"ollama-linux-{arquitetura}.tar.zst"


def _extrair(pacote: Path, destino: Path, nome: str | None = None) -> None:
    """Extrai o pacote; `nome` é o nome original (o arquivo baixado fica com o sufixo .parcial até ser conferido)."""
    nome = nome or pacote.name
    if nome.endswith(".zip"):
        raiz = destino.resolve()
        with zipfile.ZipFile(pacote) as arquivo:
            for membro in arquivo.infolist():
                if not (raiz / membro.filename).resolve().is_relative_to(raiz):  # zip que tenta gravar fora da pasta
                    raise ValueError(f"o pacote {nome} tenta gravar fora da pasta: {membro.filename}")
                arquivo.extract(membro, raiz)
    elif nome.endswith(".tgz"):
        with tarfile.open(pacote, "r:gz") as arquivo:
            arquivo.extractall(destino, filter="data")
    elif nome.endswith(".tar.zst"):
        import zstandard

        with pacote.open("rb") as bruto:
            leitor = zstandard.ZstdDecompressor().stream_reader(bruto)
            with tarfile.open(fileobj=io.BufferedReader(leitor), mode="r|") as arquivo:
                arquivo.extractall(destino, filter="data")
    else:
        raise ValueError(f"formato de pacote desconhecido: {nome}")


def _apagar_a_extracao_pela_metade(pasta: Path) -> None:
    """A extração que caiu no meio não fica ocupando GBs até a próxima tentativa. Falhar aqui só vai para o log: o erro
    que importa é o da extração, que segue subindo."""
    try:
        shutil.rmtree(pasta)
    except FileNotFoundError:
        return
    except OSError as erro:
        log.warning("não consegui apagar a extração pela metade em %s: %s", pasta, erro)


class MotorDeIA:
    def __init__(self, pasta: Path, url_do_sistema: str, cliente: httpx.Client | None = None) -> None:
        self.pasta = pasta
        self.url_do_sistema = url_do_sistema
        self.url_propria = f"http://127.0.0.1:{PORTA_PROPRIA}"
        self._cliente = cliente or httpx.Client(timeout=TEMPO_DO_DOWNLOAD, follow_redirects=True)
        self._processo: subprocess.Popen[bytes] | None = None
        self._log_do_motor: BinaryIO | None = None
        self._trava = threading.Lock()
        self._trava_da_instalacao = threading.Lock()  # duas IAs instaladas em seguida não instalam o motor duas vezes

    @property
    def executavel(self) -> Path | None:
        nome = "ollama.exe" if sys.platform == "win32" else "ollama"
        programa = self.pasta / "programa"
        achados = sorted(programa.rglob(nome)) if programa.is_dir() else []
        return next((a for a in achados if a.is_file()), None)

    def _responde(self, url: str) -> bool:
        try:
            return self._cliente.get(url + "/api/version", timeout=2.0).status_code == 200
        except httpx.HTTPError:
            return False

    def situacao(self) -> str:
        if self._responde(self.url_do_sistema):
            return SISTEMA
        if self.executavel is not None:
            return PROPRIO
        return AUSENTE

    def url(self) -> str:
        """O endereço do motor em uso: o do sistema, ou o próprio (ligando se preciso). Sem motor: o do sistema, e a
        tela diz que a IA precisa ser instalada."""
        if self._responde(self.url_do_sistema):
            return self.url_do_sistema
        if self.executavel is not None:
            self.ligar()
            return self.url_propria
        return self.url_do_sistema

    def ligar(self) -> None:
        with self._trava:
            if self._processo is not None and self._processo.poll() is None:
                return
            if self._responde(self.url_propria):
                return  # sobrou de uma abertura anterior (o app fechou sem desligar): usa esse, em vez de brigar pela porta
            executavel = self.executavel
            if executavel is None:
                raise FileNotFoundError("o motor de IA ainda não foi instalado")
            ambiente = os.environ | {
                "OLLAMA_HOST": f"127.0.0.1:{PORTA_PROPRIA}",
                "OLLAMA_MODELS": str(self.pasta / "modelos"),
            }
            log.info("ligando o motor de IA próprio: %s", executavel)
            self._fechar_o_log()
            self._log_do_motor = (self.pasta / "motor.log").open("wb")  # um log por abertura: não cresce sem fim
            # o executável é o do pacote oficial do Ollama, conferido por SHA-256 na instalação
            self._processo = subprocess.Popen(
                [str(executavel), "serve"],
                env=ambiente,
                stdout=self._log_do_motor,
                stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),  # no Windows, sem janela de console
            )
        limite = time.monotonic() + SEGUNDOS_PARA_LIGAR
        while time.monotonic() < limite:
            if self._responde(self.url_propria):
                return
            if self._processo.poll() is not None:
                raise RuntimeError(f"o motor de IA parou ao ligar; veja {self.pasta / 'motor.log'}")
            time.sleep(0.5)
        self.desligar()  # não deixa um motor meio ligado segurando a porta
        raise TimeoutError(f"o motor de IA não respondeu em {SEGUNDOS_PARA_LIGAR} s")

    def _fechar_o_log(self) -> None:
        if self._log_do_motor is not None:
            self._log_do_motor.close()
            self._log_do_motor = None

    def desligar(self) -> None:
        with self._trava:
            if self._processo is not None and self._processo.poll() is None:
                self._processo.terminate()
                try:
                    self._processo.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self._processo.kill()
            self._processo = None
            self._fechar_o_log()

    def _soma_publicada(self, arquivo: str) -> str:
        endereco = ENDERECO_DO_PACOTE.format(versao=VERSAO_DO_OLLAMA, arquivo="sha256sum.txt")
        resposta = self._cliente.get(endereco)
        resposta.raise_for_status()
        for linha in resposta.text.splitlines():
            soma, _, nome = linha.strip().partition(" ")
            if nome.strip().lstrip("*./") == arquivo:
                return soma.lower()
        raise ValueError(f"o Ollama {VERSAO_DO_OLLAMA} não publicou a soma de {arquivo}")

    def instalar(self, avisar: Callable[[int, int], None]) -> None:
        """Baixa o pacote oficial, confere a SHA-256, extrai na pasta do app e liga. Um pacote que não confere é apagado
        e o erro sobe (a tela mostra). Com outra instalação andando, espera por ela e só liga."""
        with self._trava_da_instalacao:
            if self.executavel is not None:
                self.ligar()
                return
            self._instalar_sem_trava(avisar)

    def _instalar_sem_trava(self, avisar: Callable[[int, int], None]) -> None:
        arquivo = pacote_do_sistema()
        esperada = self._soma_publicada(arquivo)
        self.pasta.mkdir(parents=True, exist_ok=True)
        parcial = self.pasta / (arquivo + ".parcial")
        soma = hashlib.sha256()
        endereco = ENDERECO_DO_PACOTE.format(versao=VERSAO_DO_OLLAMA, arquivo=arquivo)
        try:
            with self._cliente.stream("GET", endereco) as resposta, parcial.open("wb") as saida:
                resposta.raise_for_status()
                total = int(resposta.headers.get("content-length") or 0)
                baixado = 0
                for pedaco in resposta.iter_bytes(PEDACO_DO_DOWNLOAD):
                    saida.write(pedaco)
                    soma.update(pedaco)
                    baixado += len(pedaco)
                    avisar(baixado, total)
            if soma.hexdigest() != esperada:
                raise ValueError(f"o pacote {arquivo} não confere com a soma publicada pelo Ollama")
            # extrai ao lado e só troca no fim: uma extração que cai no meio não deixa o binário sem as bibliotecas
            destino, extraindo = self.pasta / "programa", self.pasta / "programa.parcial"
            if extraindo.exists():
                shutil.rmtree(extraindo)
            try:
                _extrair(parcial, extraindo, arquivo)
            except BaseException:
                _apagar_a_extracao_pela_metade(extraindo)
                raise
            if destino.exists():
                shutil.rmtree(destino)
            os.replace(extraindo, destino)
        finally:
            parcial.unlink(missing_ok=True)
        executavel = self.executavel
        if executavel is None:
            raise FileNotFoundError(f"o pacote {arquivo} não trouxe o executável do Ollama")
        executavel.chmod(0o755)
        self.ligar()
