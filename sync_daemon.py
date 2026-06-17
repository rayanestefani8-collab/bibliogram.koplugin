#!/usr/bin/env python3
"""
Serviço de sincronização automática.
Executa o coletor em loop a cada N minutos e mantém o servidor OPDS rodando.

Uso:
  python sync_daemon.py --channel @meucanal --interval 60
"""

import subprocess, time, logging, argparse, os, sys, signal, atexit
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PID_FILE = os.path.join(BASE_DIR, "sync_daemon.pid")

# Grava PID para comunicação com webapp
with open(PID_FILE, "w") as _f:
    _f.write(str(os.getpid()))
atexit.register(lambda: os.path.exists(PID_FILE) and os.remove(PID_FILE))

# Flag para sincronização imediata via sinal
_sync_requested = False

def _handle_sigusr1(signum, frame):
    global _sync_requested
    log.info("Sinal SIGUSR1 recebido — sincronização manual solicitada.")
    _sync_requested = True

signal.signal(signal.SIGUSR1, _handle_sigusr1)


def run_collector(channel: str):
    log.info("Iniciando sincronização incremental do canal %s...", channel)
    result = subprocess.run(
        [sys.executable, "collector.py", "--channel", channel, "--sync"],
        capture_output=True, text=True, cwd=BASE_DIR
    )
    if result.stdout:
        for line in result.stdout.strip().splitlines():
            log.info("collector: %s", line)
    if result.returncode != 0:
        log.error("Coletor falhou (código %d): %s", result.returncode, result.stderr[:300])


def start_server():
    log.info("Iniciando servidor OPDS...")
    return subprocess.Popen(
        [sys.executable, "server.py"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT
    )


def main():
    parser = argparse.ArgumentParser(description="Sincronização automática Telegram → OPDS")
    parser.add_argument("--channel",  "-c", required=True, help="Canal do Telegram")
    parser.add_argument("--interval", "-i", type=int, default=60,
                        help="Intervalo entre sincronizações em minutos (padrão: 60)")
    parser.add_argument("--no-server", action="store_true",
                        help="Não inicia o servidor OPDS (já está rodando)")
    args = parser.parse_args()

    server_proc = None
    if not args.no_server:
        server_proc = start_server()
        time.sleep(2)  # aguarda servidor iniciar

    # Primeira coleta completa inicial
    log.info("Coleta inicial — isso pode demorar para canais grandes...")
    run_collector(args.channel)

    log.info("Sincronização automática a cada %d minutos. Ctrl+C para parar.", args.interval)
    try:
        global _sync_requested
        elapsed = 0
        while True:
            time.sleep(10)
            elapsed += 10
            if _sync_requested:
                _sync_requested = False
                elapsed = 0
                run_collector(args.channel)
            elif elapsed >= args.interval * 60:
                elapsed = 0
                run_collector(args.channel)
    except KeyboardInterrupt:
        log.info("Interrompido pelo usuário.")
    finally:
        if server_proc:
            server_proc.terminate()


if __name__ == "__main__":
    main()
