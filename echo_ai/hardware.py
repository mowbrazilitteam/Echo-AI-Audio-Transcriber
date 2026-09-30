"""O que a máquina tem, para o app escolher sozinho onde roda (plug and play em Windows, Mac e Linux).

A regra: placa NVIDIA com driver funcionando = transcreve na placa (float16); qualquer outra coisa = transcreve na CPU
(int8), que roda em todo computador, só mais devagar. Placa de outra marca, e o Mac, usam a CPU: o motor do whisper
(CTranslate2) só acelera em NVIDIA. O driver da NVIDIA o app não instala (é do fabricante e pede administrador): sem
ele, a tela diz que a placa existe e que instalar o driver deixa a transcrição mais rápida.

As recomendações de modelo saem da memória: o whisper que cabe na placa ou na RAM, e a IA de 1 a 8 bilhões que cabe.
"""

import logging
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from importlib.machinery import ModuleSpec
from pathlib import Path

import psutil

log = logging.getLogger("echo")

GB = 1024**3
TEMPO_DO_NVIDIA_SMI_S = 5
PLACA, CPU = "cuda", "cpu"
PRECISAO_DA_PLACA, PRECISAO_DA_CPU = "float16", "int8"
# memória mínima para cada recomendação (GB); a folga deixa espaço para o sistema e para o navegador
VRAM_PARA_O_TURBO = 4.0
RAM_PARA_A_IA_DE_7B = 16.0
RAM_PARA_A_IA_DE_3B = 8.0
VRAM_PARA_A_IA_DE_7B = 6.0
IA_GRANDE, IA_MEDIA, IA_LEVE = "qwen2.5:7b", "qwen2.5:3b", "qwen2.5:1.5b"


@dataclass(frozen=True)
class Maquina:
    sistema: str  # windows | mac | linux
    arquitetura: str
    ram_gb: float
    placa: str | None  # a placa NVIDIA que funciona, ou None
    vram_gb: float | None
    placa_sem_driver: bool  # há NVIDIA mas o driver não responde: a tela sugere instalar
    aceleracao_pendente: bool  # NVIDIA com driver, mas sem as bibliotecas da CUDA: a tela oferece baixar o pacote
    dispositivo: str
    whisper_recomendado: str
    ia_recomendada: str
    nvidia_detectada: str | None = field(default=None)  # o nome da placa, mesmo quando ela ainda não está em uso

    def para_a_tela(self) -> dict[str, object]:
        return {
            "sistema": self.sistema,
            "arquitetura": self.arquitetura,
            "ram_gb": round(self.ram_gb, 1),
            "placa": self.placa,
            "vram_gb": round(self.vram_gb, 1) if self.vram_gb else None,
            "placa_sem_driver": self.placa_sem_driver,
            "aceleracao_pendente": self.aceleracao_pendente,
            "placa_nvidia": self.placa or self.nvidia_detectada,
            "dispositivo": self.dispositivo,
            "whisper_recomendado": self.whisper_recomendado,
            "ia_recomendada": self.ia_recomendada,
        }


def _sistema() -> str:
    return {"win32": "windows", "darwin": "mac"}.get(sys.platform, "linux")


def _placa_nvidia() -> tuple[str | None, float | None]:
    """(nome, memória em GB) da primeira placa NVIDIA pelo nvidia-smi, que vem com o driver; (None, None) sem ele."""
    programa = shutil.which("nvidia-smi")
    if not programa:
        return None, None
    try:
        saida = subprocess.run(
            [programa, "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=TEMPO_DO_NVIDIA_SMI_S,
            check=True,
        ).stdout.strip()
    except (subprocess.SubprocessError, OSError) as erro:
        log.warning("nvidia-smi não respondeu: %s", erro)
        return None, None
    if not saida:
        return None, None
    nome, _, memoria_mb = saida.splitlines()[0].rpartition(",")
    try:
        return nome.strip(), float(memoria_mb) / 1024
    except ValueError:
        return nome.strip() or None, None


def _tem_nvidia_sem_driver() -> bool:
    """Uma placa NVIDIA que o sistema enxerga, mas sem o driver (só Linux sabe dizer isso sem o driver)."""
    if _sistema() != "linux":
        return False
    try:
        with open("/proc/bus/pci/devices", encoding="ascii", errors="ignore") as arquivo:
            return any("\t10de" in linha for linha in arquivo)  # 10de = o código de fabricante da NVIDIA
    except OSError:
        return False


def _cuda_funciona() -> bool:
    import ctranslate2

    try:
        return ctranslate2.get_cuda_device_count() > 0 and PRECISAO_DA_PLACA in ctranslate2.get_supported_compute_types(PLACA)
    except RuntimeError as erro:  # o CTranslate2 achou a placa mas não a biblioteca da CUDA
        log.warning("a placa não está pronta para a CUDA: %s", erro)
        return False


def recomendar_whisper(dispositivo: str, vram_gb: float | None) -> str:
    if dispositivo == PLACA and (vram_gb or 0) >= VRAM_PARA_O_TURBO:
        return "large-v3-turbo"
    return "small"


def recomendar_ia(ram_gb: float, vram_gb: float | None) -> str:
    """A IA de 1 a 8 bilhões que cabe: com 16 GB de RAM ou placa de 6 GB, a de 7 B; com 8 GB, a de 3 B; abaixo, a leve."""
    if ram_gb >= RAM_PARA_A_IA_DE_7B or (vram_gb or 0) >= VRAM_PARA_A_IA_DE_7B:
        return IA_GRANDE
    if ram_gb >= RAM_PARA_A_IA_DE_3B:
        return IA_MEDIA
    return IA_LEVE


def especificacao_do_pacote(nome: str) -> ModuleSpec | None:
    """O find_spec que não quebra: para "nvidia.cublas" sem o pacote-pai "nvidia" instalado (o caso do app instalado,
    que não traz a CUDA), o find_spec levanta ModuleNotFoundError em vez de devolver None."""
    import importlib.util

    try:
        return importlib.util.find_spec(nome)
    except ModuleNotFoundError:
        return None


def _bibliotecas_da_cuda_presentes(pasta_da_aceleracao: Path) -> bool:
    """As bibliotecas que a placa precisa: as do pip (instalação de desenvolvimento) ou o pacote baixado pelo app."""
    from echo_ai import aceleracao

    return especificacao_do_pacote("nvidia.cublas") is not None or aceleracao.instalada(pasta_da_aceleracao)


def detectar(pasta_da_aceleracao: Path) -> Maquina:
    ram_gb = psutil.virtual_memory().total / GB
    nome, vram_gb = _placa_nvidia()
    driver_ok = nome is not None and _cuda_funciona()
    bibliotecas = _bibliotecas_da_cuda_presentes(pasta_da_aceleracao)
    na_placa = driver_ok and bibliotecas
    dispositivo = PLACA if na_placa else CPU
    maquina = Maquina(
        sistema=_sistema(),
        arquitetura=platform.machine().lower(),
        ram_gb=ram_gb,
        placa=nome if na_placa else None,
        vram_gb=vram_gb if na_placa else None,
        placa_sem_driver=nome is None and _tem_nvidia_sem_driver(),
        aceleracao_pendente=driver_ok and not bibliotecas,
        dispositivo=dispositivo,
        whisper_recomendado=recomendar_whisper(dispositivo, vram_gb if na_placa else None),
        ia_recomendada=recomendar_ia(ram_gb, vram_gb if na_placa else None),
        nvidia_detectada=nome if driver_ok else None,
    )
    log.info("máquina: %s", maquina.para_a_tela())
    return maquina
