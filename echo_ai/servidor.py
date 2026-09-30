"""Sobe o Echo: confere a configuração, detecta a máquina, prepara o banco, liga a fila e abre o servidor no próprio PC.

Uso:  python -m echo_ai.servidor     (só o servidor; o app com janela é python -m echo_ai)
"""

import logging
import sys
import threading
from logging.handlers import RotatingFileHandler
from types import TracebackType

import uvicorn

from echo_ai import aceleracao, hardware
from echo_ai.banco import Banco
from echo_ai.config import AUTOMATICO, Config
from echo_ai.motor_ia import MotorDeIA
from echo_ai.ollama import Ollama
from echo_ai.transcricao import Avisos, Fila, Motor, Placa, carregar_whisper
from echo_ai.web import Servicos, criar_app

TAMANHO_DO_LOG = 2 * 1024 * 1024
SEGUNDOS_PARA_FECHAR_CONEXOES = 5  # a tela deixa um fluxo de eventos aberto; sem limite, o desligar esperaria por ele


def montar(config: Config, maquina: hardware.Maquina) -> Servicos:
    config.preparar()
    banco = Banco(config.banco)
    banco.migrar()
    motor = MotorDeIA(config.pasta_dados / "motor-ia", config.ollama_url)
    ollama = Ollama(config.ollama_url)
    avisos = Avisos()
    dispositivo = maquina.dispositivo if config.whisper_dispositivo == AUTOMATICO else config.whisper_dispositivo
    # precisão automática segue o dispositivo em uso (float16 na placa, int8 na CPU): fixar a CPU com a placa presente
    # não pode deixar o float16, que a CPU não roda
    automatica = hardware.PRECISAO_DA_PLACA if dispositivo == hardware.PLACA else hardware.PRECISAO_DA_CPU
    precisao = automatica if config.whisper_precisao == AUTOMATICO else config.whisper_precisao

    def carregar(nome: str, disp: str, prec: str) -> Motor:  # o pacote de aceleração pode ter chegado depois da subida
        return carregar_whisper(nome, disp, prec, aceleracao.pastas_das_bibliotecas(config.pasta_da_aceleracao))

    placa = Placa(carregar, dispositivo, config.minutos_para_liberar_a_placa)
    fila = Fila(
        banco=banco,
        placa=placa,
        avisos=avisos,
        pasta_audios=config.pasta_audios,
        precisao=precisao,
        liberar_ollama=ollama.liberar_da_placa,
        vetorizar=lambda textos: ollama.vetores(config.modelo_de_busca, textos),
    )
    return Servicos(
        config=config,
        banco=banco,
        fila=fila,
        placa=placa,
        avisos=avisos,
        ollama=ollama,
        maquina=maquina,
        motor=motor,
        aceleracao=aceleracao.Aceleracao(config.pasta_da_aceleracao),
    )


def achar_o_motor_de_ia(servicos: Servicos) -> None:
    """Em segundo plano (ligar o motor próprio leva alguns segundos e a tela não pode esperar): o Ollama do sistema, ou
    o do app. Falhar aqui não derruba o app: a tela mostra que a IA precisa ser instalada ou religada."""
    try:
        servicos.ollama.trocar_url(servicos.motor.url())
    except (OSError, RuntimeError, TimeoutError) as erro:
        logging.getLogger("echo").warning("o motor de IA não ligou: %s", erro)


def registrar_os_erros_soltos() -> None:
    """Erro que escapa de uma thread ou derruba o processo vai para o echo.log (o usuário do app instalado não vê o
    terminal): sem isto a fila, o download ou a subida morriam só no stderr."""
    log = logging.getLogger("echo")

    def da_thread(args: threading.ExceptHookArgs) -> None:
        nome = args.thread.name if args.thread else "?"
        log.critical("erro solto na thread %s: %s: %s", nome, args.exc_type.__name__, args.exc_value, exc_info=args.exc_value)

    def do_processo(tipo: type[BaseException], valor: BaseException, rastro: TracebackType | None) -> None:
        if issubclass(tipo, KeyboardInterrupt):  # Ctrl+C não é erro
            sys.__excepthook__(tipo, valor, rastro)
            return
        log.critical("o Echo-AI parou com um erro", exc_info=(tipo, valor, rastro))

    threading.excepthook = da_thread
    sys.excepthook = do_processo


def configurar_o_log(config: Config) -> None:
    config.pasta_dados.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            RotatingFileHandler(config.pasta_dados / "echo.log", maxBytes=TAMANHO_DO_LOG, backupCount=3, encoding="utf-8"),
        ],
    )
    registrar_os_erros_soltos()


def servir(config: Config) -> None:
    """O servidor até ser desligado (Ctrl+C, a janela fechando, ou o sistema pedindo)."""
    servicos = montar(config, hardware.detectar(config.pasta_da_aceleracao))
    servicos.fila.iniciar()
    threading.Thread(target=achar_o_motor_de_ia, args=(servicos,), name="motor-de-ia", daemon=True).start()
    try:
        uvicorn.run(
            criar_app(servicos),
            host=config.host,
            port=config.porta,
            log_level="warning",
            log_config=None,  # o uvicorn usa o log do app: porta ocupada e erro 500 vão para o echo.log
            timeout_graceful_shutdown=SEGUNDOS_PARA_FECHAR_CONEXOES,
        )
    finally:
        servicos.fila.parar()
        servicos.placa.liberar()
        servicos.motor.desligar()


def main() -> None:
    config = Config()
    configurar_o_log(config)
    servir(config)


if __name__ == "__main__":
    main()
