"""Abre o Roteiriza numa janela própria (sem barra de endereço, sem abas), como um aplicativo.

Usa o Microsoft Edge (já vem no Windows) ou o Google Chrome no "modo aplicativo" (--app) com um
perfil só do Roteiriza. Se nenhum dos dois for encontrado, abre no navegador padrão.
"""
import os
import subprocess
import webbrowser


def acha_navegador():
    forcado = os.environ.get("ROTEIRIZA_NAVEGADOR")          # para testes ou para escolher outro navegador
    if forcado and os.path.exists(forcado):
        return forcado
    pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    la = os.environ.get("LocalAppData", "")
    candidatos = [
        os.path.join(pf86, r"Microsoft\Edge\Application\msedge.exe"),
        os.path.join(pf, r"Microsoft\Edge\Application\msedge.exe"),
        os.path.join(pf, r"Google\Chrome\Application\chrome.exe"),
        os.path.join(pf86, r"Google\Chrome\Application\chrome.exe"),
        os.path.join(la, r"Google\Chrome\Application\chrome.exe"),
    ]
    return next((c for c in candidatos if c and os.path.exists(c)), None)


def pasta_perfil():
    base = os.environ.get("LocalAppData") or os.path.expanduser("~")
    p = os.path.join(base, "RoteirizaFreedom", "perfil")
    os.makedirs(p, exist_ok=True)
    return p


def abrir(url):
    """Abre a janela do programa. Devolve o processo (ou None se caiu no navegador padrão)."""
    exe = acha_navegador()
    if not exe:
        webbrowser.open(url)
        return None
    args = [exe, f"--app={url}", f"--user-data-dir={pasta_perfil()}",
            "--no-first-run", "--no-default-browser-check", "--disable-sync",
            "--window-size=1366,860"]
    try:
        return subprocess.Popen(args)
    except OSError:
        webbrowser.open(url)
        return None
