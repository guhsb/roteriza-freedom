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
import socket
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
    """Pasta onde a impressora virtual grava o PDF. No Windows fica em C:\\ProgramData\\RoteirizaFreedom\\entrada:
    caminho curto, sem espaços/parênteses, fora de Downloads/OneDrive e que não muda se a pasta do programa mudar."""
    if sys.platform == "win32":
        p = os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"), "RoteirizaFreedom", "entrada")
        try:
            os.makedirs(p, exist_ok=True)
            return p
        except OSError:
            pass                                    # sem permissão: usa a pasta do programa
    return _sub("entrada")


def pasta_romaneios():
    """Onde ficam guardados os PDFs já lidos (dados\\lidos)."""
    return _sub("lidos")


@functools.lru_cache(maxsize=1)
def pasta_visivel():
    """Pasta "Romaneios" AO LADO do programa (como a pasta DANFE do programa de etiquetas): qualquer PDF salvo ali
    é lido na hora e some da pasta. É o jeito mais simples: não depende de impressora."""
    p = os.path.join(pasta_exe(), "Romaneios")
    try:
        os.makedirs(p, exist_ok=True)
        t = os.path.join(p, ".teste")
        open(t, "w").close()
        os.remove(t)
        return p
    except OSError:
        return _sub("Romaneios")


def _arq_config():
    return os.path.join(pasta_dados(), "config.json")


def ler_config():
    try:
        with open(_arq_config(), encoding="utf-8") as f:
            return json.load(f)
    except Exception:                                # noqa: BLE001
        return {}


def gravar_config(cfg):
    try:
        with open(_arq_config(), "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=1)
    except OSError:
        pass


def pasta_storage():
    return _sub("storage")


def em_uso():
    """Já existe um Roteiriza rodando? (a porta local está ocupada)"""
    import socket
    with socket.socket() as sk:
        sk.settimeout(0.5)
        return sk.connect_ex(("127.0.0.1", PORTA)) == 0


PORTA_REDE = 9100


def ips_locais():
    """Endereços IP deste computador na rede (para informar ao servidor onde imprimir)."""
    achados = []
    try:                                              # o endereço que o Windows usaria para sair (não envia nada)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sk:
            sk.connect(("10.255.255.255", 1))
            achados.append(sk.getsockname()[0])
    except OSError:
        pass
    try:
        achados += socket.gethostbyname_ex(socket.gethostname())[2]
    except OSError:
        pass
    vistos = []
    for ip in achados:
        if ip and not ip.startswith(("127.", "169.254.")) and ip not in vistos:
            vistos.append(ip)
    return vistos


def extrai_pdf(dados):
    """Procura o PDF no que a impressora mandou (às vezes vem embrulhado em comandos PJL). Devolve (tipo, bytes|None)."""
    i = dados.find(b"%PDF-")
    if i >= 0:
        fim = dados.rfind(b"%%EOF")
        fim = fim + 5 if fim > i else len(dados)
        return "pdf", dados[i:fim] + b"\n"
    if dados[:4] == b"PK\x03\x04":
        return "XPS (o driver mandou XPS, não PDF)", None
    if dados.lstrip(b"\x1b%-12345X@PJL \r\n")[:3] == b"%!P" or b"%!PS" in dados[:4096]:
        return "PostScript (o driver mandou PostScript, não PDF)", None
    if dados[:2] == b"\x1bE" or b"\x1b&l" in dados[:200]:
        return "PCL (o driver mandou PCL, não PDF)", None
    return "formato desconhecido", None


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
        self.total_capturados = 0     # quantos PDFs já chegaram da impressora (usado no teste)
        self.rede = {"sock": None, "erro": "", "ultimo": ""}   # receptor de impressão por IP (porta 9100)
        self.pedido_sair = False      # a página pediu para encerrar o programa
        self.sair_cb = None           # fecha a janela do programa
        self.trazer = None            # traz a janela do programa para a frente
        self.salvar_como = None       # salvar_como(nome, bytes) -> caminho  (janela "Salvar como" do Windows)
        self.imp = {"instalada": None, "ocupado": False, "msg": ""}
        self._visto = {}               # arquivo -> (mtime, tamanho) da última leitura (espera parar de mudar)
        self.pastas_erp = [p for p in ler_config().get("pastas_erp", []) if isinstance(p, dict) and p.get("caminho")]
        try:
            with open(os.path.join(pasta_dados(), "vistos_erp.json"), encoding="utf-8") as f:
                self._vistos_erp = set(json.load(f))      # PDFs da pasta do ERP que já foram lidos
        except Exception:                                  # noqa: BLE001
            self._vistos_erp = set()
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
                porta = self._cfg_rede()["porta"]
                r = {"instalar": impressora_virtual.instalar, "remover": impressora_virtual.remover,
                     "reparar": impressora_virtual.reparar,
                     "instalar-ip": lambda: impressora_virtual.instalar_ip(porta),
                     "firewall": lambda: impressora_virtual.liberar_firewall(porta)}[qual]()
            except Exception as e:                      # noqa: BLE001
                r = f"ERRO: {e}"
            ok = impressora_virtual.impressora_instalada()
            with self.lock:
                self.imp["ocupado"] = False
                self.imp["instalada"] = ok
                if "ERRO" in (r or ""):
                    self.imp["msg"] = "Não deu certo: " + r.split("ERRO:")[-1].strip()
                elif qual == "instalar":
                    self.imp["msg"] = 'Impressora instalada! Clique em "Testar impressora" e depois imprima o romaneio na "Romaneio Freedom".'
                elif qual == "instalar-ip":
                    self.imp["msg"] = 'Impressora "Romaneio Freedom (IP)" criada neste PC. Ligue "Receber impressão por IP" e imprima nela.'
                elif qual == "firewall":
                    self.imp["msg"] = "Porta liberada no firewall (redes de domínio e privadas)."
                elif qual == "reparar":
                    self.imp["msg"] = 'Fila de impressão limpa. Clique em "Testar impressora" para conferir.'
                else:
                    self.imp["msg"] = "Impressora removida."
        threading.Thread(target=alvo, daemon=True).start()

    def comparar_impressoras(self):
        """Compara a Romaneio Freedom com a Etiqueta Freedom (que funciona) e mostra o que é diferente."""
        with self.lock:
            if self.imp["ocupado"]:
                return
            self.imp["ocupado"] = True
            self.imp["msg"] = "Comparando com a Etiqueta Freedom..."

        def alvo():
            import impressora_virtual
            texto = impressora_virtual.comparar_com_etiqueta()
            msg = impressora_virtual.resumo_comparacao(texto) + " (detalhes em dados\\comparacao_impressoras.txt)"
            with self.lock:
                self.imp["ocupado"] = False
                self.imp["msg"] = msg
        threading.Thread(target=alvo, daemon=True).start()

    def testar_impressora(self):
        """Imprime uma página de teste e espera o PDF chegar; se não chegar, diz o motivo provável."""
        with self.lock:
            if self.imp["ocupado"]:
                return
            self.imp["ocupado"] = True
            self.imp["msg"] = "Enviando uma página de teste para a impressora..."

        def alvo():
            import impressora_virtual
            antes = self.total_capturados
            t0 = time.time()
            erro = impressora_virtual.enviar_teste()
            chegou = False
            while time.time() - t0 < 25:
                if self.total_capturados > antes:
                    chegou = True
                    break
                time.sleep(0.5)
            if chegou:
                msg = (f"✅ A impressora funciona: o PDF de teste chegou em {time.time() - t0:.0f} s. "
                       "Se o romaneio do sistema ainda travar, o problema está na impressão do sistema, não na impressora.")
            else:
                diag = impressora_virtual.diagnostico()
                try:
                    with open(os.path.join(pasta_dados(), "diagnostico_impressora.log"), "w", encoding="utf-8") as f:
                        f.write(time.strftime("%Y-%m-%d %H:%M:%S") + "\n" + (erro or "") + "\n" + diag + "\n")
                except OSError:
                    pass
                msg = ("❌ O PDF de teste não chegou em 25 s. " + impressora_virtual.resumo_diagnostico(diag) +
                       ' Clique em "Reparar impressão". Se continuar assim, me mande o arquivo dados\\diagnostico_impressora.log.')
            with self.lock:
                self.imp["ocupado"] = False
                self.imp["msg"] = msg
        threading.Thread(target=alvo, daemon=True).start()

    @staticmethod
    def _chave(caminho):
        try:
            st = os.stat(caminho)
            return (st.st_mtime_ns, st.st_size) if st.st_size > 0 else None
        except OSError:
            return None

    @staticmethod
    def _pdfs_na_entrada():
        """PDFs esperando nas duas "caixas de entrada": a da impressora (romaneio.pdf, em ProgramData) e a pasta
        "Romaneios" ao lado do programa (PDF salvo à mão, sem impressora)."""
        achados = []
        for pasta in (pasta_entrada(), pasta_visivel()):
            try:
                achados += [os.path.join(pasta, n) for n in sorted(os.listdir(pasta)) if n.lower().endswith(".pdf")]
            except OSError:
                pass
        return achados

    # ---------- impressão por IP: o programa vira uma "impressora de rede" ----------
    def _cfg_rede(self):
        c = ler_config().get("rede", {})
        return {"ativa": bool(c.get("ativa")), "aberta": bool(c.get("aberta")), "porta": int(c.get("porta") or PORTA_REDE)}

    def estado_rede(self):
        c = self._cfg_rede()
        return {**c, "ouvindo": self.rede["sock"] is not None, "ips": ips_locais(), "erro": self.rede["erro"], "ultimo": self.rede["ultimo"]}

    def configurar_rede(self, ativa, aberta):
        cfg = ler_config()
        cfg["rede"] = {**self._cfg_rede(), "ativa": bool(ativa), "aberta": bool(aberta)}
        gravar_config(cfg)
        self._rede_aplicar()
        return self.estado_rede()

    def _rede_parar(self):
        sk, self.rede["sock"] = self.rede["sock"], None
        if sk:
            for acao in (lambda: sk.shutdown(socket.SHUT_RDWR), sk.close):    # acorda o accept() e libera a porta
                try:
                    acao()
                except OSError:
                    pass

    def _rede_aplicar(self):
        self._rede_parar()
        self.rede["erro"] = ""
        c = self._cfg_rede()
        if not c["ativa"]:
            return
        erro = None
        for _ in range(8):                           # a porta antiga pode levar um instante para soltar
            try:
                sk = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sk.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sk.bind(("0.0.0.0" if c["aberta"] else "127.0.0.1", c["porta"]))
                sk.listen(8)
                sk.settimeout(1.0)
                erro = None
                break
            except OSError as e:
                erro = e
                try:
                    sk.close()
                except OSError:
                    pass
                time.sleep(0.4)
        if erro is not None:
            self.rede["erro"] = f"Não consegui abrir a porta {c['porta']}: {erro}. Outro programa pode estar usando."
            return
        self.rede["sock"] = sk
        threading.Thread(target=self._rede_loop, args=(sk,), daemon=True).start()

    def _rede_loop(self, sk):
        while self.rede["sock"] is sk and not self._parar.is_set():
            try:
                conn, addr = sk.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            threading.Thread(target=self._rede_atende, args=(conn, addr), daemon=True).start()

    def _rede_log(self, texto):
        try:
            with open(os.path.join(pasta_dados(), "rede.log"), "a", encoding="utf-8") as f:
                f.write(time.strftime("%Y-%m-%d %H:%M:%S") + "  " + texto + "\n")
        except OSError:
            pass

    def _rede_atende(self, conn, addr):
        """Recebe a impressão até a conexão fechar (ou ficar parada), acha o PDF e entrega à pasta de entrada."""
        conn.settimeout(5)
        dados, ultimo = bytearray(), time.time()
        try:
            while True:
                try:
                    pedaco = conn.recv(65536)
                except socket.timeout:
                    if time.time() - ultimo > 8:
                        break
                    continue
                if not pedaco:
                    break
                dados += pedaco
                ultimo = time.time()
                if len(dados) > 80_000_000:
                    break
        except OSError:
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass
        tipo, pdf = extrai_pdf(bytes(dados))
        origem = f"{addr[0]}:{addr[1]}"
        if not dados:
            return                                    # conexão vazia (teste de porta de outro programa)
        if pdf:
            nome = time.strftime("rede_%Y%m%d_%H%M%S") + f"_{uuid.uuid4().hex[:4]}.pdf"
            final = os.path.join(pasta_entrada(), nome)
            tmp = final + ".tmp"
            try:
                with open(tmp, "wb") as f:
                    f.write(pdf)
                os.replace(tmp, final)                # só vira .pdf quando está completo
            except OSError as e:
                self.rede["ultimo"] = f"Recebi {len(dados)} bytes de {origem}, mas não consegui gravar: {e}"
                self._rede_log(self.rede["ultimo"])
                return
            self.rede["ultimo"] = f"{time.strftime('%H:%M:%S')} · PDF recebido de {origem} ({len(pdf) // 1024} KB)"
        else:
            pasta = _sub("recebidos_rede")
            arq = os.path.join(pasta, time.strftime("rede_%Y%m%d_%H%M%S") + ".bin")
            try:
                with open(arq, "wb") as f:
                    f.write(bytes(dados))
            except OSError:
                pass
            self.rede["ultimo"] = (f"{time.strftime('%H:%M:%S')} · Recebi {len(dados) // 1024} KB de {origem}, mas não é PDF: {tipo}. "
                                   f"Escolha o driver \"Microsoft Print To PDF\" na impressora. (guardado em dados\\recebidos_rede)")
        self._rede_log(f"{origem} · {len(dados)} bytes · {tipo} · {self.rede['ultimo']}")

    # ---------- pastas do ERP (onde o sistema salva os PDFs): lê os NOVOS, não apaga nada ----------
    def adicionar_pasta_erp(self, caminho):
        caminho = os.path.normpath((caminho or "").strip().strip('"'))
        if not caminho or not os.path.isdir(caminho):
            return False, "Pasta não encontrada. Confira o caminho."
        if any(os.path.normcase(p["caminho"]) == os.path.normcase(caminho) for p in self.pastas_erp):
            return True, ""
        # PDFs que já estavam lá antes são ignorados: só os NOVOS (a partir de agora) viram romaneio
        self.pastas_erp.append({"caminho": caminho, "desde": time.time()})
        self._salvar_pastas()
        return True, ""

    def remover_pasta_erp(self, caminho):
        self.pastas_erp = [p for p in self.pastas_erp if os.path.normcase(p["caminho"]) != os.path.normcase(os.path.normpath(caminho or ""))]
        self._salvar_pastas()

    def _salvar_pastas(self):
        cfg = ler_config()
        cfg["pastas_erp"] = self.pastas_erp
        gravar_config(cfg)

    def _marca_visto_erp(self, chave):
        self._vistos_erp.add(chave)
        try:
            with open(os.path.join(pasta_dados(), "vistos_erp.json"), "w", encoding="utf-8") as f:
                json.dump(sorted(self._vistos_erp)[-3000:], f)
        except OSError:
            pass

    def _varrer_pasta_erp(self, p):
        caminho, desde = p["caminho"], p.get("desde", 0)
        try:
            nomes = os.listdir(caminho)
        except OSError:
            return
        for n in nomes:
            if not n.lower().endswith(".pdf"):
                continue
            arq = os.path.join(caminho, n)
            try:
                st = os.stat(arq)
            except OSError:
                continue
            if st.st_mtime < desde - 2:
                continue                                    # é de antes de configurar a pasta: ignora
            chave = f"{os.path.normcase(arq)}|{st.st_mtime_ns}|{st.st_size}"
            if chave in self._vistos_erp:
                continue
            self._capturar_um(arq, mover=False, vistos_chave=chave)

    def ha_novo(self):
        """Tem algum PDF esperando na pasta de entrada? (usado quando a impressão abre o programa: --romaneio)"""
        return any(self._chave(a) is not None for a in self._pdfs_na_entrada())

    # ---------- captura do romaneio impresso ----------
    def capturar(self):
        for arq in self._pdfs_na_entrada():
            self._capturar_um(arq)
        for p in list(self.pastas_erp):
            self._varrer_pasta_erp(p)

    def _capturar_um(self, arq, mover=True, vistos_chave=None):
        c = self._chave(arq)
        if c is None:
            self._visto.pop(arq, None)
            return
        if self._visto.get(arq) != c:       # espera o arquivo parar de mudar (2 leituras iguais)
            self._visto[arq] = c
            return
        nome = time.strftime("romaneio_%Y%m%d_%H%M%S.pdf")
        destino = os.path.join(pasta_romaneios(), nome)
        k = 1
        while os.path.exists(destino):      # dois romaneios no mesmo segundo
            k += 1
            destino = os.path.join(pasta_romaneios(), nome[:-4] + f"_{k}.pdf")
        try:
            if mover:
                # MOVE (não copia): a pasta da impressora precisa ficar livre, senão o Windows não consegue
                # gravar o próximo romaneio e a impressão fica presa em "Spooling"
                shutil.move(arq, destino)
            else:
                shutil.copy2(arq, destino)  # pasta do ERP: o original fica lá (o sistema é dono dele)
        except OSError:
            return                          # ainda em uso (Windows ou cópia em andamento); tenta na próxima
        self._visto.pop(arq, None)
        if vistos_chave:
            self._marca_visto_erp(vistos_chave)
        item = {"id": uuid.uuid4().hex[:12], "nome": os.path.basename(destino), "caminho": destino,
                "mtime": int(c[0] / 1e6), "capturado": int(time.time() * 1000)}   # ms: quando o PDF foi gravado / capturado
        with self.lock:
            self.fila.append(item)
            self.total_capturados += 1
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
                if u.path == "/api/config":
                    import impressora_virtual
                    cfg = ler_config()
                    return self._resp(200, {"pasta_romaneios": pasta_visivel(), "pasta_impressora": pasta_entrada(),
                                            "pastas_erp": [p["caminho"] for p in srv.pastas_erp],
                                            "inicio_automatico": impressora_virtual.inicio_automatico_ativo(),
                                            "ocr": __import__("ocr_romaneio").disponivel(),
                                            "rede": srv.estado_rede(),
                                            "exe": impressora_virtual.eh_exe()})
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
                if u.path in ("/api/impressora/instalar", "/api/impressora/remover", "/api/impressora/reparar",
                              "/api/impressora/instalar-ip", "/api/impressora/firewall"):
                    srv.acao_impressora(u.path.rsplit("/", 1)[-1])
                    return self._resp(200, {"ok": True})
                if u.path == "/api/rede":
                    q = parse_qs(u.query)
                    estado = srv.configurar_rede((q.get("ativa") or ["0"])[0] == "1", (q.get("aberta") or ["0"])[0] == "1")
                    return self._resp(200, {"ok": not estado["erro"], **estado})
                if u.path in ("/api/pasta-erp", "/api/pasta-erp/remover"):
                    n = min(int(self.headers.get("Content-Length") or 0), 2000)
                    caminho = self.rfile.read(n).decode("utf-8", "replace")
                    if u.path.endswith("/remover"):
                        srv.remover_pasta_erp(caminho)
                        return self._resp(200, {"ok": True})
                    ok, erro = srv.adicionar_pasta_erp(caminho)
                    return self._resp(200, {"ok": ok, "erro": erro})
                if u.path == "/api/inicio-automatico":
                    import impressora_virtual
                    ligar = (parse_qs(u.query).get("ligar") or ["0"])[0] == "1"
                    erro = impressora_virtual.define_inicio_automatico(ligar)
                    return self._resp(200, {"ok": not erro, "erro": erro,
                                            "inicio_automatico": impressora_virtual.inicio_automatico_ativo()})
                if u.path == "/api/impressora/comparar":
                    srv.comparar_impressoras()
                    return self._resp(200, {"ok": True})
                if u.path == "/api/impressora/testar":
                    srv.testar_impressora()
                    return self._resp(200, {"ok": True})
                if u.path == "/api/abrir-pasta":
                    qual = (parse_qs(u.query).get("qual") or [""])[0]
                    if hasattr(os, "startfile"):
                        os.startfile({"entrada": pasta_entrada, "romaneios": pasta_visivel}.get(qual, pasta_dados)())   # noqa: S606
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
                if u.path == "/api/ocr":
                    # PDF em IMAGEM (o sistema às vezes imprime o relatório como figura): lê por OCR e devolve os trechos com posição
                    import tempfile
                    import ocr_romaneio
                    n = int(self.headers.get("Content-Length") or 0)
                    if n <= 0 or n > 60_000_000:
                        return self._resp(200, {"ok": False, "erro": "Arquivo vazio ou grande demais."})
                    dados = self.rfile.read(n)
                    if not ocr_romaneio.disponivel():
                        return self._resp(200, {"ok": False, "erro": "A leitura de imagem não está instalada neste programa."})
                    fd, tmp = tempfile.mkstemp(suffix=".pdf", dir=pasta_dados())
                    try:
                        with os.fdopen(fd, "wb") as f:
                            f.write(dados)
                        r = ocr_romaneio.ocr_pdf(tmp)
                        return self._resp(200, {"ok": True, **r})
                    except Exception as e:                    # noqa: BLE001
                        return self._resp(200, {"ok": False, "erro": f"Não consegui ler a imagem: {e}"})
                    finally:
                        try:
                            os.remove(tmp)
                        except OSError:
                            pass
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
        self._rede_aplicar()
        try:
            import ocr_romaneio
            if ocr_romaneio.disponivel():
                threading.Thread(target=ocr_romaneio.aquecer, daemon=True).start()
        except Exception:                                      # noqa: BLE001
            pass

    def parar(self):
        self._parar.set()
        self._rede_parar()
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()        # libera a porta
