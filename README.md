# Roteiriza Freedom (.exe)

Programa do Windows (Python + PyInstaller) que serve a página `web/index.html` localmente,
guarda os dados em `dados\` e cria a impressora virtual **Romaneio Freedom**.

- `servidor.py` – servidor local (127.0.0.1:47654), armazenamento em arquivos, vigia das pastas de entrada (impressora, pasta Romaneios, pastas do ERP)
- `impressora_virtual.py` – instala/remove a impressora (Microsoft Print To PDF → porta-arquivo) e a tarefa agendada
- `janela.py` – plano B: abre o Roteiriza no Edge/Chrome em modo aplicativo (se o WebView2 não existir)
- `ocr_romaneio.py` – leitura por imagem (rapidocr + onnxruntime + pypdfium2) para PDF sem texto; devolve os trechos com posição
- `app.py` – janela do programa (pywebview/WebView2), inicialização e ciclo de vida (fechar a janela encerra o programa)
- `web/index.html` – o Roteiriza (leitura do PDF do romaneio feita na página, com pdf.js embutido)

Para gerar o .exe: suba estes arquivos no GitHub; a Action *Gerar Roteiriza.exe* cria o `Roteiriza.zip`
(aba Actions > execução > Artifacts, ou Releases se criar uma tag `v1.0`).
