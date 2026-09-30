"""O banco (SQLite, um processo só): conversas, mensagens, áudios, trechos transcritos e ajustes.

Tudo o que é gravado fica aqui e na pasta de áudios; datas sempre em UTC (a tela converte). Os trechos têm busca por
palavra (FTS5) e, quando o Ollama está de pé, o vetor de significado (memória longa). Cada função abre e fecha a sua
conexão: o app tem uma thread de transcrição e as requisições da tela, e o SQLite em WAL aguenta isso.

Esquema versionado por PRAGMA user_version: cada entrada de MIGRACOES sobe uma versão, dentro de uma transação.
"""

import json
import sqlite3
import struct
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ESTADOS_DO_AUDIO = ("na_fila", "transcrevendo", "pronto", "erro")
PAPEIS = ("usuario", "assistente", "audio")
ESPERA_PELO_BANCO_S = 10

MIGRACOES = [
    """
    CREATE TABLE conversas (
        id INTEGER PRIMARY KEY,
        titulo TEXT NOT NULL,
        criada_em TEXT NOT NULL,
        atualizada_em TEXT NOT NULL
    );
    CREATE TABLE audios (
        id INTEGER PRIMARY KEY,
        conversa_id INTEGER NOT NULL REFERENCES conversas(id) ON DELETE CASCADE,
        nome_original TEXT NOT NULL,
        arquivo TEXT NOT NULL,
        modelo TEXT NOT NULL,
        idioma TEXT NOT NULL,
        estado TEXT NOT NULL CHECK (estado IN ('na_fila', 'transcrevendo', 'pronto', 'erro')),
        progresso REAL NOT NULL DEFAULT 0,
        duracao REAL,
        idioma_detectado TEXT,
        erro TEXT,
        segundos_de_trabalho REAL,
        onda TEXT,
        criado_em TEXT NOT NULL,
        terminado_em TEXT
    );
    CREATE INDEX ix_audios_estado ON audios(estado, id);
    CREATE INDEX ix_audios_conversa ON audios(conversa_id);
    CREATE TABLE trechos (
        id INTEGER PRIMARY KEY,
        audio_id INTEGER NOT NULL REFERENCES audios(id) ON DELETE CASCADE,
        n INTEGER NOT NULL,
        inicio REAL NOT NULL,
        fim REAL NOT NULL,
        texto TEXT NOT NULL,
        vetor BLOB,
        UNIQUE (audio_id, n)
    );
    CREATE VIRTUAL TABLE trechos_fts USING fts5(texto, content='trechos', content_rowid='id',
                                                tokenize='unicode61 remove_diacritics 2');
    CREATE TRIGGER trechos_ai AFTER INSERT ON trechos BEGIN
        INSERT INTO trechos_fts(rowid, texto) VALUES (new.id, new.texto);
    END;
    CREATE TRIGGER trechos_ad AFTER DELETE ON trechos BEGIN
        INSERT INTO trechos_fts(trechos_fts, rowid, texto) VALUES ('delete', old.id, old.texto);
    END;
    CREATE TABLE mensagens (
        id INTEGER PRIMARY KEY,
        conversa_id INTEGER NOT NULL REFERENCES conversas(id) ON DELETE CASCADE,
        papel TEXT NOT NULL CHECK (papel IN ('usuario', 'assistente', 'audio')),
        texto TEXT NOT NULL,
        audio_id INTEGER REFERENCES audios(id) ON DELETE CASCADE,
        fontes TEXT,
        modelo TEXT,
        criada_em TEXT NOT NULL
    );
    CREATE INDEX ix_mensagens_conversa ON mensagens(conversa_id, id);
    CREATE TABLE ajustes (chave TEXT PRIMARY KEY, valor TEXT NOT NULL);
    """,
]


def agora() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class Banco:
    caminho: Path

    @contextmanager
    def conexao(self) -> Iterator[sqlite3.Connection]:
        c = sqlite3.connect(self.caminho, timeout=ESPERA_PELO_BANCO_S, isolation_level=None)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys = ON")
        try:
            yield c
        finally:
            c.close()

    @contextmanager
    def transacao(self) -> Iterator[sqlite3.Connection]:
        with self.conexao() as c:
            c.execute("BEGIN IMMEDIATE")
            try:
                yield c
            except BaseException:
                c.execute("ROLLBACK")
                raise
            c.execute("COMMIT")

    def migrar(self) -> int:
        """Sobe o esquema até a última versão. -> a versão final."""
        with self.conexao() as c:
            c.execute("PRAGMA journal_mode = WAL")
            versao = c.execute("PRAGMA user_version").fetchone()[0]
        for numero, sql in enumerate(MIGRACOES[versao:], start=versao + 1):
            # executescript: os gatilhos têm ';' dentro do BEGIN ... END. A versão sobe na mesma transação.
            with self.conexao() as c:
                try:
                    c.executescript(f"BEGIN IMMEDIATE;\n{sql}\nPRAGMA user_version = {numero};\nCOMMIT;")
                except sqlite3.Error:
                    if c.in_transaction:
                        c.execute("ROLLBACK")
                    raise
        return len(MIGRACOES)

    # ── conversas ──────────────────────────────────────────────────────────────────────────
    def criar_conversa(self, titulo: str = "Nova transcrição") -> int:
        with self.transacao() as c:
            cur = c.execute(
                "INSERT INTO conversas (titulo, criada_em, atualizada_em) VALUES (?, ?, ?)", (titulo, agora(), agora())
            )
            return int(cur.lastrowid or 0)

    def conversas(self, busca: str = "", limite: int = 200) -> list[dict[str, Any]]:
        """As conversas, da mais recente para a mais antiga; com `busca`, as que têm a palavra no título ou no texto."""
        with self.conexao() as c:
            if not busca.strip():
                linhas = c.execute(
                    "SELECT id, titulo, criada_em, atualizada_em FROM conversas ORDER BY atualizada_em DESC, id DESC LIMIT ?",
                    (limite,),
                ).fetchall()
            else:
                termo = consulta_fts(busca)
                linhas = c.execute(
                    "SELECT DISTINCT cv.id, cv.titulo, cv.criada_em, cv.atualizada_em FROM conversas cv "
                    "LEFT JOIN audios a ON a.conversa_id = cv.id LEFT JOIN trechos t ON t.audio_id = a.id "
                    "WHERE cv.titulo LIKE ? OR t.id IN (SELECT rowid FROM trechos_fts WHERE trechos_fts MATCH ?) "
                    "ORDER BY cv.atualizada_em DESC LIMIT ?",
                    ("%" + busca.strip() + "%", termo, limite),
                ).fetchall()
        return [dict(linha) for linha in linhas]

    def conversa(self, conversa_id: int) -> dict[str, Any] | None:
        with self.conexao() as c:
            linha = c.execute(
                "SELECT id, titulo, criada_em, atualizada_em FROM conversas WHERE id = ?", (conversa_id,)
            ).fetchone()
        return dict(linha) if linha else None

    def renomear(self, conversa_id: int, titulo: str) -> bool:
        with self.transacao() as c:
            return (
                c.execute(
                    "UPDATE conversas SET titulo = ?, atualizada_em = ? WHERE id = ?",
                    (titulo.strip()[:120] or "Sem título", agora(), conversa_id),
                ).rowcount
                == 1
            )

    def apagar_conversa(self, conversa_id: int) -> list[str]:
        """Apaga a conversa e tudo dela. -> os arquivos de áudio que ela tinha (quem chama apaga do disco)."""
        with self.transacao() as c:
            arquivos = [r[0] for r in c.execute("SELECT arquivo FROM audios WHERE conversa_id = ?", (conversa_id,))]
            c.execute("DELETE FROM conversas WHERE id = ?", (conversa_id,))
        return arquivos

    def tocar(self, c: sqlite3.Connection, conversa_id: int) -> None:
        c.execute("UPDATE conversas SET atualizada_em = ? WHERE id = ?", (agora(), conversa_id))

    # ── mensagens ──────────────────────────────────────────────────────────────────────────
    def acrescentar_mensagem(
        self,
        conversa_id: int,
        papel: str,
        texto: str,
        audio_id: int | None = None,
        fontes: list[dict[str, Any]] | None = None,
        modelo: str | None = None,
    ) -> int:
        if papel not in PAPEIS:
            raise ValueError(f"papel desconhecido: {papel!r}")
        with self.transacao() as c:
            cur = c.execute(
                "INSERT INTO mensagens (conversa_id, papel, texto, audio_id, fontes, modelo, criada_em) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    conversa_id,
                    papel,
                    texto,
                    audio_id,
                    json.dumps(fontes, ensure_ascii=False) if fontes else None,
                    modelo,
                    agora(),
                ),
            )
            self.tocar(c, conversa_id)
            return int(cur.lastrowid or 0)

    def mensagens(self, conversa_id: int) -> list[dict[str, Any]]:
        with self.conexao() as c:
            linhas = c.execute(
                "SELECT id, papel, texto, audio_id, fontes, modelo, criada_em FROM mensagens WHERE conversa_id = ? ORDER BY id",
                (conversa_id,),
            ).fetchall()
        saida = []
        for linha in linhas:
            m = dict(linha)
            m["fontes"] = json.loads(m["fontes"]) if m["fontes"] else []
            saida.append(m)
        return saida

    # ── áudios e trechos ───────────────────────────────────────────────────────────────────
    def acrescentar_audio(self, conversa_id: int, nome_original: str, arquivo: str, modelo: str, idioma: str) -> int:
        with self.transacao() as c:
            cur = c.execute(
                "INSERT INTO audios (conversa_id, nome_original, arquivo, modelo, idioma, estado, criado_em) "
                "VALUES (?, ?, ?, ?, ?, 'na_fila', ?)",
                (conversa_id, nome_original, arquivo, modelo, idioma, agora()),
            )
            audio_id = int(cur.lastrowid or 0)
            c.execute(
                "INSERT INTO mensagens (conversa_id, papel, texto, audio_id, criada_em) VALUES (?, 'audio', ?, ?, ?)",
                (conversa_id, nome_original, audio_id, agora()),
            )
            self.tocar(c, conversa_id)
            return audio_id

    def audio(self, audio_id: int) -> dict[str, Any] | None:
        with self.conexao() as c:
            linha = c.execute("SELECT * FROM audios WHERE id = ?", (audio_id,)).fetchone()
        return dict(linha) if linha else None

    def audios_da_conversa(self, conversa_id: int) -> list[dict[str, Any]]:
        with self.conexao() as c:
            return [dict(r) for r in c.execute("SELECT * FROM audios WHERE conversa_id = ? ORDER BY id", (conversa_id,))]

    def proximo_da_fila(self) -> dict[str, Any] | None:
        """Pega o próximo áudio da fila e já o marca como 'transcrevendo' (numa transação só: dois nunca pegam o mesmo)."""
        with self.transacao() as c:
            linha = c.execute("SELECT * FROM audios WHERE estado = 'na_fila' ORDER BY id LIMIT 1").fetchone()
            if not linha:
                return None
            c.execute("UPDATE audios SET estado = 'transcrevendo', progresso = 0, erro = NULL WHERE id = ?", (linha["id"],))
            c.execute("DELETE FROM trechos WHERE audio_id = ?", (linha["id"],))  # recomeço limpo depois de uma queda
            return dict(linha) | {"estado": "transcrevendo"}

    def ha_trabalho_na_fila(self) -> bool:
        with self.conexao() as c:
            return c.execute("SELECT 1 FROM audios WHERE estado IN ('na_fila', 'transcrevendo') LIMIT 1").fetchone() is not None

    def devolver_os_interrompidos(self) -> int:
        """Na subida: o que ficou 'transcrevendo' (o app caiu no meio) volta para a fila."""
        with self.transacao() as c:
            return c.execute("UPDATE audios SET estado = 'na_fila' WHERE estado = 'transcrevendo'").rowcount

    def gravar_trecho(self, audio_id: int, n: int, inicio: float, fim: float, texto: str, progresso: float) -> int:
        with self.transacao() as c:
            cur = c.execute(
                "INSERT INTO trechos (audio_id, n, inicio, fim, texto) VALUES (?, ?, ?, ?, ?)", (audio_id, n, inicio, fim, texto)
            )
            c.execute("UPDATE audios SET progresso = ? WHERE id = ?", (progresso, audio_id))
            return int(cur.lastrowid or 0)

    def gravar_onda(self, audio_id: int, onda: list[float], duracao: float) -> None:
        """O contorno do áudio (picos de 0 a 1) que a tela desenha como tocador, e a duração real."""
        with self.transacao() as c:
            c.execute(
                "UPDATE audios SET onda = ?, duracao = ? WHERE id = ?",
                (json.dumps([round(x, 3) for x in onda]), duracao, audio_id),
            )

    def concluir_audio(self, audio_id: int, duracao: float, idioma_detectado: str, segundos: float) -> None:
        with self.transacao() as c:
            c.execute(
                "UPDATE audios SET estado = 'pronto', progresso = 1, duracao = ?, idioma_detectado = ?, "
                "segundos_de_trabalho = ?, terminado_em = ? WHERE id = ?",
                (duracao, idioma_detectado, segundos, agora(), audio_id),
            )

    def falhar_audio(self, audio_id: int, erro: str) -> None:
        with self.transacao() as c:
            c.execute(
                "UPDATE audios SET estado = 'erro', erro = ?, terminado_em = ? WHERE id = ?", (erro[:2000], agora(), audio_id)
            )

    def refazer_audio(self, audio_id: int, modelo: str, idioma: str) -> bool:
        """Põe o áudio de novo na fila (outro modelo ou idioma): os trechos antigos saem quando ele for pego."""
        with self.transacao() as c:
            return (
                c.execute(
                    "UPDATE audios SET estado = 'na_fila', modelo = ?, idioma = ?, progresso = 0, erro = NULL "
                    "WHERE id = ? AND estado IN ('pronto', 'erro')",
                    (modelo, idioma, audio_id),
                ).rowcount
                == 1
            )

    def trechos(self, audio_id: int) -> list[dict[str, Any]]:
        with self.conexao() as c:
            return [
                dict(r)
                for r in c.execute("SELECT id, n, inicio, fim, texto FROM trechos WHERE audio_id = ? ORDER BY n", (audio_id,))
            ]

    def texto_do_audio(self, audio_id: int) -> str:
        return " ".join(t["texto"] for t in self.trechos(audio_id)).strip()

    def trechos_sem_vetor(self, audio_id: int) -> list[dict[str, Any]]:
        with self.conexao() as c:
            return [
                dict(r)
                for r in c.execute("SELECT id, texto FROM trechos WHERE audio_id = ? AND vetor IS NULL ORDER BY n", (audio_id,))
            ]

    def trechos_sem_vetor_de_todos(self, limite: int) -> list[dict[str, Any]]:
        """Os trechos de áudios prontos que ficaram sem vetor (transcritos antes de haver IA, ou com o Ollama fora)."""
        with self.conexao() as c:
            return [
                dict(r)
                for r in c.execute(
                    "SELECT t.id, t.texto FROM trechos t JOIN audios a ON a.id = t.audio_id "
                    "WHERE t.vetor IS NULL AND a.estado = 'pronto' ORDER BY t.id LIMIT ?",
                    (limite,),
                )
            ]

    def gravar_vetores(self, vetores: dict[int, list[float]]) -> None:
        with self.transacao() as c:
            c.executemany(
                "UPDATE trechos SET vetor = ? WHERE id = ?", [(empacotar(v), trecho_id) for trecho_id, v in vetores.items()]
            )

    def buscar_por_palavra(self, busca: str, limite: int = 20, qualquer_palavra: bool = False) -> list[dict[str, Any]]:
        """A busca da lateral exige todas as palavras; a da memória (`qualquer_palavra`) aceita qualquer uma e deixa o
        bm25 ordenar: uma pergunta ("o que foi dito sobre o orçamento?") não tem todas as palavras num trecho só."""
        with self.conexao() as c:
            return [
                dict(r)
                for r in c.execute(
                    "SELECT t.id, t.audio_id, t.inicio, t.fim, t.texto, a.conversa_id, a.nome_original, cv.titulo, "
                    "bm25(trechos_fts) AS nota FROM trechos_fts JOIN trechos t ON t.id = trechos_fts.rowid "
                    "JOIN audios a ON a.id = t.audio_id JOIN conversas cv ON cv.id = a.conversa_id "
                    "WHERE trechos_fts MATCH ? ORDER BY nota LIMIT ?",
                    (consulta_fts(busca, qualquer_palavra), limite),
                )
            ]

    def todos_os_vetores(self) -> list[dict[str, Any]]:
        with self.conexao() as c:
            linhas = c.execute(
                "SELECT t.id, t.audio_id, t.inicio, t.fim, t.texto, t.vetor, a.conversa_id, a.nome_original, cv.titulo "
                "FROM trechos t JOIN audios a ON a.id = t.audio_id JOIN conversas cv ON cv.id = a.conversa_id "
                "WHERE t.vetor IS NOT NULL"
            ).fetchall()
        return [dict(r) | {"vetor": desempacotar(r["vetor"])} for r in linhas]

    # ── ajustes da tela ────────────────────────────────────────────────────────────────────
    def ajustes(self) -> dict[str, str]:
        with self.conexao() as c:
            return {r["chave"]: r["valor"] for r in c.execute("SELECT chave, valor FROM ajustes")}

    def gravar_ajustes(self, novos: dict[str, str]) -> None:
        with self.transacao() as c:
            c.executemany(
                "INSERT INTO ajustes (chave, valor) VALUES (?, ?) ON CONFLICT(chave) DO UPDATE SET valor = excluded.valor",
                list(novos.items()),
            )


def consulta_fts(busca: str, qualquer_palavra: bool = False) -> str:
    """A busca do usuário vira uma consulta FTS5 segura: cada palavra entre aspas, com prefixo; todas obrigatórias, ou
    qualquer uma (OR) na memória."""
    palavras = [p.replace('"', "") for p in busca.split() if p.replace('"', "")]
    return (" OR " if qualquer_palavra else " ").join(f'"{p}"*' for p in palavras) or '""'


def empacotar(vetor: list[float]) -> bytes:
    return struct.pack(f"<{len(vetor)}f", *vetor)


def desempacotar(dados: bytes) -> list[float]:
    return list(struct.unpack(f"<{len(dados) // 4}f", dados))
