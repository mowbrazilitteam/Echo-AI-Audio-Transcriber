"""A configuração do Echo, conferida quando o app sobe.

Tudo tem valor padrão para o app ser plug and play: quem instala não configura nada. Quem quiser trocar põe no `.env`
(prefixo ECHO_, ver `.env.example`). O que é obrigatório para o app funcionar (a pasta de dados gravável) é conferido na
subida e, se falhar, o app não sobe. O que é opcional (a voz Piper, a IA local) é conferido também, mas só desliga a
função e diz por quê na tela: a transcrição continua.
"""

import os
import sys
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

NOME_DO_APP = "Echo-AI"
NOME_COMPLETO = "Echo AI Audio Transcriber"
AUTOMATICO = "auto"  # dispositivo e precisão escolhidos pelo hardware (echo_ai.hardware)


def pasta_de_dados_do_sistema() -> Path:
    """Onde cada sistema guarda os dados de um app do usuário (sem pedir administrador)."""
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / NOME_DO_APP
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / NOME_DO_APP
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / NOME_DO_APP.lower()


class Config(BaseSettings):
    # extra="ignore": um .env de outro projeto na pasta atual (DATABASE_URL, ...) não derruba o app; os ECHO_ continuam
    # validados pelo tipo de cada campo
    model_config = SettingsConfigDict(env_prefix="ECHO_", env_file=".env", extra="ignore")

    pasta_dados: Path = Field(default_factory=pasta_de_dados_do_sistema)
    host: str = "127.0.0.1"  # só o próprio PC; nunca 0.0.0.0
    porta: int = Field(default=8765, ge=1024, le=65535)
    whisper_modelo: str = "small"  # o que vem com o instalador: roda bem em CPU e em qualquer placa
    whisper_precisao: str = AUTOMATICO
    whisper_dispositivo: str = AUTOMATICO
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_modelo: str = ""  # vazio = a IA recomendada para a máquina, se estiver instalada
    modelo_de_busca: str = "nomic-embed-text"  # os vetores da memória longa (busca por significado)
    piper_binario: Path | None = None  # None = a Piper dentro da pasta de dados (pasta_da_piper)
    piper_voz: str = "faber"
    minutos_para_liberar_a_placa: int = Field(default=3, ge=1, le=120)
    tamanho_maximo_do_audio_mb: int = Field(default=4096, ge=1)

    @property
    def banco(self) -> Path:
        return self.pasta_dados / "echo.db"

    @property
    def pasta_audios(self) -> Path:
        return self.pasta_dados / "audios"

    @property
    def pasta_vozes_geradas(self) -> Path:
        return self.pasta_dados / "voz"

    @property
    def pasta_da_aceleracao(self) -> Path:
        return self.pasta_dados / "aceleracao"

    @property
    def pasta_da_piper(self) -> Path:
        return self.pasta_dados / "piper"

    @property
    def piper(self) -> Path:
        executavel = "piper.exe" if sys.platform == "win32" else "piper"
        return self.piper_binario or self.pasta_da_piper / "piper" / executavel

    @property
    def piper_vozes(self) -> Path:
        return self.pasta_da_piper / "vozes"

    def preparar(self) -> None:
        """Cria as pastas e confere que dá para gravar nelas. Sem isso o app não sobe (erro alto, não silêncio)."""
        for pasta in (self.pasta_dados, self.pasta_audios, self.pasta_vozes_geradas):
            pasta.mkdir(parents=True, exist_ok=True)
            teste = pasta / ".gravavel"
            teste.write_text("ok", encoding="utf-8")
            teste.unlink()
        if self.host not in ("127.0.0.1", "localhost"):
            raise ValueError(f"o {NOME_DO_APP} só escuta o próprio PC (127.0.0.1); host recebido: {self.host!r}")

    def voz_disponivel(self) -> str | None:
        """None se a Piper está pronta; senão, o motivo (a tela mostra e desliga a resposta falada)."""
        if not self.piper.is_file():
            return f"a Piper não está em {self.piper}"
        if not (self.piper_vozes / (f"pt_BR-{self.piper_voz}-medium.onnx")).is_file():
            return f"a voz {self.piper_voz} não está em {self.piper_vozes}"
        return None
