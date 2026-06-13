from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path


def atomic_write_text(path: Path | str, text: str, encoding: str = "utf-8") -> None:
    """Grava ``text`` em ``path`` de forma atômica (all-or-nothing).

    Escreve primeiro num tempfile criado NO MESMO diretório do alvo, faz
    flush + os.fsync para garantir que os bytes chegaram ao disco, e só então
    os.replace(tmp, path) — que é atômico no mesmo filesystem. Em qualquer erro
    o tempfile é removido para não deixar órfãos.

    O tempfile precisa ficar no mesmo diretório porque os.replace cross-device
    (tmp em /tmp, alvo em outro mount) falha com OSError. Isso elimina o torn
    write que corrompe run_state.json / calibration_predictions.json quando o
    processo morre no meio de um write_text comum.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding=encoding) as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise


def quarantine_corrupt(path: Path | str) -> None:
    """Isola um arquivo corrompido sem NUNCA propagar exceção.

    Sufixo único (timestamp + contador) para não clobbar a forense de um
    incidente anterior, e rename best-effort dentro de try/except OSError: se o
    rename falhar (dir read-only, race entre processos), o caminho de leitura
    ainda segue com estado vazio em vez de reintroduzir o crash auto-perpetuante
    que esta quarentena existe para prevenir.
    """
    path = Path(path)
    try:
        target = path.with_name(f"{path.name}.corrupt.{int(time.time())}")
        counter = 1
        while target.exists():
            target = path.with_name(f"{path.name}.corrupt.{int(time.time())}.{counter}")
            counter += 1
        path.replace(target)
    except OSError:
        pass
