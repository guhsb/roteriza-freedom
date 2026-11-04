# Roteiriza Freedom (.exe)

Programa do Windows (Python + PyInstaller) que serve a página `web/index.html` localmente,
guarda os dados em `dados\` e cria a impressora virtual **Romaneio Freedom**.

- `servidor.py` – servidor local (127.0.0.1:47654), armazenamento em arquivos, vigia da impressora virtual
- `impressora_virtual.py` – instala/remove a impressora (Microsoft Print To PDF → porta-arquivo) e a tarefa agendada
- `app.py` – janela do programa e inicialização
- `web/index.html` – o Roteiriza (leitura do PDF do romaneio feita na página, com pdf.js embutido)

Para gerar o .exe: suba estes arquivos no GitHub; a Action *Gerar Roteiriza.exe* cria o `Roteiriza.zip`
(aba Actions > execução > Artifacts, ou Releases se criar uma tag `v1.0`).
