#!/usr/bin/env python3
"""Tiny static + WebSocket relay for HYS Very Cool Racing Game."""
import argparse
import hashlib
import json
import os
import re
import socket
import ssl
import struct
import subprocess
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
ROOM_RE = re.compile(r"^[A-Z0-9]{4}$")
rooms = {}
rooms_lock = threading.Lock()


def lan_ip():
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("8.8.8.8", 80))
        return probe.getsockname()[0]
    except OSError:
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return "127.0.0.1"
    finally:
        probe.close()


def recv_exact(sock, count):
    data = b""
    while len(data) < count:
        chunk = sock.recv(count - len(data))
        if not chunk:
            raise ConnectionError("socket closed")
        data += chunk
    return data


def recv_frame(sock):
    first, second = recv_exact(sock, 2)
    opcode, masked, length = first & 0x0F, second & 0x80, second & 0x7F
    if length == 126:
        length = struct.unpack("!H", recv_exact(sock, 2))[0]
    elif length == 127:
        length = struct.unpack("!Q", recv_exact(sock, 8))[0]
    if length > 65536:
        raise ConnectionError("frame too large")
    mask = recv_exact(sock, 4) if masked else b""
    payload = recv_exact(sock, length)
    if masked:
        payload = bytes(value ^ mask[i % 4] for i, value in enumerate(payload))
    return opcode, payload


def frame(payload, opcode=1):
    payload = payload if isinstance(payload, bytes) else payload.encode("utf-8")
    length = len(payload)
    header = bytes((0x80 | opcode, length)) if length < 126 else bytes((0x80 | opcode, 126)) + struct.pack("!H", length)
    return header + payload


class Peer:
    def __init__(self, sock):
        self.sock = sock
        self.lock = threading.Lock()
        self.room = None
        self.role = None
        self.alive = True

    def send(self, message):
        if not self.alive:
            return
        raw = message if isinstance(message, str) else json.dumps(message, separators=(",", ":"))
        try:
            with self.lock:
                self.sock.sendall(frame(raw))
        except OSError:
            self.alive = False

    def close(self):
        self.alive = False
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


def attach(peer, role, room):
    room = str(room or "").upper()
    if not ROOM_RE.match(room):
        peer.send({"type": "error", "message": "Invalid room code"})
        return
    other = None
    with rooms_lock:
        record = rooms.setdefault(room, {"host": None, "controller": None})
        previous = record[role]
        if previous and previous is not peer:
            previous.close()
        record[role] = peer
        peer.room, peer.role = room, role
        other = record["controller" if role == "host" else "host"]
    if role == "host":
        peer.send({"type": "controller", "connected": bool(other and other.alive)})
        if other and other.alive:
            other.send({"type": "host", "connected": True})
    else:
        peer.send({"type": "joined", "room": room, "hostConnected": bool(other and other.alive)})
        if other and other.alive:
            other.send({"type": "controller", "connected": True})


def detach(peer):
    other = None
    with rooms_lock:
        if peer.room:
            record = rooms.get(peer.room)
            if record and record.get(peer.role) is peer:
                record[peer.role] = None
                other = record["controller" if peer.role == "host" else "host"]
                if not record["host"] and not record["controller"]:
                    rooms.pop(peer.room, None)
    if other and other.alive:
        if peer.role == "controller":
            other.send({"type": "controller", "connected": False})
        else:
            other.send({"type": "host", "connected": False})


def handle_message(peer, message):
    try:
        data = json.loads(message)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return
    kind = data.get("type")
    if kind == "host":
        attach(peer, "host", data.get("room"))
    elif kind == "join":
        attach(peer, "controller", data.get("room"))
    elif kind == "state" and peer.role == "controller" and peer.room:
        with rooms_lock:
            record = rooms.get(peer.room)
            host = record and record.get("host")
        if host and host.alive:
            host.send({
                "type": "state",
                "steer": max(-1.0, min(1.0, float(data.get("steer", 0) or 0))),
                "throttle": bool(data.get("throttle")),
                "brake": bool(data.get("brake")),
            })
    elif kind == "reset" and peer.role == "controller" and peer.room:
        with rooms_lock:
            record = rooms.get(peer.room)
            host = record and record.get("host")
        if host and host.alive:
            host.send({"type": "reset"})


class Handler(SimpleHTTPRequestHandler):
    server_version = "HYSRacing/1.0"

    def __init__(self, *args, directory=None, **kwargs):
        super().__init__(*args, directory=directory, **kwargs)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/info":
            # A trusted HTTPS quick-tunnel is friendlier to mobile Safari than the
            # local self-signed certificate.  The tunnel process writes its current
            # public URL here; retain the LAN HTTPS endpoint as an offline fallback.
            controller_origin = self.server.controller_origin
            try:
                with open(self.server.tunnel_file, encoding="utf-8") as source:
                    tunneled_origin = source.read().strip().rstrip("/")
                if tunneled_origin.startswith("https://"):
                    controller_origin = tunneled_origin
            except OSError:
                pass
            body = json.dumps({"controllerOrigin": controller_origin}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/ws" and self.headers.get("Upgrade", "").lower() == "websocket":
            self.handle_websocket()
            return
        super().do_GET()

    def handle_websocket(self):
        key = self.headers.get("Sec-WebSocket-Key")
        if not key:
            self.send_error(400, "Missing WebSocket key")
            return
        accept = hashlib.sha1((key + GUID).encode()).digest()
        import base64
        self.send_response(101, "Switching Protocols")
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", base64.b64encode(accept).decode())
        self.end_headers()
        peer = Peer(self.connection)
        try:
            while peer.alive:
                opcode, payload = recv_frame(self.connection)
                if opcode == 8:
                    break
                if opcode == 9:
                    with peer.lock:
                        self.connection.sendall(frame(payload, 10))
                elif opcode == 1:
                    handle_message(peer, payload.decode("utf-8"))
        except (ConnectionError, OSError, UnicodeDecodeError):
            pass
        finally:
            detach(peer)
            peer.close()

    def log_message(self, fmt, *args):
        if "/ws" not in str(args):
            super().log_message(fmt, *args)


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def start_quick_tunnel(root, tunnel_file, port):
    """Start the bundled Cloudflare helper and publish its trusted HTTPS URL."""
    binary = os.path.join(root, ".tools", "cloudflared")
    if not os.path.isfile(binary):
        return None
    try:
        os.remove(tunnel_file)
    except OSError:
        pass
    process = subprocess.Popen(
        [binary, "tunnel", "--url", f"http://127.0.0.1:{port}", "--no-autoupdate"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
    )

    def watch_output():
        for line in process.stdout:
            match = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line)
            if match:
                origin = match.group(0)
                temporary = tunnel_file + ".tmp"
                with open(temporary, "w", encoding="utf-8") as output:
                    output.write(origin + "\n")
                os.replace(temporary, tunnel_file)
                print(f"Safari phone controller: {origin}/controller.html")

    threading.Thread(target=watch_output, daemon=True).start()
    return process


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "4173")))
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--https-port", type=int, default=4174)
    args = parser.parse_args()
    root = os.path.dirname(os.path.abspath(__file__))
    static_dir = os.path.join(root, "dist")
    address = lan_ip()
    handler = lambda *a, **kw: Handler(*a, directory=static_dir, **kw)
    server = Server((args.host, args.port), handler)
    server.lan_address = address
    tunnel_file = os.path.join(root, ".tunnel-url")
    server.tunnel_file = tunnel_file
    # Hosts such as Render terminate trusted HTTPS before forwarding traffic to
    # this process. In that environment both pages and WebSockets use the same
    # public origin, so neither the local certificate nor a quick tunnel is needed.
    production = "PORT" in os.environ
    if production:
        server.controller_origin = os.environ.get("PUBLIC_ORIGIN", "").rstrip("/")
        server.tunnel_file = ""
        print(f"HYS Very Cool Racing Game production server on 0.0.0.0:{args.port}")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
        return

    cert_dir = os.path.join(root, ".cert")
    os.makedirs(cert_dir, exist_ok=True)
    cert, key, config, address_file = (os.path.join(cert_dir, name) for name in ("controller.crt", "controller.key", "openssl.cnf", "address"))
    previous_address = open(address_file).read().strip() if os.path.exists(address_file) else ""
    if previous_address != address or not os.path.exists(cert) or not os.path.exists(key):
        with open(config, "w") as out:
            out.write(f"[req]\nprompt=no\ndistinguished_name=dn\nx509_extensions=v3\n[dn]\nCN={address}\n[v3]\nsubjectAltName=IP:{address}\nkeyUsage=digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\n")
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "30", "-keyout", key, "-out", cert, "-config", config], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        with open(address_file, "w") as out:
            out.write(address)
    secure = Server((args.host, args.https_port), handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    secure.socket = context.wrap_socket(secure.socket, server_side=True)
    controller_origin = f"https://{address}:{args.https_port}"
    server.controller_origin = secure.controller_origin = controller_origin
    server.tunnel_file = secure.tunnel_file = tunnel_file
    server.lan_address = secure.lan_address = address
    secure_thread = threading.Thread(target=secure.serve_forever, daemon=True)
    secure_thread.start()
    tunnel = start_quick_tunnel(root, tunnel_file, args.port)
    print(f"HYS Very Cool Racing Game: http://127.0.0.1:{args.port}")
    print(f"Phone controller: {controller_origin}/controller.html")
    print("On first use, accept the local certificate warning so motion sensors are available.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if tunnel:
            tunnel.terminate()
            try:
                tunnel.wait(timeout=3)
            except subprocess.TimeoutExpired:
                tunnel.kill()
        secure.shutdown()
        secure.server_close()
        server.server_close()


if __name__ == "__main__":
    main()
