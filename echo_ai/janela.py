"""O app instalado: sobe o servidor numa thread e abre a tela numa janela própria do sistema (pywebview: o WebView2 no
Windows, o WebKit no Mac, o Qt WebEngine no Linux). Fechar a janela desliga tudo: a fila, a placa e o motor de IA.

Se o app já está aberto (a porta responde), abre só outra janela para ele. Se a janela não abre (sistema sem interface
gráfica, biblioteca do sistema faltando), a tela abre no navegador padrão e o servidor fica de pé até Ctrl+C.

Uso:  echo-ai                (o atalho do instalador)
      echo-ai --navegador    (abre no navegador padrão em vez da janela)
"""

import argparse
import logging
import sys
import threading
import time
import webbrowser

import httpx
import uvicorn

from echo_ai import hardware
from echo_ai.config import NOME_DO_APP, Config
from echo_ai.servidor import SEGUNDOS_PARA_FECHAR_CONEXOES, achar_o_motor_de_ia, configurar_o_log, montar
from echo_ai.web import criar_app

log = logging.getLogger("echo")

SEGUNDOS_PARA_SUBIR = 30
TAMANHO_DA_JANELA = (1280, 860)
MENOR_JANELA = (420, 600)


def _ja_esta_aberto(endereco: str) -> bool:
    try:
        return httpx.get(endereco + "/health", timeout=1.0).status_code == 200
    except httpx.HTTPError:
        return False


def _abrir_janela(endereco: str, config: Config) -> bool:
    """Abre a janela e espera ela fechar. False se a janela não abriu (quem chama cai para o navegador)."""
    try:
        import webview
    except ImportError as erro:
        log.warning("sem a biblioteca da janela (%s): abrindo no navegador", erro)
        return False
    try:
        # text_select: a transcrição tem que dar para selecionar e copiar; sem ele o pywebview injeta um <style> que a
        # CSP da página recusa
        webview.create_window(
            NOME_DO_APP,
            endereco,
            width=TAMANHO_DA_JANELA[0],
            height=TAMANHO_DA_JANELA[1],
            min_size=MENOR_JANELA,
            text_select=True,
        )
        # a pasta própria guarda o que a tela lembra (o tema) e as permissões, como o microfone
        # no Linux, o Qt direto (o pywebview tenta o GTK antes, e o erro do GTK apontaria a causa errada)
        pasta = str(config.pasta_dados / "janela")
        if sys.platform.startswith("linux"):
            webview.start(gui="qt", private_mode=False, storage_path=pasta)
        else:
            webview.start(private_mode=False, storage_path=pasta)
    except Exception as erro:  # cada sistema falha de um jeito (GTK, Qt, WebView2 ausente): a tela abre no navegador
        log.warning("a janela não abriu (%s: %s): abrindo no navegador", type(erro).__name__, erro)
        return False
    return True


def _abrir_no_navegador(endereco: str) -> None:
    if webbrowser.open(endereco):
        log.info("%s aberto no navegador em %s", NOME_DO_APP, endereco)
    else:
        log.error("não consegui abrir o navegador: abra %s à mão", endereco)


def _esperar_no_navegador(endereco: str) -> None:
    _abrir_no_navegador(endereco)
    log.info("Ctrl+C para desligar o %s", NOME_DO_APP)
    while True:
        time.sleep(3600)


def main() -> None:
    argumentos = argparse.ArgumentParser(prog="echo-ai", description="Echo AI Audio Transcriber")
    argumentos.add_argument("--navegador", action="store_true", help="abre no navegador padrão em vez da janela do app")
    opcoes = argumentos.parse_args()

    config = Config()
    configurar_o_log(config)
    endereco = f"http://{config.host}:{config.porta}"
    if _ja_esta_aberto(endereco):
        if opcoes.navegador or not _abrir_janela(endereco, config):
            _abrir_no_navegador(endereco)
        return

    servicos = montar(config, hardware.detectar(config.pasta_da_aceleracao))
    servicos.fila.iniciar()
    threading.Thread(target=achar_o_motor_de_ia, args=(servicos,), name="motor-de-ia", daemon=True).start()
    servidor = uvicorn.Server(
        uvicorn.Config(
            criar_app(servicos),
            host=config.host,
            port=config.porta,
            log_level="warning",
            log_config=None,  # o uvicorn usa o log do app (echo.log)
            timeout_graceful_shutdown=SEGUNDOS_PARA_FECHAR_CONEXOES,
        )
    )
    tarefa = threading.Thread(target=servidor.run, name="servidor", daemon=True)
    tarefa.start()
    try:
        limite = time.monotonic() + SEGUNDOS_PARA_SUBIR
        while not _ja_esta_aberto(endereco):
            if not tarefa.is_alive() or time.monotonic() > limite:
                raise RuntimeError(f"o servidor não subiu em {endereco}; veja {config.pasta_dados / 'echo.log'}")
            time.sleep(0.2)
        if opcoes.navegador or not _abrir_janela(endereco, config):
            _esperar_no_navegador(endereco)
    except KeyboardInterrupt:
        pass
    finally:
        servidor.should_exit = True
        tarefa.join(timeout=SEGUNDOS_PARA_FECHAR_CONEXOES + 5)
        servicos.fila.parar()
        servicos.placa.liberar()
        servicos.motor.desligar()


if __name__ == "__main__":
    main()
