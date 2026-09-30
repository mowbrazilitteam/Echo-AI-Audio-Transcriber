"""A aceleração na placa NVIDIA, baixada só por quem tem a placa (o instalador vem leve, sem os 2 GB da CUDA).

Com o driver da NVIDIA funcionando e sem as bibliotecas da CUDA, a tela oferece baixar o pacote de aceleração: os
wheels oficiais da NVIDIA no PyPI (cuBLAS, cuDNN e NVRTC), nas versões travadas no projeto, conferidos pela SHA-256
que o PyPI publica. Do wheel sai só a pasta das bibliotecas (lib no Linux, bin no Windows), para a pasta do app. O Mac
não tem CUDA: transcreve na CPU.
"""

import hashlib
import logging
import platform
import shutil
import sys
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx

log = logging.getLogger("echo")

# as versões do uv.lock (a combinação testada com o CTranslate2 4.8)
PACOTES = (("nvidia-cublas-cu12", "12.9.2.10"), ("nvidia-cudnn-cu12", "9.26.0.51"), ("nvidia-cuda-nvrtc-cu12", "12.9.86"))
ENDERECO_DO_PYPI = "https://pypi.org/pypi/{pacote}/{versao}/json"
PEDACO_DO_DOWNLOAD = 1024 * 1024
TEMPO_DO_DOWNLOAD = httpx.Timeout(connect=10.0, read=120.0, write=30.0, pool=10.0)
# o que tem de estar na pasta para a placa funcionar
ESSENCIAIS = {"linux": ("libcublas.so.12", "libcudnn.so.9"), "windows": ("cublas64_12.dll", "cudnn64_9.dll")}


def _sistema() -> str:
    return {"win32": "windows", "darwin": "mac"}.get(sys.platform, "linux")


def disponivel_neste_sistema() -> bool:
    return _sistema() in ESSENCIAIS


def _serve_para_esta_maquina(nome_do_arquivo: str) -> bool:
    if _sistema() == "windows":
        return nome_do_arquivo.endswith("win_amd64.whl")
    arquitetura = "aarch64" if platform.machine().lower() in ("arm64", "aarch64") else "x86_64"
    return "manylinux" in nome_do_arquivo and nome_do_arquivo.endswith(f"{arquitetura}.whl")


def pastas_das_bibliotecas(pasta: Path) -> list[Path]:
    """As pastas com as bibliotecas da CUDA dentro da pasta do app (para o carregamento da placa)."""
    subpasta = "bin" if _sistema() == "windows" else "lib"
    return sorted(p for p in pasta.glob(f"nvidia/*/{subpasta}") if p.is_dir())


def instalada(pasta: Path) -> bool:
    essenciais = ESSENCIAIS.get(_sistema(), ())
    presentes = {arquivo.name for p in pastas_das_bibliotecas(pasta) for arquivo in p.iterdir()}
    return bool(essenciais) and all(nome in presentes for nome in essenciais)


@dataclass(frozen=True)
class ArquivoDoPypi:
    """O que interessa de um arquivo na resposta JSON do PyPI (o wheel desta máquina)."""

    nome: str
    url: str
    tamanho: int
    sha256: str


class Aceleracao:
    def __init__(self, pasta: Path, cliente: httpx.Client | None = None) -> None:
        self.pasta = pasta
        self._cliente = cliente or httpx.Client(timeout=TEMPO_DO_DOWNLOAD, follow_redirects=True)

    def _arquivo_do_pacote(self, pacote: str, versao: str) -> ArquivoDoPypi:
        resposta = self._cliente.get(ENDERECO_DO_PYPI.format(pacote=pacote, versao=versao))
        resposta.raise_for_status()
        for arquivo in resposta.json().get("urls", []):
            if _serve_para_esta_maquina(arquivo["filename"]):
                return ArquivoDoPypi(
                    nome=str(arquivo["filename"]),
                    url=str(arquivo["url"]),
                    tamanho=int(arquivo["size"]),
                    sha256=str(arquivo["digests"]["sha256"]),
                )
        raise ValueError(f"o PyPI não tem {pacote} {versao} para esta máquina")

    def _extrair_bibliotecas(self, wheel: Path) -> None:
        subpasta = "/bin/" if _sistema() == "windows" else "/lib/"
        raiz = self.pasta.resolve()
        with zipfile.ZipFile(wheel) as arquivo:
            for membro in arquivo.infolist():
                if not membro.filename.startswith("nvidia/") or subpasta not in membro.filename or membro.is_dir():
                    continue
                alvo = (raiz / membro.filename).resolve()
                if not alvo.is_relative_to(raiz):
                    raise ValueError(f"o pacote tenta gravar fora da pasta: {membro.filename}")
                alvo.parent.mkdir(parents=True, exist_ok=True)
                with arquivo.open(membro) as origem, alvo.open("wb") as destino:
                    shutil.copyfileobj(origem, destino)

    def instalar(self, avisar: Callable[[int, int], None]) -> None:
        """Baixa e extrai os três pacotes, um por vez, com o andamento somado. Um pacote que não confere com a
        SHA-256 do PyPI é apagado e o erro sobe (a tela mostra)."""
        if not disponivel_neste_sistema():
            raise ValueError("este sistema não tem CUDA: a transcrição usa a CPU")
        arquivos = [self._arquivo_do_pacote(pacote, versao) for pacote, versao in PACOTES]
        total = sum(a.tamanho for a in arquivos)
        baixado = 0
        self.pasta.mkdir(parents=True, exist_ok=True)
        for arquivo in arquivos:
            parcial = self.pasta / (arquivo.nome + ".parcial")
            soma = hashlib.sha256()
            try:
                with self._cliente.stream("GET", arquivo.url) as resposta, parcial.open("wb") as saida:
                    resposta.raise_for_status()
                    for pedaco in resposta.iter_bytes(PEDACO_DO_DOWNLOAD):
                        saida.write(pedaco)
                        soma.update(pedaco)
                        baixado += len(pedaco)
                        avisar(baixado, total)
                if soma.hexdigest() != arquivo.sha256:
                    raise ValueError(f"{arquivo.nome} não confere com a soma publicada pelo PyPI")
                self._extrair_bibliotecas(parcial)
            finally:
                parcial.unlink(missing_ok=True)
        if not instalada(self.pasta):
            raise FileNotFoundError("os pacotes da NVIDIA não trouxeram as bibliotecas esperadas")
        log.info("aceleração da placa instalada em %s", self.pasta)
