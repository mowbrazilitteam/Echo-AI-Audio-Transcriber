"""Os downloads do app (modelos do whisper, IAs, o motor de IA), cada um numa thread, com o andamento para a tela.

É a única parte do app que usa a internet, e só quando o usuário clica em baixar. A tela pergunta o andamento a cada
segundo enquanto houver download (GET /api/downloads); um download que já está andando não começa de novo.
"""

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass

log = logging.getLogger("echo")

BAIXANDO, PRONTO, ERRO = "baixando", "pronto", "erro"
Avisar = Callable[[int, int], None]  # (bytes baixados, bytes no total; 0 = ainda não se sabe)


@dataclass
class Andamento:
    tipo: str  # whisper | ia | motor
    nome: str
    estado: str = BAIXANDO
    fase: str | None = None  # motor | modelo | busca, quando o download tem mais de uma parte
    baixado: int = 0
    total: int = 0
    erro: str | None = None

    def para_a_tela(self) -> dict[str, object]:
        return {
            "tipo": self.tipo,
            "nome": self.nome,
            "estado": self.estado,
            "fase": self.fase,
            "baixado": self.baixado,
            "total": self.total,
            "fracao": round(self.baixado / self.total, 4) if self.total else None,
            "erro": self.erro,
        }


class Relato:
    """O que o trabalho de download recebe: chamado como Avisar (baixado, total), e `fase` quando muda de parte."""

    def __init__(self, andamento: Andamento, trava: threading.Lock) -> None:
        self._andamento, self._trava = andamento, trava

    def __call__(self, baixado: int, total: int) -> None:
        with self._trava:
            self._andamento.baixado, self._andamento.total = baixado, max(total, self._andamento.total)

    def fase(self, nome: str) -> None:
        with self._trava:
            self._andamento.fase, self._andamento.baixado, self._andamento.total = nome, 0, 0


Trabalho = Callable[[Relato], None]


class Downloads:
    def __init__(self) -> None:
        self._trava = threading.Lock()
        self._andamentos: dict[tuple[str, str], Andamento] = {}
        self._threads: list[threading.Thread] = []

    def iniciar(self, tipo: str, nome: str, trabalho: Trabalho) -> bool:
        """Começa o download numa thread. False se esse mesmo download já está andando."""
        chave = (tipo, nome)
        with self._trava:
            atual = self._andamentos.get(chave)
            if atual is not None and atual.estado == BAIXANDO:
                return False
            andamento = Andamento(tipo, nome)
            self._andamentos[chave] = andamento

        relato = Relato(andamento, self._trava)

        def rodar() -> None:
            try:
                trabalho(relato)
            except Exception as erro:
                log.exception("o download de %s %s falhou", tipo, nome)
                with self._trava:
                    andamento.estado, andamento.erro = ERRO, f"{type(erro).__name__}: {erro}"
                return
            with self._trava:
                andamento.estado = PRONTO
                andamento.baixado = andamento.total or andamento.baixado

        thread = threading.Thread(target=rodar, name=f"download-{tipo}-{nome}", daemon=True)
        with self._trava:
            self._threads = [t for t in self._threads if t.is_alive()] + [thread]
        thread.start()
        return True

    def esperar(self, segundos: float) -> None:
        """Espera os downloads em andamento terminarem (os testes e o desligar do app)."""
        with self._trava:
            threads = list(self._threads)
        for thread in threads:
            thread.join(segundos)

    def estado(self) -> list[dict[str, object]]:
        with self._trava:
            return [a.para_a_tela() for a in self._andamentos.values()]

    def andando(self) -> bool:
        with self._trava:
            return any(a.estado == BAIXANDO for a in self._andamentos.values())
