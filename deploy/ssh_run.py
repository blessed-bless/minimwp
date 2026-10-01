"""SSH-раннер на paramiko: штатный ssh.exe на этой машине молчит и ничего не пишет.

  python _ssh.py run "команда"        — выполнить команду на сервере
  python _ssh.py script путь.sh       — залить файл и выполнить через bash
  python _ssh.py put локальный удалённый
"""
import os
import pathlib
import sys

import paramiko

# Консоль Windows живёт в cp1251 и падает на любом символе вне неё.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HOST = "109.71.240.211"
USER = "root"
KEY = pathlib.Path(os.environ["USERPROFILE"]) / ".ssh" / "id_ed25519"


def connect() -> paramiko.SSHClient:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        HOST,
        username=USER,
        pkey=paramiko.Ed25519Key.from_private_key_file(str(KEY)),
        timeout=20,
        banner_timeout=30,
        auth_timeout=30,
    )
    return client


def run(client: paramiko.SSHClient, command: str, timeout: int = 900) -> int:
    stdin, stdout, stderr = client.exec_command(command, timeout=timeout, get_pty=False)
    stdin.close()
    out = stdout.read().decode("utf-8", "replace")
    err = stderr.read().decode("utf-8", "replace")
    code = stdout.channel.recv_exit_status()
    if out:
        sys.stdout.write(out)
    if err:
        sys.stdout.write("--- stderr ---\n" + err)
    sys.stdout.write(f"--- exit {code} ---\n")
    return code


def main() -> int:
    mode = sys.argv[1]
    client = connect()
    try:
        if mode == "run":
            return run(client, sys.argv[2])

        if mode == "put":
            sftp = client.open_sftp()
            sftp.put(sys.argv[2], sys.argv[3])
            sftp.close()
            print(f"uploaded -> {sys.argv[3]}")
            return 0

        if mode == "script":
            local = pathlib.Path(sys.argv[2])
            remote = "/tmp/" + local.name
            sftp = client.open_sftp()
            sftp.putfo(
                __import__("io").BytesIO(
                    local.read_text(encoding="utf-8").replace("\r\n", "\n").encode()
                ),
                remote,
            )
            sftp.close()
            return run(client, f"bash {remote}")

        print("unknown mode")
        return 2
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
