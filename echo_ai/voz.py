"""A resposta falada: a voz Piper do próprio PC lê o texto e o áudio fica guardado (a mesma frase não é gerada duas vezes)."""

import hashlib
import re
import subprocess
from pathlib import Path

TEMPO_MAXIMO_DA_VOZ_S = 120
CARACTERES_MAXIMOS = 6000
RITMO = "1.05"
VOZES = ("faber", "cadu")  # as vozes pt-BR da Piper em ~/.local/piper/vozes


class FalhaDaVoz(RuntimeError):
    """A Piper não gerou o áudio (erro, tempo esgotado ou arquivo vazio): a tela mostra o motivo."""


def limpar_para_voz(texto: str) -> str:
    """Tira marcação que a voz leria em voz alta: citações [1], asteriscos, cerquilhas e links."""
    texto = re.sub(r"\[\d+\]", "", texto)
    texto = re.sub(r"https?://\S+", "", texto)
    texto = re.sub(r"[*#_`>]+", "", texto)
    return re.sub(r"\s+", " ", texto).strip()[:CARACTERES_MAXIMOS]


def falar(texto: str, piper: Path, pasta_vozes: Path, voz: str, saida: Path) -> Path:
    """-> o .wav com a fala. Erro da Piper sobe (a tela mostra), nunca um arquivo vazio."""
    limpo = limpar_para_voz(texto)
    if not limpo:
        raise ValueError("não há texto para falar")
    nome = hashlib.sha256((voz + "|" + limpo).encode("utf-8")).hexdigest()[:24] + ".wav"
    destino = saida / nome
    if destino.is_file() and destino.stat().st_size > 0:
        return destino
    modelo = pasta_vozes / (f"pt_BR-{voz}-medium.onnx")
    temporario = destino.with_suffix(".parcial.wav")
    try:
        subprocess.run(
            [str(piper), "--model", str(modelo), "--output_file", str(temporario), "--length_scale", RITMO],
            input=limpo.encode("utf-8"),
            check=True,
            capture_output=True,
            timeout=TEMPO_MAXIMO_DA_VOZ_S,
        )
    except subprocess.CalledProcessError as erro:
        temporario.unlink(missing_ok=True)
        detalhe = (erro.stderr or b"").decode("utf-8", "replace").strip()[-300:]
        raise FalhaDaVoz(f"a Piper falhou: {detalhe or 'sem detalhe'}") from erro
    except subprocess.TimeoutExpired as erro:
        temporario.unlink(missing_ok=True)
        raise FalhaDaVoz(f"a Piper passou de {TEMPO_MAXIMO_DA_VOZ_S} s") from erro
    if not temporario.is_file() or temporario.stat().st_size == 0:
        temporario.unlink(missing_ok=True)
        raise FalhaDaVoz("a Piper não gerou o áudio")
    temporario.replace(destino)
    return destino
