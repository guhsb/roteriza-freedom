"""Roteiriza Freedom — programa do Windows (um .exe só, igual ao de etiquetas).

Abre o Roteiriza na janela do próprio programa (motor do Windows embutido, sem Edge/Chrome, sem barra de
endereço), guarda os dados na pasta 'dados' ao lado do .exe e lê os romaneios impressos na impressora
virtual "Romaneio Freedom". Fechar a janela encerra o programa; imprimir um romaneio abre ele de novo.
"""
import os
import sys
import time

import janela
import servidor

ESPERA_INICIAL = float(os.environ.get("ROTEIRIZA_ESPERA_INICIAL", 45))   # (modo navegador) tempo para a janela abrir
FOLGA_JANELA_FECHADA = float(os.environ.get("ROTEIRIZA_FOLGA", 15))      # (modo navegador) janela fechou -> encerra
FOLGA_NAVEGADOR_PADRAO = 90


def webview2_instalado():
    """O Windows tem o motor de janela (WebView2)? Vem no Windows 11 e na maioria dos Windows 10."""
    try:
        import winreg
    except ImportError:
        return False
    guid = r"{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
    chaves = [(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\\" + guid),
              (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\EdgeUpdate\Clients\\" + guid),
              (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\EdgeUpdate\Clients\\" + guid)]
    for raiz, caminho in chaves:
        try:
            with winreg.OpenKey(raiz, caminho) as k:
                versao = winreg.QueryValueEx(k, "pv")[0]
                if versao and versao != "0.0.0.0":
                    return True
        except OSError:
            continue
    return False


def desbloquear_arquivos():
    """Arquivo baixado da internet vem "bloqueado" pelo Windows (marca Zone.Identifier) e o .NET se recusa
    a carregar DLL bloqueada ("Failed to resolve Python.Runtime..."). Tira a marca das DLLs do programa
    antes de abrir a janela — o mesmo que "Desbloquear" nas propriedades do arquivo. Só roda 1 vez por versão."""
    if sys.platform != "win32":
        return
    base = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.abspath(sys.executable))
    try:
        versao = str(int(os.path.getmtime(sys.executable)))
        marcador = os.path.join(servidor.pasta_dados(), ".desbloqueado_" + versao)
        if os.path.exists(marcador):
            return
        for raiz, _, arquivos in os.walk(os.path.dirname(os.path.abspath(sys.executable))):
            for nome in arquivos:
                if nome.lower().endswith((".dll", ".pyd", ".exe")):
                    try:
                        os.remove(os.path.join(raiz, nome) + ":Zone.Identifier")
                    except OSError:
                        pass                      # não estava bloqueado
        open(marcador, "w").close()
    except Exception:                             # noqa: BLE001  (nunca impede o programa de abrir)
        pass


def _gravar(nome, texto, modo="a"):
    """Grava em dados\\<nome>; se não der, na pasta temporária. Devolve o caminho (ou '')."""
    import tempfile
    pastas = []
    try:
        pastas.append(servidor.pasta_dados())
    except Exception:                             # noqa: BLE001
        pass
    pastas.append(tempfile.gettempdir())
    for pasta in pastas:
        try:
            caminho = os.path.join(pasta, nome)
            with open(caminho, modo, encoding="utf-8") as f:
                f.write(texto)
            return caminho
        except OSError:
            continue
    return ""


def passo(texto):
    """Anota por onde a abertura passou (dados\\inicio.log): se o programa parar, dá para ver onde."""
    _gravar("inicio.log", time.strftime("%H:%M:%S") + "  " + texto + "\n")


def registrar_erro(erro, arquivo="erro_janela.log"):
    import traceback
    return _gravar(arquivo, time.strftime("%Y-%m-%d %H:%M:%S") + "\n" + "".join(traceback.format_exception(erro)) + "\n")


def caixa_de_mensagem(texto, titulo="Roteiriza Freedom"):
    """O programa não tem janela de texto: sem isto, uma falha ao abrir seria invisível."""
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, texto, titulo, 0x10)
    except Exception:                             # noqa: BLE001
        pass


def usar_webview():
    if os.environ.get("ROTEIRIZA_SEM_WEBVIEW"):
        return False
    try:
        import webview  # noqa: F401
    except Exception:
        return False
    return webview2_instalado() if sys.platform == "win32" else True


def rodar_webview(srv, minimizado=False):
    """Janela própria do programa. Bloqueia até a janela ser fechada."""
    import webview

    j = webview.create_window("Roteiriza Freedom", servidor.URL + "?app=1", width=1366, height=860,
                              min_size=(1000, 640), maximized=not minimizado, minimized=minimizado, text_select=True)

    def trazer():
        try:
            j.restore()
            j.show()
            j.on_top = True          # força a janela para a frente (e solta em seguida)
            j.on_top = False
        except Exception:
            pass

    def salvar_como(nome, dados):
        pasta = os.path.join(os.path.expanduser("~"), "Downloads")
        tipo = webview.FileDialog.SAVE if hasattr(webview, "FileDialog") else webview.SAVE_DIALOG
        destino = j.create_file_dialog(tipo, directory=pasta if os.path.isdir(pasta) else "", save_filename=nome)
        if not destino:
            return None
        if isinstance(destino, (list, tuple)):
            destino = destino[0]
        with open(destino, "wb") as f:
            f.write(dados)
        return str(destino)

    srv.trazer = trazer
    srv.ao_chegar = lambda nome: trazer()           # chegou romaneio: a janela vem para a frente
    srv.salvar_como = salvar_como
    srv.sair_cb = lambda: j.destroy()
    base = os.environ.get("LocalAppData") or os.path.expanduser("~")
    storage = os.path.join(base, "RoteirizaFreedom", "webview")
    os.makedirs(storage, exist_ok=True)
    webview.start(private_mode=False, storage_path=storage)


def rodar_navegador(srv, minimizado=False):
    """Plano B (sem o motor do Windows): janela do Edge/Chrome em modo aplicativo."""
    srv.ao_chegar = srv.salvar_como = srv.sair_cb = None      # solta o que a janela embutida tinha configurado
    def abrir(minimizada=False):
        srv.janela = janela.abrir(servidor.URL, minimizada)
    srv.abrir_janela = abrir
    srv.trazer = abrir
    abrir(minimizado)
    inicio = time.time()
    while not srv.pedido_sair:
        time.sleep(1)
        if time.time() - inicio < ESPERA_INICIAL or srv.janela_viva():
            continue
        folga = FOLGA_JANELA_FECHADA if srv.janela is not None else FOLGA_NAVEGADOR_PADRAO
        if time.time() - srv.ultimo_poll > folga:
            break
    if srv.janela_viva():
        try:
            srv.janela.terminate()
        except OSError:
            pass


def main():
    _gravar("inicio.log", "", "w")
    passo("iniciando · " + " ".join(sys.argv[1:]) + " · " + sys.platform)
    modo_romaneio = "--romaneio" in sys.argv        # aberto pela tarefa agendada quando algo é impresso
    minimizado = "--minimizado" in sys.argv         # aberto junto com o Windows: fica minimizado até chegar um romaneio
    srv = servidor.Servidor()

    if modo_romaneio:
        if servidor.em_uso():
            return                                   # já aberto: ele mesmo pega o PDF
        fim = time.time() + 3                        # dá um instante para o Windows terminar de gravar o PDF
        while not srv.ha_novo() and time.time() < fim:
            time.sleep(0.3)
        if not srv.ha_novo():
            return                                   # impressão de outra coisa (etiqueta, DANFE...): ignora
    try:
        srv.iniciar()
    except OSError:
        passo("porta ocupada: já existe outro Roteiriza aberto")
        if not minimizado and not servidor.pedir_frente() and not modo_romaneio:   # já aberto: só traz a janela dele
            caixa_de_mensagem("Não consegui abrir: a porta %d do Roteiriza está ocupada.\n\n"
                              "Provavelmente o Roteiriza já está aberto, sem janela. Abra o Gerenciador de Tarefas "
                              "(Ctrl+Shift+Esc), feche o \"Roteiriza.exe\" e abra de novo." % servidor.PORTA)
        return
    passo("servidor no ar")

    srv.atualizar_impressora()
    try:
        if usar_webview():
            passo("abrindo a janela do programa (WebView2)")
            desbloquear_arquivos()
            try:
                rodar_webview(srv, minimizado)
            except Exception as erro:                 # noqa: BLE001
                registrar_erro(erro)                  # dados\erro_janela.log
                passo("a janela embutida falhou (erro_janela.log): usando o plano B")
                rodar_navegador(srv, minimizado)      # a janela embutida não abriu: usa o plano B, sem cair
        else:
            passo("abrindo no Edge/Chrome (modo aplicativo)")
            rodar_navegador(srv, minimizado)
    finally:
        srv.parar()


def principal():
    """Chama main() e, se QUALQUER coisa falhar, mostra a mensagem e grava dados\\erro_inicio.log."""
    try:                                              # erro "duro" (fecha sozinho) também deixa rastro
        import faulthandler
        faulthandler.enable(open(os.path.join(servidor.pasta_dados(), "falha_nativa.log"), "w"))
    except Exception:                                 # noqa: BLE001
        pass
    try:
        main()
    except SystemExit:
        raise
    except BaseException as erro:                     # noqa: BLE001
        caminho = registrar_erro(erro, "erro_inicio.log")
        passo("ERRO: " + repr(erro))
        caixa_de_mensagem("O Roteiriza não conseguiu abrir.\n\n%s: %s\n\nO detalhe foi gravado em:\n%s\n\nMe mande esse arquivo."
                          % (type(erro).__name__, erro, caminho))
        raise


if __name__ == "__main__":
    principal()
