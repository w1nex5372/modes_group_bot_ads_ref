import os
import signal
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable


def start(name, script):
    print(f"Paleidžiama: {name}")
    return subprocess.Popen([PY, os.path.join(ROOT, script)], cwd=ROOT)


def main():
    procs = [
        ("Referral botas", "referral_bot.py"),
        ("Auto forwarderis", "forwarder.py"),
    ]

    running = [start(name, script) for name, script in procs]

    try:
        while True:
            for proc, (name, script) in zip(running, procs):
                code = proc.poll()
                if code is not None:
                    print(f"{name} sustojo su kodu {code}. Paleidžiu iš naujo po 5 s...")
                    time.sleep(5)
                    idx = running.index(proc)
                    running[idx] = start(name, script)
            time.sleep(2)
    except KeyboardInterrupt:
        print("\nStabdoma...")
        for proc in running:
            if proc.poll() is None:
                proc.terminate()
        for proc in running:
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()


if __name__ == "__main__":
    main()
