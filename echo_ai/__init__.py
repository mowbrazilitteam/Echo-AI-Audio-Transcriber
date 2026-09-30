"""Echo AI Audio Transcriber: transcrição local (na placa de vídeo ou na CPU), com histórico, conversa e memória, tudo no
próprio computador."""

import os

# O download pelo Xet (o padrão do Hugging Face) só grava o arquivo no fim, e a barra de andamento ficaria parada até
# lá; o download comum grava aos poucos (transcricao.baixar_modelo mede no disco). Quem quiser o Xet põe 0 no ambiente.
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

__version__ = "1.0.0"
