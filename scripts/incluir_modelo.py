"""Põe um modelo do whisper dentro do pacote do app, para o instalador já trazer (o GitHub Actions roda antes de
empacotar). Uso: python scripts/incluir_modelo.py small"""

import sys
from pathlib import Path

from huggingface_hub import snapshot_download

from echo_ai.transcricao import ARQUIVOS_DO_MODELO, NOMES_DO_CATALOGO, PASTA_DOS_MODELOS_INCLUIDOS, REVISAO, repositorio_do_modelo


def main(nome: str) -> None:
    if nome not in NOMES_DO_CATALOGO:
        raise SystemExit(f"modelo fora do catálogo: {nome}")
    destino = PASTA_DOS_MODELOS_INCLUIDOS / nome
    snapshot_download(repositorio_do_modelo(nome), revision=REVISAO[nome], allow_patterns=ARQUIVOS_DO_MODELO, local_dir=destino)
    tamanho = sum(f.stat().st_size for f in Path(destino).rglob("*") if f.is_file())
    print(f"{nome} incluído em {destino} ({tamanho / 1e6:.0f} MB)")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "small")
