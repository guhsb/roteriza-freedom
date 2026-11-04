"""Roteiriza Freedom — programa do Windows.

Abre o Roteiriza no navegador (http://127.0.0.1:47654), guarda os dados na pasta 'dados'
ao lado do .exe e lê os romaneios impressos na impressora virtual "Romaneio Freedom".
"""
import os
import sys
import threading
import tkinter as tk
import webbrowser
from tkinter import messagebox, ttk

import impressora_virtual
import servidor

BG = "#F4F5F1"
NAVY = "#141C2B"


def main():
    modo_romaneio = "--romaneio" in sys.argv        # aberto pela tarefa agendada quando algo é impresso
    srv = servidor.Servidor()

    if modo_romaneio and not srv.ha_novo():
        return                                       # impressão de outra coisa (etiqueta, DANFE...): ignora
    try:
        srv.iniciar()
    except OSError:
        # já existe um Roteiriza aberto (ele mesmo vai pegar o PDF); se foi o usuário, só traz a página
        if not modo_romaneio:
            webbrowser.open(servidor.URL)
        return

    raiz = tk.Tk()
    raiz.title("Roteiriza Freedom")
    raiz.configure(bg=BG)
    raiz.geometry("440x330")
    raiz.resizable(False, False)

    status = tk.StringVar(value="Programa ativo.")
    imp = tk.StringVar(value="Impressora Romaneio Freedom: verificando...")

    tk.Label(raiz, text="Roteiriza Freedom", bg=BG, fg=NAVY, font=("Segoe UI", 16, "bold")).pack(pady=(16, 2))
    tk.Label(raiz, text=servidor.URL, bg=BG, fg="#6B7280", font=("Segoe UI", 9)).pack()
    tk.Label(raiz, textvariable=imp, bg=BG, fg=NAVY, font=("Segoe UI", 10)).pack(pady=(12, 0))
    tk.Label(raiz, textvariable=status, bg=BG, fg="#0E7C7B", font=("Segoe UI", 10, "bold"),
             wraplength=400).pack(pady=(4, 8))

    def atualiza_impressora():
        ok = impressora_virtual.impressora_instalada()
        raiz.after(0, lambda: imp.set("Impressora Romaneio Freedom: " + ("instalada ✓" if ok else "NÃO instalada")))

    def rodar_em_thread(fn, depois):
        def alvo():
            r = fn()
            raiz.after(0, lambda: depois(r))
        threading.Thread(target=alvo, daemon=True).start()

    def instalar():
        status.set("Aguardando a permissão de administrador...")
        def fim(r):
            status.set("")
            threading.Thread(target=atualiza_impressora, daemon=True).start()
            if "ERRO" in (r or ""):
                messagebox.showerror("Instalar impressora", r)
            else:
                messagebox.showinfo("Instalar impressora",
                                    'Pronto! No sistema, imprima o romaneio na impressora "Romaneio Freedom".')
        rodar_em_thread(impressora_virtual.instalar, fim)

    def remover():
        if not messagebox.askyesno("Remover impressora", 'Remover a impressora "Romaneio Freedom"?'):
            return
        status.set("Aguardando a permissão de administrador...")
        def fim(r):
            status.set("")
            threading.Thread(target=atualiza_impressora, daemon=True).start()
        rodar_em_thread(impressora_virtual.remover, fim)

    def fechar():
        if messagebox.askyesno("Sair", "Fechar o Roteiriza?\nA página aberta no navegador deixa de salvar. "
                                       "(Imprimir um romaneio abre o programa de novo sozinho.)"):
            srv.parar()
            raiz.destroy()

    ttk.Button(raiz, text="Abrir o Roteiriza", command=lambda: webbrowser.open(servidor.URL)).pack(pady=3, ipadx=20)
    ttk.Button(raiz, text="Instalar impressora Romaneio Freedom", command=instalar).pack(pady=3, ipadx=20)
    ttk.Button(raiz, text="Remover impressora", command=remover).pack(pady=3, ipadx=20)
    ttk.Button(raiz, text="Abrir pasta dos dados / backups",
               command=lambda: os.startfile(servidor.pasta_dados()) if hasattr(os, "startfile") else None).pack(pady=3, ipadx=20)

    def chegou(nome):
        raiz.after(0, lambda: status.set(f"Romaneio recebido: {nome}"))
    srv.ao_chegar = chegou

    raiz.protocol("WM_DELETE_WINDOW", fechar)
    threading.Thread(target=atualiza_impressora, daemon=True).start()
    if modo_romaneio:
        raiz.iconify()
    else:
        webbrowser.open(servidor.URL)
    raiz.mainloop()


if __name__ == "__main__":
    main()
