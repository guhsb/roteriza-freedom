"""Leitura do romaneio quando o PDF vem como IMAGEM (sem texto), igual ao que o programa de etiquetas faz com o DANFE.

Transforma cada página em imagem (pypdfium2), lê o texto com OCR (rapidocr + onnxruntime) e devolve os trechos com
posição no MESMO formato que a página usa para PDFs com texto: {s, x, y}, em pontos do PDF, com y crescendo para CIMA.
Assim o mesmo leitor (romaneioParse) serve para os dois casos.
"""
import re
import threading
import time

DPI = 200
FATOR_TABELA = 0.18        # lacuna de palavra = fator x altura da linha. Calibrado por página: a tabela (página 1, letra maior)
FATOR_RECIBOS = 0.12       # prefere um limite maior; os recibos (letra menor) preferem um menor
_motor = None
_trava = threading.Lock()


def disponivel():
    try:
        import cv2, numpy, pypdfium2                 # noqa: F401, E401
        from rapidocr_onnxruntime import RapidOCR    # noqa: F401
        return True
    except Exception:                                # noqa: BLE001
        return False


def _engine():
    global _motor
    with _trava:
        if _motor is None:
            from rapidocr_onnxruntime import RapidOCR
            _motor = RapidOCR()
    return _motor


def aquecer():
    """Carrega o modelo em segundo plano na abertura, para a 1ª leitura não esperar."""
    try:
        _engine()
    except Exception:                                # noqa: BLE001
        pass


def _ajusta_texto(txt):
    """Conserta só pelo texto o que a leitura costuma colar: "Rotulo:valor" e "PostoAlmiranteFilial24"."""
    txt = re.sub(r"(?<=[A-Za-zÀ-ÿ]):(?=[A-Za-z0-9À-ÿ])", ": ", txt)          # Motorista:ADONIAS -> Motorista: ADONIAS
    def quebra(m):
        t = m.group(0)
        if len(t) < 12 or not (re.search(r"[a-zà-ÿ]", t) and re.search(r"[A-ZÀ-Ý]", t)):
            return t
        t = re.sub(r"(?<=[a-zà-ÿ])(?=[A-ZÀ-Ý])", " ", t)                      # PostoAlmirante -> Posto Almirante
        return re.sub(r"(?<=[A-Za-zÀ-ÿ])(?=\d)", " ", t)                       # Filial24 -> Filial 24
    return re.sub(r"\S+", quebra, txt)


def _caixa(box):
    xs = [p[0] for p in box]
    ys = [p[1] for p in box]
    return min(xs), min(ys), max(xs), max(ys)


def _reespaca(eng, img, x0, y0, x1, y1, fator=FATOR_RECIBOS):
    """A leitura costuma colar as palavras de texto em MAIÚSCULAS ("POSTOLINHAVERDE"). Separa pelas lacunas da imagem
    e lê cada palavra sozinha. Devolve o texto com espaços, ou None se não achou lacunas."""
    import cv2
    import numpy as np
    h, w = img.shape[:2]
    a, b, c, d = max(0, int(x0) - 2), max(0, int(y0) - 2), min(w, int(x1) + 3), min(h, int(y1) + 3)
    corte = img[b:d, a:c]
    if corte.size == 0:
        return None
    cinza = cv2.cvtColor(corte, cv2.COLOR_RGB2GRAY) if corte.ndim == 3 else corte
    _, mascara = cv2.threshold(cinza, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    coluna = mascara.sum(axis=0) > 0
    altura = d - b
    minimo = max(3, int(round(altura * fator)))       # lacuna de palavra (calibrado: ~3 a 5 px; entre letras fica abaixo disso)
    pedacos, ini, vazio = [], None, 0
    for i, tem in enumerate(coluna):
        if tem:
            if ini is None:
                ini = i
            vazio = 0
        elif ini is not None:
            vazio += 1
            if vazio >= minimo:
                pedacos.append((ini, i - vazio + 1))
                ini, vazio = None, 0
    if ini is not None:
        pedacos.append((ini, len(coluna)))
    if len(pedacos) < 2:
        return None
    palavras = []
    for p0, p1 in pedacos:
        seg = corte[:, max(0, p0 - 2):p1 + 2]
        if seg.shape[1] < 3:
            continue
        res, _ = eng(seg, use_det=False, use_cls=False, use_rec=True)
        palavras.append((res[0][0] if res else "").strip())
    palavras = [p for p in palavras if p]
    return " ".join(palavras) if len(palavras) >= 2 else None


def ocr_pdf(caminho, dpi=DPI):
    """Devolve {"paginas": [[{s,x,y}, ...], ...], "segundos": n}. Levanta exceção se a leitura por imagem não estiver instalada."""
    import numpy as np
    import pypdfium2 as pdfium
    t0 = time.time()
    eng = _engine()
    pdf = pdfium.PdfDocument(caminho)
    paginas = []
    escala = dpi / 72.0
    for i in range(len(pdf)):
        pg = pdf[i]
        largura_pt, altura_pt = pg.get_size()
        img = np.array(pg.render(scale=escala).to_pil().convert("RGB"))
        with _trava:
            achados, _ = eng(img)
        itens = []
        for box, txt, _conf in (achados or []):
            x0, y0, x1, y1 = _caixa(box)
            txt = (txt or "").strip()
            if not txt:
                continue
            txt = _ajusta_texto(txt)
            # texto COMPRIDO quase sem espaços (típico de MAIÚSCULAS coladas): separa as palavras pela imagem.
            # Linhas curtas ("Motorista: ADONIAS") ficam como estão: a separação pela imagem cortaria palavras no meio.
            if max((len(w) for w in txt.split()), default=0) >= 14:   # palavra de 14+ letras sem espaço = texto colado
                try:
                    with _trava:
                        melhor = _reespaca(eng, img, x0, y0, x1, y1, FATOR_TABELA if i == 0 else FATOR_RECIBOS)
                    if melhor:
                        txt = melhor
                except Exception:                    # noqa: BLE001
                    pass
            # posição em pontos do PDF; y = linha central, contado de baixo para cima (como o PDF)
            x_pt = x0 / escala
            y_pt = altura_pt - ((y0 + y1) / 2.0) / escala
            larg_pt = (x1 - x0) / escala
            # cada palavra vira um item, com o x estimado pela posição dos caracteres (as células da tabela colam às vezes)
            pos = 0
            total = max(len(txt), 1)
            for m in re.finditer(r"\S+", txt):
                itens.append({"s": m.group(0), "x": round(x_pt + larg_pt * (m.start() / total), 2), "y": round(y_pt, 2)})
        paginas.append({"w": round(largura_pt, 1), "h": round(altura_pt, 1), "itens": itens})
    return {"paginas": paginas, "segundos": round(time.time() - t0, 1)}
