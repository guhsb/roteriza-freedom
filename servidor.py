"""Servidor local do Roteiriza Freedom.

- Serve a página (web/index.html) em http://127.0.0.1:47654/
- Guarda os dados do app na pasta  dados\\storage  (ao lado do .exe), não no navegador
- Vigia o arquivo da impressora virtual (dados\\entrada\\romaneio.pdf): quando um romaneio
  é impresso, guarda uma cópia em dados\\romaneios e avisa a página, que lê o PDF e abre a revisão.
"""
import functools
import json
import os
import shutil
import sys
import threading
import time
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, unquote, urlparse

PORTA = 47654
URL = f"http://127.0.0.1:{PORTA}/"
VERSAO = "1.0"


def pasta_exe():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def recurso(*partes):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, *partes)


@functools.lru_cache(maxsize=1)
def pasta_dados():
    """Pasta 'dados' ao lado do .exe; se não der para gravar lá, usa %APPDATA%."""
    p = os.path.join(pasta_exe(), "dados")
    try:
        os.makedirs(p, exist_ok=True)
        t = os.path.join(p, ".teste")
        open(t, "w").close()
        os.remove(t)
        return p
    except OSError:
        p = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "RoteirizaFreedom", "dados")
        os.makedirs(p, exist_ok=True)
        return p


def _sub(nome):
    p = os.path.join(pasta_dados(), nome)
    os.makedirs(p, exist_ok=True)
    return p


def pasta_entrada():
    return _sub("entrada")


def pasta_romaneios():
    return _sub("romaneios")


def pasta_storage():
    return _sub("storage")


def porta_arquivo():
    """Arquivo onde a impressora virtual grava o PDF (é o 'porto' da impressora)."""
    return os.path.join(pasta_entrada(), "romaneio.pdf")


class Servidor:
    def __init__(self, ao_chegar=None):
        self.fila = []                # romaneios capturados que a página ainda não buscou
        self.lock = threading.Lock()
        self.ultimo_poll = 0.0        # última vez que a página perguntou pela fila
        self.ao_chegar = ao_chegar    # callback(nome) para a janela do programa
        self.ultimo = None            # (mtime_ns, tamanho) do último PDF já capturado
        self._visto = None
        self.httpd = None
        self._parar = threading.Event()
        self._estado_arq = os.path.join(pasta_dados(), "estado.json")
        self._carregar_estado()

    # ---------- estado ----------
    def _carregar_estado(self):
        try:
            with open(self._estado_arq, encoding="utf-8") as f:
                u = json.load(f).get("ultimo")
            self.ultimo = tuple(u) if u else None
        except Exception:
            self.ultimo = None

    def _salvar_estado(self):
        try:
            with open(self._estado_arq, "w", encoding="utf-8") as f:
                json.dump({"ultimo": list(self.ultimo) if self.ultimo else None}, f)
        except OSError:
            pass

    @staticmethod
    def _chave(caminho):
        try:
            st = os.stat(caminho)
            return (st.st_mtime_ns, st.st_size) if st.st_size > 0 else None
        except OSError:
            return None

    def ha_novo(self):
        """Tem um PDF na porta da impressora que ainda não foi capturado? (usado por --romaneio)"""
        c = self._chave(porta_arquivo())
        if c is None or c == self.ultimo:
            return False
        if self.ultimo is None:      # nunca capturou nada: só vale se for recente
            return time.time() - c[0] / 1e9 < 600
        return True

    # ---------- captura do romaneio impresso ----------
    def capturar(self):
        arq = porta_arquivo()
        c = self._chave(arq)
        if c is None or c == self.ultimo:
            return
        if self.ultimo is None and time.time() - c[0] / 1e9 > 600:
            self.ultimo = c                 # arquivo antigo de antes da instalação: ignora
            self._salvar_estado()
            return
        if self._visto != c:                # espera o arquivo parar de mudar (2 leituras iguais)
            self._visto = c
            return
        nome = time.strftime("romaneio_%Y%m%d_%H%M%S.pdf")
        destino = os.path.join(pasta_romaneios(), nome)
        try:
            shutil.copy2(arq, destino)
        except OSError:
            return                          # ainda em uso pelo Windows; tenta na próxima
        self.ultimo = c
        self._salvar_estado()
        item = {"id": uuid.uuid4().hex[:12], "nome": nome, "caminho": destino}
        with self.lock:
            self.fila.append(item)
        if time.time() - self.ultimo_poll > 8:   # nenhuma página aberta: abre o navegador
            try:
                webbrowser.open(URL)
            except Exception:
                pass
        if self.ao_chegar:
            try:
                self.ao_chegar(nome)
            except Exception:
                pass

    def _vigia(self):
        while not self._parar.wait(1.5):
            try:
                self.capturar()
            except Exception:
                pass

    # ---------- ciclo de vida ----------
    def iniciar(self):
        """Sobe o servidor. Levanta OSError se a porta já está em uso (programa já aberto)."""
        srv = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            # --- utilidades ---
            def _host_ok(self):
                return (self.headers.get("Host") or "") in (f"127.0.0.1:{PORTA}", f"localhost:{PORTA}")

            def _escrita_ok(self):
                # cabeçalho próprio: outros sites não conseguem mandá-lo sem permissão (CORS)
                return self._host_ok() and self.headers.get("X-Roteiriza") == "1"

            def _resp(self, codigo, corpo=b"", tipo="application/json; charset=utf-8"):
                if isinstance(corpo, (dict, list)):
                    corpo = json.dumps(corpo).encode("utf-8")
                elif isinstance(corpo, str):
                    corpo = corpo.encode("utf-8")
                self.send_response(codigo)
                self.send_header("Content-Type", tipo)
                self.send_header("Content-Length", str(len(corpo)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(corpo)

            def _chave_storage(self):
                q = parse_qs(urlparse(self.path).query)
                k = (q.get("key") or [""])[0]
                return k if k and len(k) < 200 else None

            def _arq_storage(self, k):
                return os.path.join(pasta_storage(), quote(k, safe="") + ".json")

            # --- rotas ---
            def do_GET(self):
                if not self._host_ok():
                    return self._resp(403, {"erro": "host"})
                u = urlparse(self.path)
                if u.path in ("/", "/index.html"):
                    with open(recurso("web", "index.html"), "rb") as f:
                        return self._resp(200, f.read(), "text/html; charset=utf-8")
                if u.path == "/api/ping":
                    return self._resp(200, {"ok": True, "app": "roteiriza", "versao": VERSAO})
                if u.path == "/api/storage":
                    k = self._chave_storage()
                    if not k:
                        return self._resp(400, {"erro": "key"})
                    try:
                        with open(self._arq_storage(k), encoding="utf-8") as f:
                            return self._resp(200, {"value": f.read()})
                    except OSError:
                        return self._resp(404, {"erro": "nao existe"})
                if u.path == "/api/storage-list":
                    pref = (parse_qs(u.query).get("prefix") or [""])[0]
                    chaves = [unquote(n[:-5]) for n in os.listdir(pasta_storage()) if n.endswith(".json")]
                    return self._resp(200, {"keys": [k for k in chaves if k.startswith(pref)]})
                if u.path == "/api/fila":
                    srv.ultimo_poll = time.time()
                    with srv.lock:
                        return self._resp(200, [{"id": i["id"], "nome": i["nome"]} for i in srv.fila])
                if u.path.startswith("/api/pdf/"):
                    id_ = u.path.rsplit("/", 1)[-1]
                    with srv.lock:
                        it = next((i for i in srv.fila if i["id"] == id_), None)
                    if not it:
                        return self._resp(404, {"erro": "nao existe"})
                    with open(it["caminho"], "rb") as f:
                        return self._resp(200, f.read(), "application/pdf")
                return self._resp(404, {"erro": "rota"})

            def do_PUT(self):
                if not self._escrita_ok():
                    return self._resp(403, {"erro": "negado"})
                if urlparse(self.path).path != "/api/storage":
                    return self._resp(404, {"erro": "rota"})
                k = self._chave_storage()
                if not k:
                    return self._resp(400, {"erro": "key"})
                n = int(self.headers.get("Content-Length") or 0)
                dados = self.rfile.read(n)
                arq = self._arq_storage(k)
                tmp = arq + ".tmp"
                with open(tmp, "wb") as f:
                    f.write(dados)
                os.replace(tmp, arq)           # gravação atômica: não corrompe se fechar no meio
                return self._resp(200, {"ok": True})

            def do_DELETE(self):
                if not self._escrita_ok():
                    return self._resp(403, {"erro": "negado"})
                k = self._chave_storage()
                if not k:
                    return self._resp(400, {"erro": "key"})
                try:
                    os.remove(self._arq_storage(k))
                except OSError:
                    pass
                return self._resp(200, {"ok": True})

            def do_POST(self):
                if not self._escrita_ok():
                    return self._resp(403, {"erro": "negado"})
                u = urlparse(self.path)
                if u.path.startswith("/api/ack/"):
                    id_ = u.path.rsplit("/", 1)[-1]
                    with srv.lock:
                        srv.fila = [i for i in srv.fila if i["id"] != id_]
                    return self._resp(200, {"ok": True})
                return self._resp(404, {"erro": "rota"})

        self.httpd = ThreadingHTTPServer(("127.0.0.1", PORTA), H)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        threading.Thread(target=self._vigia, daemon=True).start()

    def parar(self):
        self._parar.set()
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()        # libera a porta
