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


def em_uso():
    """Já existe um Roteiriza rodando? (a porta local está ocupada)"""
    import socket
    with socket.socket() as sk:
        sk.settimeout(0.5)
        return sk.connect_ex(("127.0.0.1", PORTA)) == 0


def pedir_frente():
    """Há outro Roteiriza aberto: pede para ele trazer a janela para a frente."""
    import urllib.request
    try:
        req = urllib.request.Request(URL + "api/trazer", method="POST", headers={"X-Roteiriza": "1"})
        urllib.request.urlopen(req, timeout=2).read()
        return True
    except Exception:
        return False


def porta_arquivo():
    """Arquivo onde a impressora virtual grava o PDF (é o 'porto' da impressora)."""
    return os.path.join(pasta_entrada(), "romaneio.pdf")


class Servidor:
    def __init__(self, ao_chegar=None):
        self.fila = []                # romaneios capturados que a página ainda não buscou
        self.lock = threading.Lock()
        self.ultimo_poll = 0.0        # última vez que a página perguntou pela fila
        self.ao_chegar = ao_chegar    # callback(nome) quando chega um romaneio
        self.abrir_janela = None      # função que abre a janela do programa (definida pelo app.py)
        self.janela = None            # processo da janela (Edge/Chrome em modo aplicativo)
        self.pedido_sair = False      # a página pediu para encerrar o programa
        self.sair_cb = None           # fecha a janela do programa
        self.trazer = None            # traz a janela do programa para a frente
        self.salvar_como = None       # salvar_como(nome, bytes) -> caminho  (janela "Salvar como" do Windows)
        self.imp = {"instalada": None, "ocupado": False, "msg": ""}
        self._visto = None
        self.httpd = None
        self._parar = threading.Event()

    def janela_viva(self):
        return self.janela is not None and self.janela.poll() is None

    # ---------- impressora virtual (comandos vindos da página) ----------
    def atualizar_impressora(self):
        def alvo():
            import impressora_virtual
            ok = impressora_virtual.impressora_instalada()
            with self.lock:
                self.imp["instalada"] = ok
        threading.Thread(target=alvo, daemon=True).start()

    def acao_impressora(self, qual):
        with self.lock:
            if self.imp["ocupado"]:
                return
            self.imp["ocupado"] = True
            self.imp["msg"] = "Aguardando a permissão de administrador..."

        def alvo():
            import impressora_virtual
            try:
                r = impressora_virtual.instalar() if qual == "instalar" else impressora_virtual.remover()
            except Exception as e:                      # noqa: BLE001
                r = f"ERRO: {e}"
            ok = impressora_virtual.impressora_instalada()
            with self.lock:
                self.imp["ocupado"] = False
                self.imp["instalada"] = ok
                if "ERRO" in (r or ""):
                    self.imp["msg"] = "Não deu certo: " + r.split("ERRO:")[-1].strip()
                elif qual == "instalar":
                    self.imp["msg"] = 'Impressora instalada! No sistema, imprima o romaneio na "Romaneio Freedom".'
                else:
                    self.imp["msg"] = "Impressora removida."
        threading.Thread(target=alvo, daemon=True).start()

    @staticmethod
    def _chave(caminho):
        try:
            st = os.stat(caminho)
            return (st.st_mtime_ns, st.st_size) if st.st_size > 0 else None
        except OSError:
            return None

    def ha_novo(self):
        """Tem um PDF na porta da impressora esperando? (usado quando a impressão abre o programa: --romaneio)"""
        return self._chave(porta_arquivo()) is not None

    # ---------- captura do romaneio impresso ----------
    def capturar(self):
        arq = porta_arquivo()
        c = self._chave(arq)
        if c is None:
            self._visto = None
            return
        if self._visto != c:                # espera o arquivo parar de mudar (2 leituras iguais)
            self._visto = c
            return
        nome = time.strftime("romaneio_%Y%m%d_%H%M%S.pdf")
        destino = os.path.join(pasta_romaneios(), nome)
        k = 1
        while os.path.exists(destino):      # dois romaneios no mesmo segundo
            k += 1
            destino = os.path.join(pasta_romaneios(), nome[:-4] + f"_{k}.pdf")
        try:
            # MOVE (não copia): a pasta da impressora precisa ficar livre, senão o Windows não consegue
            # gravar o próximo romaneio e a impressão fica presa em "Spooling"
            shutil.move(arq, destino)
        except OSError:
            return                          # ainda em uso pelo Windows; tenta na próxima
        self._visto = None
        item = {"id": uuid.uuid4().hex[:12], "nome": os.path.basename(destino), "caminho": destino,
                "mtime": int(c[0] / 1e6), "capturado": int(time.time() * 1000)}   # ms: quando o PDF foi gravado / capturado
        with self.lock:
            self.fila.append(item)
        if self.abrir_janela and not self.janela_viva() and time.time() - self.ultimo_poll > 8:
            try:                                    # nenhuma janela aberta: abre o programa
                self.abrir_janela()
            except Exception:
                pass
        if self.ao_chegar:
            try:
                self.ao_chegar(item["nome"])
            except Exception:
                pass

    def _vigia(self):
        while not self._parar.wait(0.5):
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
                if u.path == "/api/impressora":
                    import impressora_virtual
                    with srv.lock:
                        return self._resp(200, {**srv.imp, "exe": impressora_virtual.eh_exe()})
                if u.path == "/api/fila":
                    srv.ultimo_poll = time.time()
                    with srv.lock:
                        return self._resp(200, [{k: i[k] for k in ("id", "nome", "mtime", "capturado")} for i in srv.fila])
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
                if u.path in ("/api/impressora/instalar", "/api/impressora/remover"):
                    srv.acao_impressora(u.path.rsplit("/", 1)[-1])
                    return self._resp(200, {"ok": True})
                if u.path == "/api/abrir-pasta":
                    if hasattr(os, "startfile"):
                        os.startfile(pasta_dados())            # noqa: S606
                    return self._resp(200, {"ok": True})
                if u.path == "/api/log":
                    n = min(int(self.headers.get("Content-Length") or 0), 4000)
                    linha = self.rfile.read(n).decode("utf-8", "replace").replace("\n", " ")
                    arq = os.path.join(pasta_dados(), "leitura.log")
                    try:
                        if os.path.exists(arq) and os.path.getsize(arq) > 500_000:    # não deixa crescer sem fim
                            os.replace(arq, arq + ".antigo")
                        with open(arq, "a", encoding="utf-8") as f:
                            f.write(time.strftime("%Y-%m-%d %H:%M:%S") + " | " + linha + "\n")
                    except OSError:
                        pass
                    return self._resp(200, {"ok": True})
                if u.path == "/api/sair":
                    srv.pedido_sair = True
                    if srv.sair_cb:
                        srv.sair_cb()
                    return self._resp(200, {"ok": True})
                if u.path == "/api/trazer":
                    if srv.trazer:
                        srv.trazer()
                    return self._resp(200, {"ok": True})
                if u.path == "/api/abrir-url":
                    n = min(int(self.headers.get("Content-Length") or 0), 4000)
                    alvo = self.rfile.read(n).decode("utf-8", "replace").strip()
                    if alvo.startswith("https://wa.me/"):        # só o que o app precisa (WhatsApp)
                        import webbrowser
                        webbrowser.open(alvo)
                    return self._resp(200, {"ok": True})
                if u.path == "/api/salvar":
                    nome = os.path.basename((parse_qs(u.query).get("nome") or ["arquivo"])[0]) or "arquivo"
                    n = int(self.headers.get("Content-Length") or 0)
                    dados = self.rfile.read(n)
                    if not srv.salvar_como:
                        return self._resp(200, {"ok": False})
                    caminho = srv.salvar_como(nome, dados)
                    return self._resp(200, {"ok": bool(caminho), "caminho": caminho or ""})
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
