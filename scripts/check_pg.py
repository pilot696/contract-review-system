"""PostgreSQL connectivity probe using only the Python standard library.

Tests, in order:
  1. TCP connect to host:port
  2. SSL negotiation (SSLRequest; falls back to plaintext if the server refuses)
  3. startup + authentication (SCRAM-SHA-256, MD5, clear-text, trust)
  4. simple queries: version / current_user / role attributes / database list

Usage:
  python scripts/check_pg.py --databases contract_review
  # 默认连接参数从仓库根目录 .env 的 DATABASE_URL 读取；也可显式覆盖：
  python scripts/check_pg.py --host 127.0.0.1 --port 5432 --user crs --password <password>
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import os
import socket
import ssl
import struct
import sys
from pathlib import Path
from urllib.parse import urlparse
from dataclasses import dataclass, field

PROTOCOL_3_0 = 196608
SSL_REQUEST_CODE = 80877103


class PgError(Exception):
    def __init__(self, fields: dict[str, str]) -> None:
        self.fields = fields
        self.code = fields.get("C", "?")
        self.message = fields.get("M", "unknown error")
        super().__init__(f"[{self.code}] {self.message}")


@dataclass
class Result:
    host: str
    port: int
    tcp_ok: bool = False
    ssl_used: bool = False
    ssl_supported: bool = False
    auth_method: str = ""
    authenticated: bool = False
    server_version: str = ""
    current_user: str = ""
    current_database: str = ""
    role_super: str = ""
    role_createdb: str = ""
    databases: list[str] = field(default_factory=list)
    query_rows: list[list[str | None]] = field(default_factory=list)
    error: str = ""


# --------------------------------------------------------------------------- #
# low-level protocol helpers
# --------------------------------------------------------------------------- #
def recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("connection closed by server")
        buf.extend(chunk)
    return bytes(buf)


def recv_msg(sock: socket.socket) -> tuple[bytes, bytes]:
    header = recv_exact(sock, 5)
    msg_type = header[:1]
    (length,) = struct.unpack("!I", header[1:5])
    payload = recv_exact(sock, length - 4) if length > 4 else b""
    return msg_type, payload


def send_startup(sock: socket.socket, params: dict[str, str]) -> None:
    body = struct.pack("!I", PROTOCOL_3_0)
    for key, value in params.items():
        body += key.encode("utf-8") + b"\x00" + value.encode("utf-8") + b"\x00"
    body += b"\x00"
    sock.sendall(struct.pack("!I", len(body) + 4) + body)


def send_password(sock: socket.socket, payload: bytes) -> None:
    sock.sendall(b"p" + struct.pack("!I", len(payload) + 4) + payload)


def send_query(sock: socket.socket, sql: str) -> None:
    payload = sql.encode("utf-8") + b"\x00"
    sock.sendall(b"Q" + struct.pack("!I", len(payload) + 4) + payload)


def parse_error(payload: bytes) -> dict[str, str]:
    fields: dict[str, str] = {}
    parts = payload.split(b"\x00")
    for part in parts:
        if not part:
            continue
        code = chr(part[0])
        fields[code] = part[1:].decode("utf-8", "replace")
    return fields


def hmac_sha256(key: bytes, msg: bytes) -> bytes:
    return hmac.new(key, msg, hashlib.sha256).digest()


def extract_sasl_mechanisms(payload: bytes) -> list[str]:
    return [m.decode() for m in payload.split(b"\x00") if m]


def scram_client_first() -> tuple[str, str]:
    """Returns (client_first_message_to_send, client_first_bare)."""
    nonce = base64.b64encode(os.urandom(18)).decode("ascii").rstrip("=")
    bare = f"n=,r={nonce}"
    return f"n,,{bare}", bare


def scram_client_final(
    password: str, client_first_bare: str, server_first: str, server_nonce: str
) -> tuple[str, bytes]:
    """Returns (client_final_without_proof, server_signature_expected)."""
    attrs: dict[str, str] = {}
    for item in server_first.split(","):
        if "=" in item:
            k, v = item.split("=", 1)
            attrs[k] = v
    salt = base64.b64decode(attrs["s"])
    iterations = int(attrs["i"])
    salted = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    client_key = hmac_sha256(salted, b"Client Key")
    stored_key = hashlib.sha256(client_key).digest()
    final_without_proof = f"c=biws,r={server_nonce}"
    auth_message = f"{client_first_bare},{server_first},{final_without_proof}"
    client_signature = hmac_sha256(stored_key, auth_message.encode("utf-8"))
    client_proof = bytes(a ^ b for a, b in zip(client_key, client_signature))
    server_key = hmac_sha256(salted, b"Server Key")
    server_signature = hmac_sha256(server_key, auth_message.encode("utf-8"))
    return final_without_proof, server_signature, client_proof


def send_sasl_initial(sock: socket.socket, mechanism: str, initial: bytes) -> None:
    payload = mechanism.encode() + b"\x00" + struct.pack("!I", len(initial)) + initial
    send_password(sock, payload)


def read_until_ready(sock: socket.socket) -> dict[str, str]:
    """Reads messages until ReadyForQuery; returns parameter statuses."""
    params: dict[str, str] = {}
    while True:
        msg_type, payload = recv_msg(sock)
        if msg_type == b"S":  # ParameterStatus
            k, v = payload.split(b"\x00", 1)
            params[k.decode()] = v.rstrip(b"\x00").decode("utf-8", "replace")
        elif msg_type == b"K":  # BackendKeyData
            continue
        elif msg_type == b"N":  # NoticeResponse
            continue
        elif msg_type == b"E":
            raise PgError(parse_error(payload))
        elif msg_type == b"Z":  # ReadyForQuery
            return params
        else:
            continue


def read_query_rows(sock: socket.socket) -> list[list[str | None]]:
    rows: list[list[str | None]] = []
    columns: list[str] = []
    while True:
        msg_type, payload = recv_msg(sock)
        if msg_type == b"T":  # RowDescription
            (count,) = struct.unpack("!H", payload[:2])
            offset = 2
            columns = []
            for _ in range(count):
                end = payload.index(b"\x00", offset)
                columns.append(payload[offset:end].decode("utf-8", "replace"))
                offset = end + 1 + 18  # oid(4)+attnum(2)+type(4)+typlen(2)+typmod(4)+format(2)
        elif msg_type == b"D":  # DataRow
            (count,) = struct.unpack("!H", payload[:2])
            offset = 2
            row: list[str | None] = []
            for _ in range(count):
                (length,) = struct.unpack("!i", payload[offset : offset + 4])
                offset += 4
                if length == -1:
                    row.append(None)
                else:
                    row.append(payload[offset : offset + length].decode("utf-8", "replace"))
                    offset += length
            rows.append(row)
        elif msg_type == b"E":
            raise PgError(parse_error(payload))
        elif msg_type == b"Z":
            return rows
        else:
            continue


# --------------------------------------------------------------------------- #
# connection flow
# --------------------------------------------------------------------------- #
def open_socket(host: str, port: int, timeout: float) -> socket.socket:
    sock = socket.create_connection((host, port), timeout=timeout)
    sock.settimeout(timeout)
    return sock


def negotiate_ssl(sock: socket.socket, host: str, mode: str) -> tuple[socket.socket, bool, bool]:
    """Returns (socket, ssl_used, server_supports_ssl)."""
    sock.sendall(struct.pack("!II", 8, SSL_REQUEST_CODE))
    reply = recv_exact(sock, 1)
    if reply == b"S":
        if mode == "disable":
            raise PgError({"C": "SSL_REQUIRED", "M": "server requires SSL but --sslmode disable was given"})
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        tls = ctx.wrap_socket(sock, server_hostname=host)
        return tls, True, True
    if reply == b"N":
        if mode == "require":
            raise PgError({"C": "SSL_UNSUPPORTED", "M": "server does not support SSL"})
        return sock, False, False
    raise PgError({"C": "SSL_BAD_REPLY", "M": f"unexpected reply to SSLRequest: {reply!r}"})


def authenticate(sock: socket.socket, user: str, password: str) -> tuple[str, dict[str, str]]:
    params: dict[str, str] = {}
    scram_state: dict[str, str] = {}
    auth_method = ""
    while True:
        msg_type, payload = recv_msg(sock)
        if msg_type == b"R":
            (code,) = struct.unpack("!I", payload[:4])
            data = payload[4:]
            if code == 0:  # AuthenticationOk
                continue
            if code == 3:  # cleartext
                auth_method = "cleartext"
                send_password(sock, password.encode() + b"\x00")
                continue
            if code == 5:  # md5
                auth_method = "md5"
                inner = hashlib.md5(password.encode() + user.encode()).hexdigest().encode()
                outer = b"md5" + hashlib.md5(inner + data[:4]).hexdigest().encode()
                send_password(sock, outer + b"\x00")
                continue
            if code == 10:  # SASL
                mechanisms = extract_sasl_mechanisms(data)
                if "SCRAM-SHA-256" not in mechanisms:
                    raise PgError({"C": "SASL_UNSUPPORTED", "M": f"server SASL mechanisms: {mechanisms}"})
                auth_method = "scram-sha-256"
                client_first, bare = scram_client_first()
                scram_state["bare"] = bare
                send_sasl_initial(sock, "SCRAM-SHA-256", client_first.encode())
                continue
            if code == 11:  # SASL continue
                server_first = data.decode()
                attrs = dict(item.split("=", 1) for item in server_first.split(",") if "=" in item)
                final_without_proof, server_signature, client_proof = scram_client_final(
                    password, scram_state["bare"], server_first, attrs["r"]
                )
                scram_state["server_signature"] = base64.b64encode(server_signature).decode()
                send_password(sock, (final_without_proof + ",p=" + base64.b64encode(client_proof).decode()).encode())
                continue
            if code == 12:  # SASL final
                final = data.decode()
                expected = scram_state.get("server_signature", "")
                actual = final[2:] if final.startswith("v=") else ""
                if expected and actual != expected:
                    raise PgError({"C": "SCRAM_BAD_SIGNATURE", "M": "server signature mismatch"})
                continue
            if code == 2:
                raise PgError({"C": "KERBEROS", "M": "Kerberos auth is not supported by this probe"})
            raise PgError({"C": "AUTH_UNSUPPORTED", "M": f"auth request code {code}"})
        if msg_type == b"S":
            k, v = payload.split(b"\x00", 1)
            params[k.decode()] = v.rstrip(b"\x00").decode("utf-8", "replace")
        elif msg_type == b"E":
            raise PgError(parse_error(payload))
        elif msg_type == b"Z":
            return auth_method, params
        elif msg_type == b"K":
            continue
        elif msg_type == b"N":
            continue
        else:
            continue


def probe_database(host: str, port: int, user: str, password: str, database: str,
                   timeout: float, sslmode: str, sql: str | None = None) -> Result:
    result = Result(host=host, port=port)
    sock: socket.socket | None = None
    try:
        sock = open_socket(host, port, timeout)
        result.tcp_ok = True
        sock, ssl_used, ssl_supported = negotiate_ssl(sock, host, sslmode)
        result.ssl_used = ssl_used
        result.ssl_supported = ssl_supported
        send_startup(sock, {
            "user": user,
            "database": database,
            "client_encoding": "UTF8",
            "application_name": "dsh-pg-probe",
        })
        auth_method, params = authenticate(sock, user, password)
        result.auth_method = auth_method
        result.authenticated = True
        result.server_version = params.get("server_version", "")

        if sql:
            send_query(sock, sql)
            result.query_rows = read_query_rows(sock)
            return result

        send_query(sock, "SELECT current_user, current_database();")
        rows = read_query_rows(sock)
        if rows:
            result.current_user = rows[0][0] or ""
            result.current_database = rows[0][1] or ""

        send_query(sock, "SELECT rolsuper, rolcreatedb FROM pg_roles WHERE rolname = current_user;")
        rows = read_query_rows(sock)
        if rows:
            result.role_super = rows[0][0] or ""
            result.role_createdb = rows[0][1] or ""

        send_query(sock, "SELECT datname FROM pg_database WHERE NOT datistemplate ORDER BY datname;")
        rows = read_query_rows(sock)
        result.databases = [r[0] or "" for r in rows]

        send_query(sock, "SELECT 1;")
        read_query_rows(sock)
    except PgError as exc:
        result.error = f"{exc.code}: {exc.message}"
    except (OSError, ConnectionError) as exc:
        result.error = f"{type(exc).__name__}: {exc}"
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
    return result


def _connection_defaults() -> tuple[str, int, str, str]:
    """默认连接参数：环境变量或仓库根目录 .env 中的 DATABASE_URL（保持标准库实现）。"""
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        env_path = Path(__file__).resolve().parents[1] / ".env"
        try:
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("DATABASE_URL="):
                    url = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
        except OSError:
            pass
    if not url:
        return "127.0.0.1", 5432, "crs", ""
    parsed = urlparse(url.replace("+psycopg", ""))
    return (
        parsed.hostname or "127.0.0.1",
        parsed.port or 5432,
        parsed.username or "crs",
        parsed.password or "",
    )


def main() -> int:
    default_host, default_port, default_user, default_password = _connection_defaults()
    parser = argparse.ArgumentParser(description="PostgreSQL connectivity probe (stdlib only)")
    parser.add_argument("--host", default=default_host)
    parser.add_argument("--port", type=int, default=default_port)
    parser.add_argument("--user", default=default_user)
    parser.add_argument("--password", default=default_password)
    parser.add_argument("--databases", default="postgres,contract_review")
    parser.add_argument("--timeout", type=float, default=8.0)
    parser.add_argument("--sslmode", choices=["prefer", "disable", "require"], default="prefer")
    parser.add_argument("--sql", default=None, help="run one SQL query and print its rows")
    args = parser.parse_args()

    overall_ok = True
    for database in [d.strip() for d in args.databases.split(",") if d.strip()]:
        print(f"=== database: {database} ===")
        result = probe_database(args.host, args.port, args.user, args.password, database,
                                args.timeout, args.sslmode, args.sql)
        print(f"tcp_ok={result.tcp_ok} ssl_used={result.ssl_used} ssl_supported={result.ssl_supported}")
        print(f"auth_method={result.auth_method or '-'} authenticated={result.authenticated}")
        if result.authenticated:
            print(f"server_version={result.server_version}")
            if args.sql:
                for row in result.query_rows:
                    print("row=" + " | ".join("NULL" if v is None else v for v in row))
                print("status=OK")
                continue
            print(f"current_user={result.current_user} current_database={result.current_database}")
            print(f"superuser={result.role_super} createdb={result.role_createdb}")
            print(f"databases={','.join(result.databases)}")
            print("status=OK")
        else:
            overall_ok = False
            print(f"status=FAIL error={result.error}")
        print()
    return 0 if overall_ok else 1


if __name__ == "__main__":
    sys.exit(main())
